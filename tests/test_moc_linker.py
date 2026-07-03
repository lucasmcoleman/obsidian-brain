"""moc_linker managed-block idempotency helpers.

Regression coverage for the audit H-A finding: model-generated `desc` text flows
into `re.sub` as the *replacement* string, where backslash escapes and group
references (`\\1`, `\\g<..>`) are interpreted — so a Windows path or a stray
backslash in a note summary either crashes the nightly linker (invalid group
reference) or silently mangles the MOC file (\\n/\\t become real whitespace).

Also covers the H-B finding: the nightly write paths (write_mocs / tag_notes /
cross_link) must NOT rewrite a note when nothing but the managed-block timestamp
would change, so an idempotent night doesn't bump every note's mtime (which
defeats the ledger's recency filter and forces a full whole-vault re-embed).
"""
import os

import moc_linker as ml
from conftest import write_note

OLD_MTIME = 1_000_000  # a fixed past mtime; an unchanged file must keep it


def _managed(existing_body_desc):
    existing = f"# Work MOC\n\n{ml.MANAGED_BEGIN}\nOLD\n{ml.MANAGED_END}\n"
    block = ml.render_managed_block([{"title": "NoteX", "desc": existing_body_desc}])
    return ml.upsert_managed_block(existing, block, "Work")


def test_upsert_managed_block_group_ref_in_desc_does_not_crash():
    # A bare "\1" is an invalid group reference in a re.sub replacement template.
    out = _managed(r"see \1 for context")
    assert r"see \1 for context" in out  # preserved verbatim, no PatternError


def test_upsert_managed_block_windows_path_in_desc_does_not_crash():
    out = _managed(r"backup path C:\temp\1 here")
    assert r"C:\temp\1" in out


def test_upsert_managed_block_literal_escapes_not_expanded():
    # "\n"/"\t" in a desc must stay literal two-char sequences, not become
    # a real newline/tab injected into the MOC index.
    out = _managed(r"alpha\nbeta\tgamma")
    body = out.split(ml.MANAGED_BEGIN, 1)[1]
    assert r"alpha\nbeta\tgamma" in body
    assert "alpha\nbeta" not in body  # no injected real newline


def test_upsert_related_block_backslash_in_desc_does_not_crash():
    text = f"# Note\n\nbody\n\n{ml.RELATED_BEGIN}\nOLD\n{ml.RELATED_END}\n"
    block = ml.render_related_block([{"title": "Other", "desc": r"ref \1 path C:\x\1"}])
    out = ml.upsert_related_block(text, block)  # must not raise
    assert r"C:\x\1" in out


# ── H-B: idempotent nights must not rewrite unchanged notes (no mtime churn) ────
def test_tag_notes_skips_write_when_moc_already_present(vault):
    note = write_note(vault, "n.md", "# Note\n\nbody\n")
    results = [{"moc": "Work MOC", "abs": note, "rel": "n.md"}]
    ml.tag_notes(vault, results, apply=True)  # first run stamps moc: frontmatter
    first = note.read_text(encoding="utf-8")

    os.utime(note, (OLD_MTIME, OLD_MTIME))
    ml.tag_notes(vault, results, apply=True)  # second run is a no-op

    assert note.stat().st_mtime == OLD_MTIME  # not rewritten
    assert note.read_text(encoding="utf-8") == first


def test_write_mocs_skips_write_when_only_timestamp_would_change(vault):
    moc_dir = vault / ml.MOC_SUBDIR
    moc_dir.mkdir(parents=True)
    by_moc = {"Work MOC": [{"title": "Alpha", "desc": "a note about alpha"}]}
    ml.write_mocs(vault, by_moc, apply=True)
    path = moc_dir / "Work MOC.md"

    os.utime(path, (OLD_MTIME, OLD_MTIME))
    ml.write_mocs(vault, by_moc, apply=True)  # same entries → only the stamp differs

    assert path.stat().st_mtime == OLD_MTIME  # skipped despite the volatile timestamp


def test_cross_link_skips_write_when_neighbors_unchanged(vault, monkeypatch):
    a = write_note(vault, "a.md", "# Alpha\n\nalpha body here\n")
    b = write_note(vault, "b.md", "# Beta\n\nbeta body here\n")
    notes = [
        {"title": "Alpha", "rel": "a.md", "abs": a, "body": "alpha body here"},
        {"title": "Beta", "rel": "b.md", "abs": b, "body": "beta body here"},
    ]
    # Deterministic, distinct vectors so each note has the other as a neighbor.
    monkeypatch.setattr(ml, "embed_text",
                        lambda text, *a, **k: [float(len(text)), 1.0, 0.5])
    ml.cross_link(notes, "http://x", "m", 5, 10, 1, apply=True)

    for p in (a, b):
        os.utime(p, (OLD_MTIME, OLD_MTIME))
    ml.cross_link(notes, "http://x", "m", 5, 10, 1, apply=True)  # same neighbors

    assert a.stat().st_mtime == OLD_MTIME
    assert b.stat().st_mtime == OLD_MTIME


# ── M-I: extract_json must tolerate braces inside JSON string values ────────────
def test_extract_json_handles_balanced_brace_in_desc():
    obj = ml.extract_json('{"moc": "Code", "desc": "python dict {k: v} example"}')
    assert obj == {"moc": "Code", "desc": "python dict {k: v} example"}


def test_extract_json_handles_unbalanced_brace_inside_string():
    obj = ml.extract_json('{"moc": "Projects", "desc": "cost } overrun fixed"}')
    assert obj["moc"] == "Projects"


def test_extract_json_prefers_last_object_with_moc_key():
    text = ('Sure, the format is {"moc": "X", "desc": "y"}. Answer:\n'
            '{"moc": "Work", "desc": "the real one"}')
    assert ml.extract_json(text)["moc"] == "Work"


def test_extract_json_from_fenced_block_with_templater_braces():
    obj = ml.extract_json('```json\n{"moc": "Notes", "desc": "uses {{date}} templater"}\n```')
    assert obj == {"moc": "Notes", "desc": "uses {{date}} templater"}


def test_extract_json_returns_none_without_json():
    assert ml.extract_json("no json here at all") is None


# ── M-H: tag_notes must not corrupt frontmatter ────────────────────────────────
def _tag(vault, note, moc="Work MOC"):
    ml.tag_notes(vault, [{"moc": moc, "abs": note, "rel": note.name}], apply=True)
    return note.read_text(encoding="utf-8")


def test_tag_notes_replaces_list_form_moc_without_orphaning(vault):
    note = write_note(vault, "n.md",
                      '---\ntitle: Foo\nmoc:\n  - "[[Old MOC]]"\ntags: [a, b]\n---\nBody here\n')
    out = _tag(vault, note)
    assert 'moc: "[[Work MOC]]"' in out
    assert "[[Old MOC]]" not in out          # old list value removed, not orphaned
    assert '- "[[Old MOC]]"' not in out
    assert "title: Foo" in out and "tags: [a, b]" in out  # other keys preserved
    assert "Body here" in out


def test_tag_notes_does_not_hoist_body_when_note_opens_with_a_divider(vault):
    # Leading '---' used as a thematic break, with a later '---' divider too.
    note = write_note(vault, "n.md",
                      "---\n\n# Real Heading\n\nSome intro.\n\n---\n\nMore content.\n")
    out = _tag(vault, note)
    assert 'moc: "[[Work MOC]]"' in out
    # The heading/body must remain body, not be absorbed into frontmatter.
    assert "# Real Heading" in out
    assert "More content." in out
    # frontmatter is only the moc line we added, then the original text follows
    assert out.startswith('---\nmoc: "[[Work MOC]]"\n---\n')


def test_tag_notes_replaces_scalar_moc(vault):
    note = write_note(vault, "n.md", '---\nmoc: "[[Old]]"\ntitle: X\n---\nBody\n')
    out = _tag(vault, note)
    assert 'moc: "[[Work MOC]]"' in out
    assert "[[Old]]" not in out
    assert "title: X" in out


def test_tag_notes_adds_frontmatter_when_absent(vault):
    note = write_note(vault, "n.md", "# Just a note\n\nno frontmatter here\n")
    out = _tag(vault, note)
    assert out.startswith('---\nmoc: "[[Work MOC]]"\n---\n')
    assert "# Just a note" in out


def test_tag_notes_is_idempotent_after_first_apply(vault):
    note = write_note(vault, "n.md", '---\ntitle: X\n---\nBody\n')
    _tag(vault, note)
    os.utime(note, (OLD_MTIME, OLD_MTIME))
    _tag(vault, note)  # second run must be a no-op
    assert note.stat().st_mtime == OLD_MTIME
