---
name: run
description: Turn a goal into a verified task list and run it autonomously with excudra's worker/verifier loop. Use when the user wants something built end to end without prompting each step.
argument-hint: <goal>
---

# excudra run

Turn the user's goal into a task list, get it approved once, then let `run.py` build it unattended.

## 1. Write the task list

Create `tasks.md` in the project directory (ask for the directory only if it isn't obvious):

- First, one short paragraph of project context: what it is, who it's for, stack and constraints. Pick sensible defaults yourself and state them; prefer free, local options.
- Then one `- [ ] task` per independently verifiable step, in build order.
- Every task gets an indented `done when:` line with an objective check: a command that passes, a request that returns something specific, a page that shows something. Never "looks good".
- Keep tasks small enough that one session can finish and test each.

## 2. Get approval

Show the task list and ask the user to approve or edit it. This is the only question. Do not start without approval.

## 3. Start the run

Run this in the background (`run_in_background`), using `python3` if `python` isn't available:

```
python "${CLAUDE_PLUGIN_ROOT}/run.py" <path to tasks.md> --dir <project dir>
```

Add `--effort`, `--budget` or `--retries` only if the user asked.

## 4. Report

When it finishes, read `tasks.md` and report briefly: tasks marked `[x]` with their verification notes, any `[!]` task with its reason and what the user needs to do, and the branch name. Re-running the same command resumes from the first unfinished task.
