"""Task scanner/completer must be markdown-code-fence aware (audit finding M-G).

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
