# Decisions

- Fake Claude by swapping `run.Claude` for a scripted class (replies consumed in order) — main() builds it by name, so no change to run.py was needed.
- Each scenario runs main() via `sys.argv` in its own `tempfile.mkdtemp()` repo — main() creates a timestamped branch and stops on blocked/out-of-retries, so scenarios can't share a run.
- Git author/committer set via env vars in the test — temp repos on CI may have no git identity, and commits would silently fail.
- Verifier guard compares `git status --porcelain` + `git diff` + `git diff --cached` before/after the verifier call and raises RuntimeError — the existing except path already marks [!] "error: …" and stops; `--disallowedTools Edit Write` doesn't stop Bash writes.
- Fake Claude script entries may be callables, run as a side effect before the next reply — lets a test simulate a verifier that edits files without changing FakeClaude's shape.
