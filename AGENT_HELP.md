# TsuryPhone Integration — Agent Quick Guide

Welcome aboard! To keep this Home Assistant integration healthy and the firmware team unblocked, please stick to the checklist below whenever you touch the repo.

## Daily workflow

1. **Sync first**  
   - `git pull --rebase` before editing to avoid conflicts.

2. **Make focused changes**  
   - Keep each change scoped and document intent in commit messages.

3. **Run fast checks**  
   - Byte-compile or run the relevant tests/linters covering the files you touched.  
   - For Python modules: `python -m compileall custom_components/tsuryphone` is the minimum sanity check.

4. **Commit and push immediately**  
   - Stage the files you changed.  
   - `git commit` with a clear message.  
   - **Always `git push` right after every change** so the rest of the team (and future agents) stay in sync. No TODO pile-ups!

5. **Verify Home Assistant reload steps when needed**  
   - Most Python changes require a HA restart or integration reload. Call this out for the user.

6. **Close the loop**  
   - Summarize what changed, how you validated it, and note any follow-up work in your final response.

## Quality checklist before you wrap up

- [ ] Repo is clean (`git status` shows no pending changes).
- [ ] Tests or compile checks relevant to the edit have passed.
- [ ] Remote branch is up to date (confirm the latest commit exists on `origin/main`).
- [ ] User has clear next steps (restart HA, re-run a test, etc.).

Thanks for keeping the project tidy and collaborative! 🎧📞
