# TsuryPhone Agent Guide

Welcome! Follow this playbook whenever you touch the integration so the workflow stays fast and predictable.

## Daily workflow
- **Sync first**: `git pull --rebase` before making changes to stay aligned with `origin/main`.
- **Make focused edits**: keep changes scoped and use clear, descriptive commit messages.
- **Run the quick check**: at minimum, byte-compile updated Python modules with `python -m compileall custom_components/tsuryphone` (run targeted tests when they exist).
- **Use the repo's Python environment**: activate the maintained Python runtime whenever you run compile commands, tests, or scripts so everything executes under the supported Home Assistant version.
- **Commit & push every task**: once a task is finished, stage, commit, and immediately `git push` to `main`. No batching or local todo piles.

## Technical guardrails
- Target the latest Home Assistant release. Do **not** add backward-compatibility shims or legacy code paths unless the user explicitly asks for them.
- Prefer explicit, modern APIs over defensive fallbacks; assume the environment meets the current minimum requirements.
- Leave the repository clean: confirm `git status` is empty before closing out.

## Wrap-up checklist
- [ ] Relevant compile/test checks are green.
- [ ] Work is committed and pushed to `origin/main`.
- [ ] Final response summarizes changes, verification, and next steps for the user.
