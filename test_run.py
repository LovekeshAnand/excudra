"""Run: python test_run.py   (offline: checks task-file parsing and marking)"""
import tempfile
from pathlib import Path

import run

with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "tasks.md"
    p.write_text("Build a CLI calculator in Python.\n\n"
                 "- [ ] add command\n  done when: `calc add 2 3` prints 5\n"
                 "- [x] already done\n"
                 "- [ ] mul command\n", encoding="utf-8")
    context, tasks, _ = run.parse(p)
    assert context == "Build a CLI calculator in Python."
    assert [(t["mark"], t["title"]) for t in tasks] == [(" ", "add command"), ("x", "already done"), (" ", "mul command")]
    assert tasks[0]["detail"] == ["done when: `calc add 2 3` prints 5"]

    run.mark(p, tasks[0], "x", "verified: ran it,\n printed 5")
    run.mark(p, tasks[2], "!", "blocked: needs a key")      # stale line index must still hit the right task
    run.mark(p, tasks[0], "x", "verified again")             # note is replaced, not stacked
    text = p.read_text(encoding="utf-8")
    assert text.count("→") == 2, text
    assert "- [x] add command\n  done when: `calc add 2 3` prints 5\n  → verified again\n" in text, text
    assert "- [!] mul command\n  → blocked: needs a key\n" in text, text

print("all run checks passed")
