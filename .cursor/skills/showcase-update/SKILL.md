---
name: showcase-update
description: Update the public agent-friend showcase repository from the private source repo. Use when the user asks to update, regenerate, sync, verify, run, commit, or publish the sanitized showcase snapshot or the agent-friend-showcase checkout.
---

# Showcase Update

## Scope

Use this skill only for the project-level public showcase flow. The private source repo remains the source of truth; the showcase repo is a generated, sanitized git checkout.

Do not copy secrets, `.env`, user data, generated caches, or local runtime state into the showcase repo. Do not start flows that may call real LLM/vendor APIs until the user explicitly authorizes credentials and approximate usage.

## Workflow

1. Check both worktrees:

```powershell
git status --short --branch
git -C ..\agent-friend-showcase status --short --branch
```

2. Generate and sync the sanitized snapshot through the project script:

```powershell
.\scripts\showcase-snapshot\run.ps1 --sync-checkout ..\agent-friend-showcase
```

Use the mac/linux wrapper when not on Windows:

```bash
./scripts/showcase-snapshot/run.sh --sync-checkout ../agent-friend-showcase
```

3. Require a clean privacy scan before proceeding. The script must report `scan errors: 0`; inspect `..\agent-friend-showcase\SHOWCASE-SCAN-REPORT.md` if it does not.

4. Initialize or verify the showcase checkout:

```powershell
git -C ..\agent-friend-showcase status --short --branch
Push-Location ..\agent-friend-showcase
.\scripts\setup\run.ps1
Pop-Location
```

Run setup from the showcase checkout, not the private repo, when validating showcase usability.

5. For user review, start the Tauri desktop frontend first. Use this before the full bridge/dev flow when you only need to show the local showcase UI without exercising LLM/vendor credentials:

```powershell
Push-Location ..\agent-friend-showcase
.\scripts\frontend\dev.ps1
```

This opens the real desktop windows. Do not replace it with `frontend\web.ps1` or `dev\run.ps1 --web` unless the user explicitly asks for browser/web mode.

6. Start the full desktop bridge/dev flow only after the user explicitly approves using credentials:

```powershell
Push-Location ..\agent-friend-showcase
.\scripts\dev\run.ps1
```

If using legacy `.env`, get explicit approval first because it can trigger real LLM/API calls.

7. Commit and push only after the user confirms the local showcase result. Before committing, load and follow `gen-commit-message`.

## Safety Notes

- The sync script preserves `.git`, `.venv`, `.tmp`, `.env`, `.agent-friend-data`, `.showcase-run`, and `node_modules`.
- Do not run raw directory deletion against the showcase checkout.
- On Windows, adapter symlinks may be represented as text placeholders; let the sync script handle them.
- Treat scan errors as blockers. Warnings are inventory items and may be acceptable after inspection.
