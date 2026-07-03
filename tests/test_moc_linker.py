"""moc_linker managed-block idempotency helpers.

Regression coverage for the audit H-A finding: model-generated `desc` text flows
into `re.sub` as the *replacement* string, where backslash escapes and group
references (`\\1`, `\\g<..>`) are interpreted — so a Windows path or a stray
backslash in a note summary either crashes the nightly linker (invalid group
reference) or silently mangles the MOC file (\\n/\\t become real whitespace).
"""
import moc_linker as ml


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
