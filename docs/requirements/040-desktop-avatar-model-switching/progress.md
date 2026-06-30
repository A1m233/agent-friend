# 040 - Desktop Avatar Model Switching - Progress

## Overall Status
DONE

## Task List

- [x] Implement Tauri-side installation of built-in avatar models into the user model directory and installed resource query command.
- [x] Add frontend installed catalog merge / resolve capability and focused tests.
- [x] Add the Settings "Pet" section and current avatar model selector.
- [x] Make the pet window consume `avatarModelId` + installed catalog, removing the formal-path dependency on dev-only local selection state.
- [x] Add and run key Rust / frontend tests.
- [x] Run desktop smoke validation against the real Tauri windows.

## Implementation Log

| Date | Completed | Notes |
|------|-----------|-------|
| 2026-06-30 | Created requirement/design/progress docs and entered Phase 3 | User confirmed implementation start. |
| 2026-06-30 | Added Tauri built-in resource bundling, startup install/repair, manifest writing, and `get_installed_avatar_model_resources` | Built-ins are copied to `<user_data_dir>/avatar-models/builtin` before windows load. |
| 2026-06-30 | Added installed avatar catalog resolver and settings UI | Settings now has a "Pet" section with Hiyori and built-in Ark Spine models. |
| 2026-06-30 | Updated pet runtime loading | Pet reads `avatarModelId`, resolves installed local resource URLs, and switches Live2D / Spine by backend. |
| 2026-06-30 | Fixed installed Live2D relative asset loading | Live2D model internals now redirect relative files from the installed user directory. |
| 2026-06-30 | Fixed installed Spine 3.8 loading | Ark atlas textures are loaded from installed file URLs and parsed through the Spine 3.8 runtime. |
| 2026-06-30 | Ran automated and desktop validation | `cargo test`, `pnpm --dir frontend typecheck`, frontend tests, and real Tauri CDP smoke passed. |
| 2026-06-30 | Improved Spine pet rendering quality | Spine textures now use linear filtering and mipmaps to reduce visible jagged edges without changing model size. |
| 2026-06-30 | Split ActionBar UI anchoring from hit bounds | ActionBar positioning now uses a visual UI anchor while passthrough / hit behavior keeps the full avatar bounds. |
| 2026-06-30 | Preserved pet placement when switching models | Runtime switch now keeps the current avatar placement point instead of snapping Live2D / Spine models back to their initial placement. |
| 2026-06-30 | Recorded Ark perceived size variance as a deferred issue | See `docs/issues/033-ark-model-perceived-size-variance/`; this is a visual tuning follow-up and not a 040 blocker. |
| 2026-06-30 | Completed the pre-merge quality gate | `.\scripts\check\run.ps1` passed after isolating the existing voice credential unit test from local developer credentials. |

## Validation

- `cargo test` in `frontend/src-tauri`: passed, 45 tests.
- `pnpm --dir frontend typecheck`: passed.
- `pnpm --dir frontend test -- src/avatar-models/installed.test.ts src/avatar-models/catalog.test.ts src/pages/pet/usePixiAvatarSlot.test.tsx src/pet/anchorDiagnostics.test.ts src/pet/avatarRuntime/spine38/spine38Loader.test.ts`: passed, 42 files / 214 tests.
- Real desktop startup via `scripts/desktop-visual/run.ps1 start --no-voice`: built-in assets installed or detected under `C:\Users\example-user\AppData\Roaming\agent-friend\avatar-models\builtin`.
- Real Settings window via CDP: "Pet" section rendered, selector showed Hiyori plus built-in Ark models, and selecting Ark Amiya persisted `avatarModelId`.
- Real Pet window via CDP: Ark Amiya Spine model loaded from installed assets and rendered non-transparent pixels in `C:\Users\example-user\AppData\Local\Temp\agent-friend-pet-cdp.png`.
- Real Pet window via CDP: ActionBar center aligned to the model UI anchor, and Ark -> Hiyori -> Ark switching preserved the current avatar placement point with 0px anchor drift in the sampled window.
- Real Pet window via CDP: Ark model size sampling confirmed current height normalization works, while perceived visual mass still varies by silhouette; deferred as issue 033.
- `.\scripts\check\run.ps1`: passed. Includes ruff lint/format, mypy over 244 source files, backend pytest with 661 passed tests, and frontend eslint / guards / typecheck / Vitest with 42 passed files and 214 passed tests.

## Residual Notes

- Voice prewarm requests fail in `--no-voice` desktop smoke because the voice bridge is intentionally not running; this is unrelated to avatar model switching.
- Ark model perceived visual size still varies by silhouette; tracked as issue 033 and deferred out of requirement 040.
