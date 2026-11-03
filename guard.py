#!/usr/bin/env python3
"""guard: free, local, zero-dependency safety hooks for AI coding agents.

  python guard.py pre    PreToolUse hook: blocks destructive commands and
                         installs of packages that don't exist (slopsquatting).
  python guard.py stop   Stop hook: blocks "done" when tests were deleted,
                         skipped, or had assertions removed.
  python guard.py recall SessionStart(compact) hook: re-injects the user's own
                         messages verbatim after context compaction.

Reads the hook JSON on stdin. Exit 2 = block; stderr goes back to the agent.
"""
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DESTRUCTIVE = [
    (r"\brm\s+(-\w*[rf]\w*\s+)+(/|~|\$HOME|\*|\.)(\s|$)", "recursive delete of a broad path"),
    (r"\bgit\s+push\b.*(--force\b|-f\b)(?!-with-lease)", "force push"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard discards work"),
    (r"\bgit\s+clean\s+-\w*f", "git clean deletes untracked files"),
    (r"\b(drop\s+(table|database|schema)|truncate\s+table)\b", "drops or truncates data"),
    (r"\bdelete\s+from\s+\w+\s*(;|$)", "DELETE without WHERE"),
    (r"\b(prisma\s+migrate\s+reset|supabase\s+db\s+reset|rails\s+db:(drop|reset))\b", "resets a database"),
    (r"\bterraform\s+destroy\b|\bpulumi\s+destroy\b", "destroys infrastructure"),
    (r"\bkubectl\s+delete\b", "deletes cluster resources"),
    (r"\baws\s+s3\s+(rb|rm\b.*--recursive)", "deletes S3 data"),
    (r"\b(mkfs|format\s+[a-z]:)|\bdd\b.*\bof=/dev/", "wipes a disk"),
    (r"remove-item\b.*-recurse\b.*-force|\brd\s+/s\b|\bdel\s+/s\b", "recursive delete (Windows)"),
]

INSTALL = re.compile(
    r"\b(?:(npm|pnpm|bun)\s+(?:install|i|add)|(yarn)\s+add|(pip3?|uv\s+pip)\s+install|(uv|poetry)\s+add)\s+([^;&|]+)"
)
NEW_PACKAGE_DAYS = 14


def check_destructive(cmd):
    low = cmd.lower()
    return [why for pat, why in DESTRUCTIVE if re.search(pat, low)]


def package_names(cmd):
    """(registry, name) pairs from install commands; skips flags, files, URLs."""
    out = []
    for m in INSTALL.finditer(cmd):
        registry = "npm" if (m.group(1) or m.group(2)) else "pypi"
        for tok in m.group(5).split():
            scoped = registry == "npm" and tok.startswith("@") and tok.count("/") == 1
            if tok.startswith("-") or ("/" in tok and not scoped) or ":" in tok \
                    or tok.endswith((".txt", ".whl", ".tgz", ".tar.gz")):
                continue  # flags, paths, URLs, git refs, files
            if registry == "npm":
                name = re.sub(r"(?<=.)@.*$", "", tok)  # strip version, keep @scope
            else:
                name = re.split(r"[=<>!~\[;]", tok, 1)[0]
            if name:
                out.append((registry, name))
    return out


def package_age_days(registry, name):
    """Days since first publish, None if it doesn't exist. Raises on network errors."""
    url = f"https://registry.npmjs.org/{name.replace('/', '%2F')}" if registry == "npm" \
        else f"https://pypi.org/pypi/{name}/json"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    if registry == "npm":
        created = data.get("time", {}).get("created")
    else:
        uploads = [f["upload_time_iso_8601"] for files in data.get("releases", {}).values() for f in files]
        created = min(uploads) if uploads else None
    if not created:
        return 0
    first = datetime.fromisoformat(created.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - first).days


def pre(event):
    cmd = (event.get("tool_input") or {}).get("command")
    if not cmd:
        return 0
    problems = [f"Destructive: {why}." for why in check_destructive(cmd)]
    for registry, name in package_names(cmd):
        try:
            age = package_age_days(registry, name)
        except Exception:
            continue  # ponytail: fails open offline; flip to block if you need strict mode
        if age is None:
            problems.append(f"Package '{name}' does not exist on {registry}. Likely a hallucinated name.")
        elif age < NEW_PACKAGE_DAYS:
            problems.append(f"Package '{name}' was first published {age} days ago on {registry}. Possible slopsquat.")
    if problems:
        print("guard blocked this command:\n- " + "\n- ".join(problems) +
              "\nDo not retry or work around this. Ask the user to run it themselves if it is intended.",
              file=sys.stderr)
        return 2
    return 0


TEST_FILE = re.compile(r"(^|/)(tests?/|__tests__/|test_[^/]*$|[^/]*_test\.\w+$|[^/]*\.(test|spec)\.\w+$)")
SKIP = re.compile(r"\.skip\(|\bxit\(|\bxdescribe\(|\.only\(|@pytest\.mark\.(skip|xfail)|@unittest\.skip|\bt\.Skip\(|#\[ignore\]")
ASSERT = re.compile(r"\bassert|\bexpect\(|\bshould\b|\.to(Be|Equal)|\bt\.(Error|Fatal)")


def test_tampering(cwd):
    """Reasons the test suite looks weakened vs HEAD. Untracked new tests are fine."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout

    if not git("rev-parse", "--verify", "HEAD").strip():
        return []
    reasons = []
    for line in git("diff", "HEAD", "--name-status").splitlines():
        status, _, path = line.partition("\t")
        if status.startswith("D") and TEST_FILE.search(path):
            reasons.append(f"deleted test file {path}")
    removed_asserts = added_asserts = 0
    current = ""
    for line in git("diff", "HEAD", "-U0").splitlines():
        if line.startswith("--- "):
            continue
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else ""
            continue
        if not TEST_FILE.search(current):
            continue
        if line.startswith("+") and SKIP.search(line):
            reasons.append(f"added skip/only in {current}: {line[1:].strip()[:80]}")
        if ASSERT.search(line):
            if line.startswith("-"):
                removed_asserts += 1
            elif line.startswith("+"):
                added_asserts += 1
    if removed_asserts > added_asserts:
        reasons.append(f"net {removed_asserts - added_asserts} assertion line(s) removed from tests")
    return reasons


def stop(event):
    if event.get("stop_hook_active"):
        return 0  # already blocked once this turn; don't loop forever
    reasons = test_tampering(event.get("cwd") or ".")
    if reasons:
        print("guard: tests look weakened compared to the last commit:\n- " + "\n- ".join(reasons) +
              "\nIf this was not explicitly requested, restore the tests and fix the code instead. "
              "If it was intended, tell the user exactly which tests changed and why.", file=sys.stderr)
        return 2
    return 0


PROMPT_CHARS, RECALL_CHARS = 2000, 12000


def user_prompts(transcript_path):
    """The user's own messages from a Claude Code transcript, oldest first."""
    prompts = []
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") != "user" or e.get("isMeta") or e.get("isCompactSummary") or e.get("isSidechain"):
                continue
            c = (e.get("message") or {}).get("content")
            text = c if isinstance(c, str) else "\n".join(
                b.get("text", "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text")
            text = text.strip()
            if text and not text.startswith(("<command-", "<local-command", "<system-reminder")):
                prompts.append(text if len(text) <= PROMPT_CHARS else text[:PROMPT_CHARS] + " [...]")
    return prompts


def recall(event):
    """SessionStart(compact): re-inject the user's requests verbatim so a lossy summary can't drift them."""
    try:
        prompts = user_prompts(event["transcript_path"])
    except (KeyError, OSError):
        return 0
    if not prompts:
        return 0
    keep, size = [], len(prompts[0])  # always keep the first request (the original goal), then newest-first
    for p in reversed(prompts[1:]):
        if size + len(p) > RECALL_CHARS:
            break
        keep.insert(0, p)
        size += len(p)
    skipped = len(prompts) - 1 - len(keep)
    lines = ["Context was just compacted. The user's own messages, verbatim (summary may have lost detail):",
             f"[1] {prompts[0]}"]
    if skipped:
        lines.append(f"[... {skipped} older message(s) omitted ...]")
    lines += [f"[{len(prompts) - len(keep) + i + 1}] {p}" for i, p in enumerate(keep)]
    status = subprocess.run(["git", "status", "--short"], cwd=event.get("cwd") or ".",
                            capture_output=True, text=True).stdout.splitlines()
    if status:
        lines.append("Uncommitted changes:\n" + "\n".join(status[:40]) + ("\n..." if len(status) > 40 else ""))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                             "additionalContext": "\n\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    modes = {"pre": pre, "stop": stop, "recall": recall}
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in modes:
        sys.exit(__doc__)
    sys.exit(modes[mode](json.load(sys.stdin)))
