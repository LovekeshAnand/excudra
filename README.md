# excudra

Free, local guardrails for Claude Code. One Python file, stdlib only. No account, no API calls, no telemetry.

| Hook | What it does |
|---|---|
| `PreToolUse` → `guard.py pre` | Blocks destructive commands (`rm -rf /`, force push, `git reset --hard`, `DROP TABLE`, `DELETE` without `WHERE`, `prisma migrate reset`, `terraform destroy`, ...) and installs of npm/PyPI packages that don't exist or are < 14 days old (slopsquatting) |
| `Stop` → `guard.py stop` | Blocks "done" when tests were deleted, got `.skip`/`.only`, or lost assertions vs the last commit |
| `SessionStart(compact)` → `guard.py recall` | After compaction, re-injects your own messages verbatim plus `git status`, so the summary can't drift from what you asked |

## Autonomous runs: `run.py`

Write a task list, walk away. Each task is built by a worker session, then checked by a separate read-only verifier session using real evidence (tests, builds, running the app). Failures go back to the worker with the verifier's feedback; passes are committed. Everything runs on a new `excudra/<timestamp>` branch with the guard hooks loaded.

```markdown
A habit tracker web app. Keep it simple, SQLite, no paid services.

- [ ] REST API for habits (create, list, check off today)
  done when: integration tests hit the running server and pass
- [ ] Web UI that uses the API
  done when: the page loads and a habit can be added and checked off
```

```
python run.py tasks.md --dir ./my-app --budget 20
```

- Re-run the same command to resume: `[x]` tasks are skipped.
- Stops and marks `[!]` when a task is blocked (credentials, paid services, destructive actions) or fails `--retries` times (default 3).
- Uses your Claude Code login. `--budget` caps the run using the cost Claude Code reports. On subscriptions, headless usage counts against your plan's allowance.
- Log of every session: `.excudra/log.jsonl`. Decisions the agents made: `DECISIONS.md`.

## Install

Requires Python 3.9+ and Claude Code with Git Bash on Windows.

```
/plugin install excudra --marketplace <owner>/<repo>
```

Or try it locally without installing: `claude --plugin-dir /path/to/excudra`

## Test

```
python test_guard.py
```

## Known limits

- Package checks fail open when offline.
- The test-tampering check blocks once per turn, then lets Claude stop after it has explained itself.
- Pattern matching, not a sandbox: a determined agent can still find a command it doesn't catch.
