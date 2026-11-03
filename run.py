#!/usr/bin/env python3
"""excudra run: give it a task list, it builds until each task is independently verified.

  python run.py tasks.md [--dir PROJECT] [--budget 20] [--retries 3]

tasks.md:
    Free text at the top is the project goal/context, passed to every agent.
    - [ ] A task
      done when: how we know it works (optional, indented lines belong to the task)
    - [x] Finished tasks are skipped, so re-running resumes where it stopped.

Per task: a worker session builds it, a separate read-only verifier session checks it
with real evidence (tests, builds, running the app). Fail -> feedback goes back to the
worker, up to --retries. Pass -> git commit. Blocked or out of retries -> stop and report.
Runs on a new git branch; the guard hooks in this folder are loaded into every session.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK = re.compile(r"^- \[( |x|!)\] (.+)$")

WORKER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["status", "summary"],
    "properties": {"status": {"enum": ["done", "blocked"]}, "summary": {"type": "string"}},
}
VERDICT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["pass", "evidence", "feedback"],
    "properties": {"pass": {"type": "boolean"}, "evidence": {"type": "string"}, "feedback": {"type": "string"}},
}

WORKER = """You are working autonomously. No human will answer questions during this run.

PROJECT CONTEXT:
{context}

YOUR TASK: {task}
{done_when}
Rules:
- Make decisions yourself. When unsure about a library, API or approach, research it (web search/fetch, docs, the codebase) instead of guessing. Never invent package names or APIs.
- Append each significant decision with a one-line reason to DECISIONS.md.
- Write or update tests that prove the task works, and run them. Never weaken, skip or delete existing tests to make them pass.
- Keep changes scoped to this task. Follow the existing conventions of the codebase.
- Only report "blocked" if you truly cannot continue without a human: missing credentials, a paid service, or an irreversible/destructive action. Otherwise finish the task.
- An independent verifier will check your work against the task with real evidence. "Looks right" is not done.
Finish with status and a short summary of what you built and how to run/test it."""

RETRY = """The independent verifier rejected your work.

VERIFIER FEEDBACK:
{feedback}

Find the root cause and fix it. Do not game the checks. Then report status and summary again."""

VERIFIER = """You are an independent verifier. Another agent claims it finished a task. Assume nothing; check it yourself.

PROJECT CONTEXT:
{context}

TASK: {task}
{done_when}
The worker said: {summary}

Changes since the task started (git diff --stat {base}):
{diffstat}

Verify with objective evidence: run the tests, build/type-check/lint if the project has them, and run the app or hit it with real requests where that is how the task would be used. Read `git diff {base}` to check:
- the change actually implements the task, not a stub, hard-coded output or mock of the thing under test;
- tests exercise the real requirement and were not weakened, skipped or deleted.
Do not modify any files. Pass only if you saw it work. If something cannot be verified, fail and say exactly what is missing.
Return pass, the evidence you observed (commands and results), and concrete feedback for the worker if it fails."""


def parse(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    tasks, context = [], []
    for i, line in enumerate(lines):
        m = TASK.match(line)
        if m:
            tasks.append({"line": i, "mark": m.group(1), "title": m.group(2).strip(), "detail": []})
        elif tasks and line.startswith((" ", "\t")) and line.strip():
            tasks[-1]["detail"].append(line.strip())
        elif not tasks:
            context.append(line)
    return "\n".join(context).strip(), tasks, lines


def mark(path, task, mark_char, note):
    """Rewrite the task's checkbox and add/replace its result note in tasks.md."""
    _, current, lines = parse(path)
    i = next(t["line"] for t in current if t["title"] == task["title"])  # lines shift as notes are added
    lines[i] = f"- [{mark_char}] {task['title']}"
    j = i + 1
    while j < len(lines) and lines[j].startswith((" ", "\t")) and lines[j].strip():
        j += 1
    detail = [l for l in lines[i + 1:j] if not l.strip().startswith("→")]
    note = " ".join(note.split())[:400]
    lines[i + 1:j] = detail + [f"  → {note}"]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


class Claude:
    def __init__(self, cwd, budget, log, model, mode):
        self.cwd, self.left, self.log, self.model, self.mode = cwd, budget, log, model, mode
        self.exe = shutil.which("claude") or sys.exit("claude CLI not found on PATH")

    def __call__(self, prompt, schema, effort, resume=None, read_only=False):
        if self.left <= 0.05:
            raise RuntimeError("budget exhausted")
        cmd = [self.exe, "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
               "--permission-mode", self.mode, "--plugin-dir", str(HERE), "--effort", effort,
               "--max-budget-usd", f"{self.left:.2f}"]
        if self.model:
            cmd += ["--model", self.model]
        if resume:
            cmd += ["--resume", resume]
        if read_only:
            cmd += ["--disallowedTools", "Edit", "Write", "NotebookEdit"]
        p = subprocess.run(cmd, input=prompt, cwd=self.cwd, capture_output=True, text=True, encoding="utf-8")
        try:
            out = json.loads(p.stdout)
        except ValueError:
            raise RuntimeError(f"claude failed (exit {p.returncode}): {(p.stderr or p.stdout)[-500:]}")
        self.left -= out.get("total_cost_usd") or 0
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "session": out.get("session_id"), "cost": out.get("total_cost_usd"),
                                "read_only": read_only, "result": out.get("structured_output"),
                                "error": out.get("is_error") and out.get("result")}) + "\n")
        if out.get("is_error") or not out.get("structured_output"):
            raise RuntimeError(f"claude returned no result: {str(out.get('result'))[:500]}")
        return out["structured_output"], out.get("session_id")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def snapshot(cwd):
    """Working tree state: file status plus unstaged and staged content."""
    return git(cwd, "status", "--porcelain"), git(cwd, "diff"), git(cwd, "diff", "--cached")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tasks")
    ap.add_argument("--dir", default=".", help="project directory (default: current)")
    ap.add_argument("--budget", type=float, default=20.0, help="max USD for the whole run, as reported by claude")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--model", help="e.g. opus, or a full model id; default: your Claude Code default")
    ap.add_argument("--mode", default="auto", help="permission mode for workers (default: auto)")
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"],
                    help="reasoning effort for worker and verifier (default: medium)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252

    cwd, tasks_path = Path(a.dir).resolve(), Path(a.tasks).resolve()
    cwd.mkdir(parents=True, exist_ok=True)
    if not (cwd / ".git").exists():
        git(cwd, "init", "-q")
    if not git(cwd, "rev-parse", "--verify", "HEAD"):
        git(cwd, "commit", "-q", "--allow-empty", "-m", "excudra: start")
    git(cwd, "checkout", "-q", "-b", f"excudra/{time.strftime('%Y%m%d-%H%M%S')}")
    (cwd / ".excudra").mkdir(exist_ok=True)
    exclude = cwd / ".git" / "info" / "exclude"
    if ".excudra/" not in (exclude.read_text(encoding="utf-8") if exclude.exists() else ""):
        exclude.parent.mkdir(exist_ok=True)
        with open(exclude, "a", encoding="utf-8") as f:
            f.write("\n.excudra/\n")
    claude = Claude(cwd, a.budget, cwd / ".excudra" / "log.jsonl", a.model, a.mode)

    context, tasks, _ = parse(tasks_path)
    for task in tasks:
        if task["mark"] == "x":
            continue
        done_when = "\n".join(task["detail"])
        fill = {"context": context or "(none)", "task": task["title"],
                "done_when": f"Details / done when:\n{done_when}\n" if done_when else ""}
        base = git(cwd, "rev-parse", "HEAD")
        print(f"\n▶ {task['title']}", flush=True)
        try:
            prompt, session, verdict = WORKER.format(**fill), None, None
            for attempt in range(1, a.retries + 1):
                work, session = claude(prompt, WORKER_SCHEMA, a.effort, resume=session)
                print(f"  worker #{attempt}: {work['status']}: {work['summary'][:200]}", flush=True)
                if work["status"] == "blocked":
                    mark(tasks_path, task, "!", f"blocked: {work['summary']}")
                    return print("\n■ Stopped: needs you. See tasks.md.")
                git(cwd, "add", "-A")
                diffstat = git(cwd, "diff", "--cached", "--stat", base) or "(no changes)"
                before = snapshot(cwd)
                verdict, _ = claude(VERIFIER.format(**fill, summary=work["summary"], base=base, diffstat=diffstat),
                                    VERDICT_SCHEMA, a.effort, read_only=True)
                if snapshot(cwd) != before:  # read_only blocks Edit/Write, not Bash
                    raise RuntimeError("verifier modified files")
                print(f"  verifier: {'PASS' if verdict['pass'] else 'FAIL'}: {verdict['evidence'][:200]}", flush=True)
                if verdict["pass"]:
                    mark(tasks_path, task, "x", f"verified: {verdict['evidence']}")
                    git(cwd, "add", "-A")
                    git(cwd, "commit", "-q", "-m", f"excudra: {task['title']}")
                    break
                prompt = RETRY.format(feedback=verdict["feedback"])
            else:
                mark(tasks_path, task, "!", f"failed {a.retries} attempts: {verdict['feedback']}")
                return print("\n■ Stopped: out of retries. See tasks.md and .excudra/log.jsonl.")
        except RuntimeError as e:
            mark(tasks_path, task, "!", f"error: {e}")
            return print(f"\n■ Stopped: {e}")
    print(f"\n✔ All tasks verified. Branch: {git(cwd, 'branch', '--show-current')}. "
          f"Spent ≈ ${a.budget - claude.left:.2f}.")


if __name__ == "__main__":
    main()
