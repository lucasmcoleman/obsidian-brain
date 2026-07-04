"""truth_maintenance.py — provenance backfill (Layer 1), evidence-gated
contradiction filtering (Layer 2 safety gate), review-queue block (Layer 3).

The model-driven claim-extraction / NLI steps are exercised in the deployment via
the local chat model; here we pin the DETERMINISTIC, safety-critical pieces (the
gate and the frontmatter writer), the same way test_ledger pins the ledger gate.
"""
import truth_maintenance as tm
from conftest import write_note


# ── Layer 1: source-type inference + provenance backfill ────────────────────────
def test_infer_source_type_by_path():
    assert tm.infer_source_type("reMarkable/2026-06-30 mtg.md", "notes") == "ocr"
    assert tm.infer_source_type("Transcripts/call.md", "notes") == "transcript"
    assert tm.infer_source_type("Projects/Delta.md", "a short human note") == "manual"


def test_backfill_only_stamps_notes_missing_review_status():
    notes = [
        {"rel": "Projects/Delta.md", "abs": None, "fm": {}, "body": "hand note"},
        {"rel": "Transcripts/x.md", "abs": None, "fm": {}, "body": "raw transcript"},
        {"rel": "Projects/Done.md", "abs": None, "fm": {"review_status": "reviewed"}, "body": "x"},
    ]
    changes = tm.plan_provenance(notes, today="2026-07-04")
    by_rel = {c["rel"]: c for c in changes}
    assert "Projects/Done.md" not in by_rel  # already stamped → untouched
    assert by_rel["Projects/Delta.md"]["review_status"] == "reviewed"   # manual → trusted
    assert by_rel["Transcripts/x.md"]["review_status"] == "unreviewed"  # raw import → quarantined-ish
    assert by_rel["Transcripts/x.md"]["source_type"] == "transcript"


def test_set_frontmatter_keys_adds_and_preserves(tmp_path):
    p = write_note(tmp_path, "n.md", "---\ntitle: X\n---\nBody here\n")
    out = tm.set_frontmatter_keys(p.read_text(), {"review_status": "unreviewed", "source_type": "ocr"})
    assert "title: X" in out and "review_status: unreviewed" in out and "source_type: ocr" in out
    assert out.rstrip().endswith("Body here")


def test_set_frontmatter_keys_no_frontmatter_prepends():
    out = tm.set_frontmatter_keys("# Just a note\n\nbody\n", {"review_status": "reviewed"})
    assert out.startswith("---\n") and "review_status: reviewed" in out
    assert "# Just a note" in out


def test_set_frontmatter_keys_does_not_hoist_body_on_divider():
    text = "---\n\n# Heading\n\nintro\n\n---\n\nmore\n"
    out = tm.set_frontmatter_keys(text, {"review_status": "reviewed"})
    assert out.startswith('---\nreview_status: reviewed\n---\n')
    assert "# Heading" in out and "more" in out


# ── Layer 2 safety gate: both quotes must be real note content ──────────────────
def test_contradiction_gate_requires_both_quotes_present():
    notes = {
        "a.md": "The Delta deadline is June 30 per the sync.",
        "b.md": "Delta deadline moved to July 15 in today's meeting.",
    }
    conflicts = [
        {"a": "a.md", "b": "b.md", "evidence_a": "Delta deadline is June 30",
         "evidence_b": "Delta deadline moved to July 15", "confidence": 0.9},
        {"a": "a.md", "b": "b.md", "evidence_a": "Delta deadline is June 30",
         "evidence_b": "fabricated quote not in any note", "confidence": 0.9},  # hallucinated side
    ]
    kept = tm.filter_contradictions_by_evidence(conflicts, notes)
    assert len(kept) == 1
    assert kept[0]["evidence_b"].startswith("Delta deadline moved")


def test_contradiction_gate_drops_low_confidence():
    notes = {"a.md": "aaaa bbbb cccc dddd", "b.md": "aaaa bbbb cccc dddd"}
    conflicts = [{"a": "a.md", "b": "b.md", "evidence_a": "aaaa bbbb cccc",
                  "evidence_b": "aaaa bbbb cccc", "confidence": 0.2}]
    assert tm.filter_contradictions_by_evidence(conflicts, notes, min_confidence=0.5) == []


# ── Layer 3: review-queue managed block ────────────────────────────────────────
def test_render_and_upsert_queue_block_is_idempotent():
    conflicts = [{"a": "a.md", "b": "b.md", "evidence_a": "x deadline June 30",
                  "evidence_b": "x deadline July 15", "confidence": 0.8, "subject": "Delta deadline"}]
    block = tm.render_queue_block(conflicts, today="2026-07-04")
    assert tm.QUEUE_BEGIN in block and tm.QUEUE_END in block
    assert "[[a.md]]" in block and "[[b.md]]" in block and "- [ ]" in block

    existing = f"# Truth Review Queue\n\n{tm.QUEUE_BEGIN}\nOLD\n{tm.QUEUE_END}\n"
    once = tm.upsert_queue_block(existing, block)
    twice = tm.upsert_queue_block(once, block)
    assert once.count(tm.QUEUE_BEGIN) == 1
    assert once == twice  # idempotent
