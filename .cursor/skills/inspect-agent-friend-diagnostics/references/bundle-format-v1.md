# Agent-Friend Diagnostic Bundle Format v1

Use this reference to interpret bundles whose `manifest.json` has `formatVersion: 1`. The recorded build commit remains authoritative when an older or branch-specific producer differs from the current source. The current producer's structural source of truth is `frontend/src-tauri/src/diagnostics/bundle-contract-v1.json`; this reference explains evidence semantics rather than duplicating that machine contract.

## Producer Flow

The settings feedback panel invokes the Tauri `generate_diagnostic_bundle` command with an optional problem description, selected session ids, locale, timezone, and an attachment-originals flag. Rust then:

1. snapshots application, environment, runtime-service, and runtime-config state;
2. fetches each selected session from the local bridge and removes `active_events`;
3. derives attachment candidates from selected-session `user_message` events;
4. validates attachment metadata, blob size, and SHA-256 before optionally including originals;
5. collects allowlisted logs from `paths::log_dir()`;
6. snapshots the bounded WebView2 process/lifecycle buffer, runtime identity, and Windows graphics-adapter inventory;
7. describes the latest one-shot render capture and, only when the user confirms inclusion, verifies and adds its WebView2/internal and full-desktop PNGs;
8. snapshots shell, active receipt, payload hashes, observed runtime readiness, Job Object assignment, legacy layout, and incomplete installer transactions;
9. snapshots the current desktop instance domain and its safely attributable owner evidence;
10. flushes the independent Windows Shell event journal and adds its bounded history plus a current snapshot;
11. redacts managed credentials and replaces the user home path;
12. validates the manifest and every archive entry against the producer-owned bundle contract;
13. writes a `.zip.part` file in Downloads and atomically renames it after closing and syncing the ZIP.

The generator does not upload the bundle or intentionally call an external provider.

## Top-Level Structure

| Entry | Meaning | Important limits |
| --- | --- | --- |
| `manifest.json` | Collection outcome, failures, exclusions, log coverage, session ids, attachment summary | Read first |
| `problem.txt` | Optional user-written symptom description | Maximum 20,000 characters; not general-PII filtered |
| `diagnostics/application.json` | Product/version/build identity and generation time | `packageBuild` may be absent on older/dev builds |
| `diagnostics/environment.json` | OS, architecture, locale, timezone | No hostname or full environment dump |
| `diagnostics/runtime-services.json` | Bridge/voice lifecycle snapshots | Snapshot at bundle generation time, not the whole incident history |
| `diagnostics/runtime-config.json` | Config item name, group, configured/source/effect/error | Contains configuration presence, never intended credential values |
| `diagnostics/desktop-instance.json` | Resolved runtime kind, shared instance domain, process session, and current owner evidence | Present when `desktop-instance-governance-v1` is declared; paths are home-redacted and no command-line or environment content is included |
| `install/integrity.json` | Embedded shell identity, active receipt, verified payload/runtime hashes, observed readiness identity, Job Object state, and installer residue | Present when `install-integrity-v1` is declared; uses relative paths and stable error codes, never credential values |
| `diagnostics/webview2.json` | WebView2 runtime version, recent crash-report metadata, handler registration coverage, graphics inventory, and a bounded process-failure event buffer | `supported: false` on non-Windows; raw dump files are never included and no automatic recovery action is implied |
| `diagnostics/webview2-render-evidence.json` | Consent/inclusion state plus capture trigger, timestamps, graphics/window state, pixel metrics, integrity hashes, recovery assessment, and capture failures | Always present in newer v1 bundles; manual captures require explicit inclusion, while an automatic display-recovery incident is included when the user exports the diagnostic bundle |
| `render-evidence/webview2-pet.png` | WebView2 `CapturePreview` of the pet WebView | Sensitive artifact; manual captures require explicit inclusion, while the bounded latest automatic recovery incident is included with the user-exported diagnostic bundle |
| `render-evidence/windows-desktop.png` | GDI `BitBlt` capture of the complete Windows virtual desktop using `CAPTUREBLT` | Highly sensitive artifact; can include every visible monitor and other applications; automatic recovery retains only the latest incident |
| `diagnostics/model-catalog.json` | Public model/provider catalog used by the runtime | Optional when the local bridge cannot provide it |
| `diagnostics/attachments.json` | Attachment references, integrity checks, and original inclusion status | Candidates come only from included sessions |
| `diagnostics/windows-shell/current.json` | Current Windows foreground, taskbar, selected window, geometry, style, and Z-order snapshot | `capability: unsupported` on non-Windows; may be absent after a collector failure |
| `diagnostics/windows-shell/events-NNN.jsonl` | Oldest-first bounded Windows Shell event batches with a post-event snapshot | Independent journal budget; each line is one record |
| `sessions/session-NNN.json` | Selected session projection with ordered events | Complete private content may be present; `active_events` is removed |
| `logs/<allowlisted-name>` | Bounded log content after redaction | Per file 5 MiB tail; total 20 MiB |
| `attachments/NNN-<name>` | Optional verified attachment original | Total originals capped at 200 MiB |

## Manifest Semantics

- `formatVersion`: breaking bundle-format boundary. Unknown values require producer-source inspection.
- `schemaRevision`: additive revision within the format. It may be absent from older v1 bundles.
- `capabilities`: evidence families the producer contract declares. An unknown capability is a consumer-coverage gap even when the ZIP remains structurally readable.
- `outcome: complete`: no collector added a failure.
- `outcome: partial`: a non-fatal collection failed; the ZIP is still usable.
- `requestedSessionIds` versus `includedSessionIds`: establish whether selected session fetches succeeded.
- `failures`: preserve collection, item, and message; do not collapse these into a generic warning.
- `excluded`: currently calls out the memory database, persona assets, raw settings, full environment, and unselected sessions.
- `logs[].tailStartOffset > 0`: earlier log content is absent. Absence before that offset is not evidence.
- `logs[].includedBytes` is measured after text decoding/redaction and may differ from the raw byte span.
- Attachment summary reports requested originals, item count, estimated/included/skipped/failed bytes, and the 200 MiB cap.
- `windowsShell.supported` distinguishes platform support; `artifacts` names the shell entries expected in the archive; `failureCount` reports collection/export failures.
- `diagnostics/webview2.json` records up to 128 `ProcessFailed`, `BrowserProcessExited`, and pet page-visibility events. A nonzero `droppedEventCount` means earlier events were evicted. Empty events prove only that registered handlers observed no failure; inspect `registeredWindows` and `registrationFailures` before treating the absence as evidence.
- `renderEvidence.includeRequested` remains the second consent boundary for manual captures. `automaticIncidentIncluded` means the latest bounded display-recovery incident was included by the user's diagnostic-bundle export without a separate checkbox. `pendingCapture` means a local capture existed; `artifacts` lists only screenshots actually included. Missing referenced screenshots, hash/size mismatch, partial capture errors, or a large `startDeltaMs` are explicit evidence gaps.
- `install-integrity-v1` means `install/integrity.json` was collected. Older bundles without the capability remain valid; the inspector reports `evidence: not-collected` and must not infer either consistency or corruption.
- `desktop-instance-governance-v1` means the bundle records the running shell's resolved domain and current owner. A missing owner is an evidence gap, not proof that no competing historical launch occurred; correlate `tauri.log.runtime*` instance-governance events for recent arbitration.

Format v1 permits additive fields. Ignore unknown fields safely and do not require newer optional fields when reading an older v1 bundle.

## Session Evidence

Session files normally contain `session_id`, title, model, persona, and ordered `events`. Common event types include:

- `session_meta`: initial model/persona/title/schema context;
- `user_message`: content and optional attachment descriptors;
- `assistant_message`: content, partial marker, and run/model/persona metadata;
- `tool_call_request` / `tool_call_result`: tool ids, arguments/results, duration, and error state;
- `compaction`: coverage boundary, summary, and token counts;
- `turn_rewrite`: inactive event UUIDs and replacement relationship.

Use event order and stable ids, not array position alone, to correlate a turn. A session projection describes persisted events; it is not a direct dump of transient frontend state because `active_events` is deliberately removed.

## Attachment Evidence

An attachment item maps an `assetId` to its display metadata and referencing session/event/run. Integrity requires all of:

- valid 64-character hexadecimal asset id;
- matching metadata asset id, SHA-256, and declared size;
- existing blob;
- matching actual size;
- blob hash equal to both SHA-256 and asset id.

`originalStatus` can be `excluded`, `included`, `skipped`, or `failed`. `processingStatus: notRecorded` means the bundle does not establish later model-side or preprocessing behavior. Original inclusion proves only that the stored source blob passed local integrity checks.

## Log Evidence Map

| Log family | Boundary represented |
| --- | --- |
| `tauri.log*` | Desktop shell plus forwarded frontend/WebView application logs |
| `runtime-bridge.log*` | Packaged bridge process stdout/stderr, including server/access output |
| `agent_bridge.log*` | Bridge application's own structured logs for chat/session/tool/model flows |
| `memory.log*` | Memory subsystem storage, retrieval, and related processing |
| `runtime-voice.log*` | Packaged voice process stdout/stderr, including server/access output |
| `voice_bridge.log*` | Voice application's own structured transcription/RTC/tunnel logs |
| `dev-bridge.log*` / `dev-voice.log*` | Development-run bridge/voice capture when present |

All matching regular files in the canonical log directory are sorted newest first. Symlinks, directory escapes, and non-allowlisted names are rejected. Correlate duplicated-looking runtime and application logs by timestamp and identifier rather than counting them as independent occurrences.

## Windows Shell Evidence

`windows-shell-history-v1` is event-driven and independent from `tauri.log`. The producer coalesces short event bursts, records at most 64 raw events per journal record, and persists dropped/truncated counters when pressure exceeds those limits. The journal rotates independently from ordinary logs and is exported oldest first.

The current snapshot and each journal snapshot may contain:

- foreground HWND and selected agent-friend/taskbar/fullscreen-candidate windows;
- process id and executable basename, window class, visibility/minimize/maximize/cloak state;
- window, DWM frame, monitor, and work-area rectangles;
- style/extended-style flags relevant to topmost, layered, transparent, no-activate, and tool windows;
- `NonRudeHWND` property presence, top-to-bottom Z-order rank, and fullscreen geometry candidacy;
- taskbar auto-hide/always-on-top state and whether a visible taskbar ranked above the foreground fullscreen candidate;
- probe failures.

Use `recordedAt` and event `receivedAt` to narrow the incident window. Treat `droppedEventCount`, `truncatedEventCount`, malformed journal records, missing referenced artifacts, or snapshot `probeErrors` as explicit coverage gaps. A geometry candidate is evidence about window bounds, not proof that Windows internally classified the window as fullscreen.

## Install Integrity Evidence

`install-integrity-v1` cross-checks independently embedded and installed identities instead of treating file existence as proof of a complete upgrade. Its top-level `status` is one of:

- `consistent`: shell identity, active receipt, payload manifest and every manifest-listed file agree; any recorded readiness identities also agree;
- `incomplete_install`: the receipt, payload, manifest or transaction state is missing, unreadable, invalid, unsafe, or otherwise incomplete;
- `shell_receipt_mismatch`: the newly running shell does not match the active receipt selected on disk;
- `payload_hash_mismatch`: payload identity, manifest identity, required capabilities, or a file hash does not agree;
- `runtime_identity_mismatch`: a service answered readiness with a build/profile/hash different from the shell's expected identity;
- `unavailable`: source/dev builds have no embedded package identity, or the collector could not classify the evidence.

The `shell`, `activeInstall`, and `payload` objects expose build/profile identity hashes and only the receipt's relative payload path. `payload.runtimeExecutableSha256` is the expected frozen runtime hash from the validated manifest. `runtime.observedReadiness` is the most recent authenticated readiness body observed by the supervisor, not a historical process inventory. `runtime.jobObject` records whether packaged supervision created a Job Object and how many product children were assigned. `residue` reports the legacy fixed runtime and incomplete transaction count; residue can be pending cleanup even when the active version is otherwise consistent.

An absent observed readiness value means the service had not produced a successful HTTP readiness body during the current shell process. It does not mean the runtime executable was absent. Conversely, `consistent` establishes current disk and observed identity consistency; it does not reconstruct a previous failed install that left no transaction or log evidence.

## WebView2 Process Evidence

`webview2-process-health-v1` is a passive Windows-only observer. Each registered WebView contributes `ProcessFailed`; one shared environment contributes `BrowserProcessExited`. The pet WebView also reports `page-visibility` transitions so an armed one-shot capture can trigger after a real hidden interval. The producer records runtime version, whether a failure-report folder is available, metadata for up to 16 recent `.dmp` files (name, size, modified time; never dump contents), registered window labels, registration failures, DXGI adapter/driver identity, DWM composition state, and a bounded oldest-evicted event list. The same events are also written as `source: webview2` records in the bounded `tauri.log.runtime*` journal, so a bundle generated after restarting the desktop shell can still retain the prior process failure. A shared process failure can appear once per affected window; correlate by process session, timestamp, kind, reason, exit code, and a nearby failure-report timestamp instead of counting matching window events as independent crashes.

Interpret `gpu-process-exited` as an observed GPU process failure, not proof that the GPU caused the original symptom; WebView2 normally recreates that process. `render-process-exited` and `browser-process-exited` are stronger evidence of renderer/browser failure, but recovery behavior still depends on the application. Absence of events cannot rule out DirectComposition/host-HWND presentation failures because the WebView2 processes may remain healthy while Windows shows stale or black pixels.

## WebView2 Render Evidence

`webview2-render-evidence-v2` retains the manual one-shot flow from v1 and adds automatic display-recovery evidence. On a real `Off/Dimmed → On` transition, the native recovery state machine captures a hidden WebView/desktop baseline before showing the pet, then captures the presented desktop and records a fail-closed assessment. Only the latest capture is retained under the bounded log directory. Manual captures still require explicit ZIP inclusion; an automatic recovery incident is included when the user deliberately exports a diagnostic bundle, without requiring prior arming or a second checkbox.

Use `startDeltaMs` to judge pairing quality, and compare `webView2Image` with `desktopImage` together with process events:

| Evidence | Strongest next direction |
| --- | --- |
| WebView2 image has visible pet pixels; desktop is black | Host HWND / DirectComposition / Windows presentation path |
| Both images are black and a GPU/renderer failure is present | WebView2 renderer or shared GPU-process recovery |
| Both captured images look normal but the user physically saw black | Downstream DWM/display-driver/scan-out path; correlate adapter/driver and external app crashes |
| Only one image exists, capture errors are nonzero, or pairing delta is large | Insufficient paired evidence; do not force a root cause |

The pixel metrics intentionally treat alpha below 16 as transparent, so a transparent pet background is not mislabeled as opaque black. Metrics are triage aids; inspect the actual PNGs only when necessary and authorized. `CapturePreview` requires WebView2 `ContentLoading` to have fired and can otherwise fail or return the previous page, so preserve its capture error and timing rather than substituting another screenshot.

## Redaction and Privacy Boundary

The producer constructs diagnostic JSON from allowlisted fields, recursively replaces sensitive-key values, replaces configured credential values in text, redacts lines containing common authorization/credential markers, and replaces the user's home path with `<USER_HOME>`. Install-integrity evidence emits stable identity hashes, relative paths, counts, and status codes rather than install-root paths or process command lines. Windows Shell evidence intentionally excludes window titles, window contents, command lines, and full executable paths; it retains executable basenames and window classes for correlation.

This is credential protection, not general content review. Selected sessions, problem text, tool arguments/results, log messages, filenames, attachment originals, and render screenshots may still contain private or unknown-sensitive data. The full-desktop render artifact is especially sensitive because it can contain every visible monitor and other applications. Do not display, extract, or inspect either PNG unless its pixels are necessary to distinguish the rendering hypotheses; extract only the named artifact to an agent-created temporary directory and remove it afterward.
