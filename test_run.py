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


# --- end-to-end run.main() with a scripted fake Claude (no network, no claude CLI) ---
import os
import subprocess
import sys

for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}.items():
    os.environ.setdefault(k, v)


def drive(tasks_text, script, *extra):
    """Run run.main() in a fresh temp git repo. script: list of replies, consumed in order."""
    calls = []

    class FakeClaude:
        def __init__(self, cwd, budget, log, model, mode):
            self.left = budget

        def __call__(self, prompt, schema, effort, resume=None, read_only=False):
            if script and callable(script[0]):
                script.pop(0)(d)  # side effect before the reply, e.g. a verifier that edits files
            calls.append({"prompt": prompt, "verifier": schema is run.VERDICT_SCHEMA, "resume": resume, "read_only": read_only})
            assert script, f"unexpected extra claude call: {prompt[:80]}"
            return script.pop(0), f"s{len(calls)}"

    d = tempfile.mkdtemp()
    p = Path(d) / "tasks.md"
    p.write_text(tasks_text, encoding="utf-8")
    real, argv = run.Claude, sys.argv
    run.Claude, sys.argv = FakeClaude, ["run.py", str(p), "--dir", d, *extra]
    try:
        run.main()
    finally:
        run.Claude, sys.argv = real, argv
    assert not script, f"unused replies: {script}"
    log = subprocess.run(["git", "log", "--format=%s"], cwd=d, capture_output=True, text=True).stdout.split("\n")
    return p.read_text(encoding="utf-8"), calls, [l for l in log if l]


done = lambda s: {"status": "done", "summary": s}
verdict = lambda ok, fb="": {"pass": ok, "evidence": f"ev-{ok}", "feedback": fb}

# pass first try, fail-then-pass with feedback sent back, already-[x] skipped
text, calls, log = drive("Ctx.\n\n- [x] old task\n- [ ] first\n- [ ] second\n", [
    done("built first"), verdict(True),
    done("built second"), verdict(False, "test X fails"), done("fixed second"), verdict(True),
])
assert "- [x] old task\n- [x] first\n  → verified: ev-True\n- [x] second\n  → verified: ev-True\n" in text, text
assert log == ["excudra: second", "excudra: first", "excudra: start"], log
assert not any("old task" in c["prompt"] for c in calls)
assert [c["verifier"] for c in calls] == [False, True, False, True, False, True]
assert all(c["read_only"] == c["verifier"] for c in calls)
retry = calls[4]
assert "test X fails" in retry["prompt"] and retry["resume"] == "s3", retry  # resumes the same worker session
assert calls[2]["resume"] is None and "YOUR TASK: second" in calls[2]["prompt"]

# out of retries -> [!] with the last feedback, later tasks untouched, nothing committed
text, calls, log = drive("- [ ] hard\n- [ ] later\n", [
    done("try1"), verdict(False, "fb one"), done("try2"), verdict(False, "fb two"),
], "--retries", "2")
assert "- [!] hard\n  → failed 2 attempts: fb two\n- [ ] later\n" in text, text
assert "fb one" in calls[2]["prompt"] and len(calls) == 4
assert log == ["excudra: start"], log

# worker blocked -> [!], no verifier call, run stops
text, calls, log = drive("- [ ] needs key\n- [ ] after\n", [{"status": "blocked", "summary": "no API key"}])
assert "- [!] needs key\n  → blocked: no API key\n- [ ] after\n" in text, text
assert len(calls) == 1 and log == ["excudra: start"]

# verifier edits a tracked file -> [!] "verifier modified files", no commit, run stops
def tamper(d):
    with open(Path(d) / "tasks.md", "a", encoding="utf-8") as f:
        f.write("tampered\n")


text, calls, log = drive("- [ ] guarded\n- [ ] after\n", [done("built"), tamper, verdict(True)])
assert "- [!] guarded\n  → error: verifier modified files\n- [ ] after\n" in text, text
assert len(calls) == 2 and log == ["excudra: start"], log

# verifier creates an untracked file -> same
text, calls, log = drive("- [ ] guarded\n", [done("built"), lambda d: (Path(d) / "new.txt").write_text("x"), verdict(True)])
assert "- [!] guarded\n  → error: verifier modified files\n" in text, text
assert log == ["excudra: start"], log

print("all run main() checks passed")
