"""Run: python test_guard.py   (package checks hit npm/PyPI; they skip if offline)"""
import os
import subprocess
import tempfile

import guard

# destructive commands
for bad in ["rm -rf /", "rm -rf ~", "git push --force origin main", "git reset --hard HEAD~3",
            "psql -c 'DROP TABLE users'", "npx prisma migrate reset", "terraform destroy -auto-approve",
            "Remove-Item -Recurse -Force C:\\proj", "DELETE FROM users;"]:
    assert guard.check_destructive(bad), bad
for ok in ["rm -rf node_modules", "git push --force-with-lease", "git status",
           "DELETE FROM users WHERE id = 3;", "npm test"]:
    assert not guard.check_destructive(ok), ok

# install parsing
assert guard.package_names("npm i -D react@18.3.1 @types/node ./local") == [("npm", "react"), ("npm", "@types/node")]
assert guard.package_names("pip install requests==2.32 -r reqs.txt git+https://x/y") == [("pypi", "requests")]
assert guard.package_names("cd app && uv add httpx") == [("pypi", "httpx")]

# registry lookups
try:
    assert guard.package_age_days("npm", "react") > 1000
    assert guard.package_age_days("npm", "zz-surely-not-a-real-pkg-8f3a91") is None
    assert guard.package_age_days("pypi", "requests") > 1000
    assert guard.package_age_days("pypi", "zz-surely-not-a-real-pkg-8f3a91") is None
except OSError:
    print("offline: skipped registry checks")

# test tampering
with tempfile.TemporaryDirectory() as d:
    def git(*a):
        subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t"); git("config", "user.name", "t")
    os.makedirs(f"{d}/tests")
    open(f"{d}/tests/test_math.py", "w").write("def test_add():\n    assert add(1, 2) == 3\n")
    open(f"{d}/app.py", "w").write("x = 1\n")
    git("add", "."); git("commit", "-qm", "init")

    assert guard.test_tampering(d) == []
    open(f"{d}/app.py", "w").write("x = 2\n")
    assert guard.test_tampering(d) == []                       # code-only change is fine
    open(f"{d}/tests/test_math.py", "w").write("import pytest\n@pytest.mark.skip\ndef test_add():\n    pass\n")
    r = guard.test_tampering(d)
    assert any("skip" in x for x in r) and any("assertion" in x for x in r), r
    os.remove(f"{d}/tests/test_math.py")
    assert any("deleted" in x for x in guard.test_tampering(d))
    assert guard.stop({"cwd": d, "stop_hook_active": True}) == 0  # no infinite loop

# recall after compaction
import io, json, contextlib
with tempfile.TemporaryDirectory() as d:
    t = f"{d}/t.jsonl"
    lines = [
        {"type": "user", "message": {"content": "build a habit tracker"}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        {"type": "user", "isMeta": True, "message": {"content": [{"type": "text", "text": "skill dump"}]}},
        {"type": "user", "message": {"content": "<command-name>/clear</command-name>"}},
        {"type": "assistant", "message": {"content": "ok"}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "use postgres, not sqlite"}]}},
    ]
    open(t, "w", encoding="utf-8").write("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    assert guard.user_prompts(t) == ["build a habit tracker", "use postgres, not sqlite"]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert guard.recall({"transcript_path": t, "cwd": d}) == 0
    ctx = json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "[1] build a habit tracker" in ctx and "[2] use postgres, not sqlite" in ctx, ctx
    assert guard.recall({"transcript_path": f"{d}/missing.jsonl"}) == 0  # never crash the session

print("all guard checks passed")
