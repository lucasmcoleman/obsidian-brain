"""Task scanner/completer must be markdown-code-fence aware .

brain_tasks is the exhaustive/precise path (its whole value over semantic search
is exactness), and brain_complete_task edits note content in place. A checkbox
that appears inside a ``` fenced code block is documentation/example, not a task:
it must neither be listed as an open task nor be completable (which would rewrite
a line inside the code sample).
"""
import tasks
from conftest import write_note

FENCED_NOTE = """# Syntax guide

Here is how an Obsidian task looks:

```markdown
- [ ] this is only a documentation EXAMPLE, not a real task
```

And a real one:

- [ ] buy milk
"""

TILDE_NOTE = """# Tilde fence

~~~
- [ ] fenced with tildes, not a task
~~~

- [ ] real open task here
"""


def test_scan_skips_tasks_inside_fenced_code(vault):
    write_note(vault, "guide.md", FENCED_NOTE)
    open_tasks = tasks.scan_tasks("open", vault_path=str(vault))
    texts = [t["text"] for t in open_tasks]
    assert texts == ["buy milk"]  # the fenced EXAMPLE line is not a task


def test_scan_skips_tilde_fenced_code(vault):
    write_note(vault, "tilde.md", TILDE_NOTE)
    open_tasks = tasks.scan_tasks("open", vault_path=str(vault))
    texts = [t["text"] for t in open_tasks]
    assert texts == ["real open task here"]


def test_scan_skips_trash_and_livesync_logs(vault):
    # A deleted note in Obsidian's .trash/ still has checkboxes; counting them
    # resurfaces phantom open tasks from notes the user already removed.
    write_note(vault, "live.md", "- [ ] real live task\n")
    write_note(vault, ".trash/old copy 37afcd4b.md", "- [ ] stale trashed task\n")
    write_note(vault, "livesync_log_2026-06-16.md", "- [ ] not a task, a log line\n")
    open_tasks = tasks.scan_tasks("open", vault_path=str(vault))
    texts = [t["text"] for t in open_tasks]
    assert texts == ["real live task"]


def test_scan_skips_brain_dir_at_any_depth_including_entities(vault):
    # Unlike indexer.scan_vault (which carves out _brain/entities/ so curated
    # entity notes stay indexable, and only anchors the _brain exclusion at the
    # vault top level), the task scanner treats ANY path containing a `_brain`
    # component as non-knowledge, at any depth, with no entities carve-out.
    # This pins that current (drifted) behavior exactly.
    write_note(vault, "live.md", "- [ ] real live task\n")
    write_note(vault, "_brain/entities/foo.md", "- [ ] entity note task\n")
    write_note(vault, "Projects/_brain/x.md", "- [ ] nested brain-dir task\n")
    open_tasks = tasks.scan_tasks("open", vault_path=str(vault))
    texts = [t["text"] for t in open_tasks]
    assert texts == ["real live task"]


def test_complete_refuses_to_edit_a_checkbox_inside_a_code_fence(vault):
    p = write_note(vault, "guide.md", FENCED_NOTE)
    before = p.read_text(encoding="utf-8")

    result = tasks.complete_task("guide.md", "documentation EXAMPLE", vault_path=str(vault))

    assert result["status"] == "error"  # no real open task matches
    assert p.read_text(encoding="utf-8") == before  # code sample untouched


def test_complete_still_works_on_a_real_task_when_a_fenced_lookalike_exists(vault):
    p = write_note(vault, "guide.md", FENCED_NOTE)

    result = tasks.complete_task("guide.md", "buy milk", vault_path=str(vault))

    assert result["status"] == "completed"
    body = p.read_text(encoding="utf-8")
    assert "- [x] buy milk ✅" in body
    # The fenced example checkbox stays open.
    assert "- [ ] this is only a documentation EXAMPLE" in body
