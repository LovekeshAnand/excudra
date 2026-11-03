# Decisions

- Fake Claude by swapping `run.Claude` for a scripted class (replies consumed in order) — main() builds it by name, so no change to run.py was needed.
- Each scenario runs main() via `sys.argv` in its own `tempfile.mkdtemp()` repo — main() creates a timestamped branch and stops on blocked/out-of-retries, so scenarios can't share a run.
- Git author/committer set via env vars in the test — temp repos on CI may have no git identity, and commits would silently fail.
