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
7. describes the latest one-shot or automatic display/GPU recovery capture and verifies any included WebView2/internal and full-desktop PNGs; manual artifacts still require explicit inclusion, while the latest bounded automatic incident is attached to a user-exported bundle;
8. snapshots shell, active receipt, payload hashes, observed runtime readiness, Job Object assignment, legacy layout, and incomplete installer transactions;
9. snapshots the current desktop instance domain and its safely attributable owner evidence;
10. flushes the independent Windows Shell event journal and adds its bounded history plus a current snapshot;
11. requests the Bridge's already-sanitized managed execution environment health and resource summary;
12. reads bounded, persisted pet input history and independent incidents without waiting for the pet page or its writer; emits coverage even when this source is unavailable;
13. redacts managed credentials and replaces the user home path;
14. validates the manifest and every archive entry against the producer-owned bundle contract;
15. writes a `.zip.part` file in Downloads and atomically renames it after closing and syncing the ZIP.

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
| `diagnostics/execution-environments.json` | Managed provider status, runtime/version identity, prepare duration bucket, overlay/cache size buckets, recent GC state and packaged-payload validation | Present when `managed-execution-environment-v1` is declared; deliberately excludes session ids/keys, paths, package names, scripts, file content, environment values, commands and output |
| `diagnostics/desktop-instance.json` | Resolved runtime kind, shared instance domain, process session, and current owner evidence | Present when `desktop-instance-governance-v1` is declared; paths are home-redacted and no command-line or environment content is included |
| `install/integrity.json` | Embedded shell identity, active receipt, verified payload/runtime hashes, observed readiness identity, Job Object state, and installer residue | Present when `install-integrity-v1` is declared; uses relative paths and stable error codes, never credential values |
| `diagnostics/webview2.json` | WebView2 runtime version, recent crash-report metadata, handler registration coverage, graphics inventory, and a bounded process-failure event buffer | `supported: false` on non-Windows; raw dump files are never included; `gpu-process-exited` can trigger the product's bounded recovery flow |
| `diagnostics/webview2-render-evidence.json` | Consent/inclusion state plus capture trigger, timestamps, graphics/window state, black/white pixel metrics, integrity hashes, recovery assessment, and capture failures | Always present in newer v1 bundles; manual captures require explicit inclusion, while the latest automatic display- or GPU-recovery incident is included when the user exports the diagnostic bundle |
| `render-evidence/webview2-pet.png` | WebView2 `CapturePreview` of the pet WebView | Sensitive artifact; manual captures require explicit inclusion, while the bounded latest automatic recovery incident is included with the user-exported diagnostic bundle |
| `render-evidence/windows-desktop.png` | GDI `BitBlt` capture of the complete Windows virtual desktop using `CAPTUREBLT` | Highly sensitive artifact; can include every visible monitor and other applications; automatic recovery retains only the latest incident |
| `diagnostics/model-catalog.json` | Public model/provider catalog used by the runtime | Optional when the local bridge cannot provide it |
| `diagnostics/attachments.json` | Attachment references, integrity checks, and original inclusion status | Candidates come only from included sessions |
| `diagnostics/windows-shell/current.json` | Current Windows foreground, taskbar, selected window, geometry, style, and Z-order snapshot | `capability: unsupported` on non-Windows; may be absent after a collector failure |
| `diagnostics/windows-shell/events-NNN.jsonl` | Oldest-first bounded Windows Shell event batches with a post-event snapshot | Independent journal budget; each line is one record |
| `diagnostics/pet-input/coverage.json` | Persisted input evidence coverage, occurrence identities, retention/durability gaps and bounded incident summaries | Required only when `pet-input-evidence-v1` is declared; absence in older v1 is not corruption |
| `diagnostics/pet-input/current.json` | Optional export-time native state | Never substitute it for an occurrence-time observation |
| `diagnostics/pet-input/history-NNN.jsonl` | Bounded default input-chain state history with per-segment identity | No continuous mouse trajectory; an event is an observation, not a total cross-process order |
| `diagnostics/pet-input/incident-NNN.json` | Independently retained input signal with bounded context and recovery evidence | Signals are not root-cause verdicts; healthy history does not replace retained incidents |
| `sessions/session-NNN.json` | Selected session projection with ordered events | Complete private content may be present; `active_events` is removed; `selected-sessions-v2` replaces provider reasoning payloads with content-free summaries |
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
- `renderEvidence.includeRequested` remains the second consent boundary for manual captures. `automaticIncidentIncluded` means the latest bounded display- or GPU-recovery incident was included by the user's diagnostic-bundle export without a separate checkbox. `pendingCapture` means a local capture existed; `artifacts` lists only screenshots actually included. Missing referenced screenshots, hash/size mismatch, partial capture errors, or a large `startDeltaMs` are explicit evidence gaps.
- `install-integrity-v1` means `install/integrity.json` was collected. Older bundles without the capability remain valid; the inspector reports `evidence: not-collected` and must not infer either consistency or corruption.
- `desktop-instance-governance-v1` means the bundle records the running shell's resolved domain and current owner. A missing owner is an evidence gap, not proof that no competing historical launch occurred; correlate `tauri.log.runtime*` instance-governance events for recent arbitration.
- `managed-execution-environment-v1` means the bundle records a bounded, privacy-preserving execution environment summary. `runtimePayload.declared: true` with `validated: false` is strong current-payload failure evidence; `declared: false` is expected in source mode and does not itself mean Python is unavailable. Size and duration values are coarse buckets, not exact disk accounting. Older bundles without this capability remain valid and establish no managed-environment state.
- `pet-input-evidence-v1` adds `petInputEvidence` and required coverage metadata. Inspect both coverage and collection failures: successful ZIP generation and current healthy input do not establish complete historical coverage. Missing declared artifacts, malformed records or invalid metadata are consumer evidence gaps; no capability in an old bundle means `not-collected`, not proof that no failure occurred.

Format v1 permits additive fields. Ignore unknown fields safely and do not require newer optional fields when reading an older v1 bundle.

## Session Evidence

Session files normally contain `session_id`, title, model, persona, and ordered `events`. Common event types include:

- `session_meta`: initial model/persona/title/schema context;
- `user_message`: content and optional attachment descriptors;
- `assistant_message`: content, partial marker, and run/model/persona metadata;
- `tool_call_request` / `tool_call_result`: tool ids, arguments/results, duration, and error state;
- `compaction`: coverage boundary, summary, and token counts;
- `turn_rewrite`: inactive event UUIDs and replacement relationship.
- `provider_reasoning_state`: provider-private continuation metadata. Under `selected-sessions-v2`, `data` must be absent and `redacted: true` plus optional length/count fields describe the removed payload. Treat any retained `data` as a producer privacy-contract violation; do not display it.

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

## Pet Input Evidence

`pet-input-evidence-v1` is default, local, bounded observation of the pet input chain. It does not repair DPR, retry business commands, collect keyboard input, or require advance debug/CDP setup. The native writer owns persistence; the frontend contributes only the facts it observed. Export reads persisted files, so a silent or unavailable pet page does not prevent another existing feedback entrance from exporting history.

Start with `coverage.json`: `artifacts` describes the actual included ranges, `identities` binds occurrence process sessions to their recorded builds, `counters` and `gaps` describe known loss, and `retainedIncidents` summarizes independently retained signals. Optional `evictions` describes bounded removal metadata; absent counters or positions stay unknown rather than becoming zero. `durability.lastDroppedRange` brackets known dropped ingress identifiers; `interior: not-necessarily-contiguous` explicitly does not establish that every identifier between its ends was lost. The inspector validates their types and referenced artifacts, streams bounded history lines, and summarizes counts without printing coordinates or event payloads. Newer additive fields are not copied into its output. It does not perform a root-cause classification or reconstruct unobserved stages.

Each history segment begins with a `segment-header` carrying its own identity; each incident embeds its occurrence identity and bounded `context`. Do not bind older records to the current `diagnostics/application.json`, or assume that an available build commit with `dirty: true` proves exact source identity. Optional `current.json` is explicitly export-time evidence and cannot overwrite historical state.

Correlation uses process session, page instance, frontend request/sequence, native record sequence, DPR write revision, cursor sample and actual apply sequence. The writer's `recordSeq` is the persisted order; optional `ingressSeq` identifies diagnostic queue ingress, and neither proves business application order. A frontend Promise acknowledgement, command reception and Windows main-thread application are separate facts. `window-observed` records existing native window geometry/scale events, not DOM content or an atomic whole-desktop snapshot. Wall clocks can jump and are not a unique order across processes/pages; unknown or missing stages must stay unknown. A normal miss or a missing acknowledgement is not itself a business failure. A sustained scale mismatch, explicit command failure or partial style application is a signal to inspect, not a diagnosis of the original trigger.

Default history and recognized incidents have separate budgets. History has a maximum age of 7 days, a 16 MiB total and 2 MiB segments. Incidents have a maximum age of 30 days, 32 files and 8 MiB total, with 256 KiB per incident. Metadata and temporary files have another 1 MiB budget; this evidence family is bounded to 25 MiB for disk and export. Time, count or byte caps take effect at the first boundary reached: these windows are not minimum retention guarantees. Ordinary healthy history does not consume the independent incident budget.

Interpret gaps individually: `not-collected`, `unsupported`, `not-covered`, `expired`, `evicted`, `truncated`, `write-failed`, `read-failed`, `corrupt-tail` and `unknown` are distinct states. Known drop, invalid-record and rate-limit counters describe observer coverage, not pet business failures. Absence of a file does not prove the reason for its absence. Normal finite retention or unsupported platform evidence does not make the whole bundle corrupt; collection/read failures make export partial while preserving other collectors.

`durability.scope: persisted-only` means export does not force a writer flush or wait for frontend delivery. The normal flush interval is 1 second and file-sync request interval 5 seconds, but neither is a bound on crash-time loss under slow/full storage. No clean end marker means the unknown tail can be larger; the final write failure itself may be lost. A complete-line tail and an available latest heartbeat prove only the observations they contain, not continuous input capture or page health.

The family deliberately excludes DOM content/ids, other application titles, arbitrary error strings, chat, credentials, screenshots and continuous cursor tracks. Windows may record the actual existing style application/verification; other platforms report their available API result without inventing Windows style facts. Inspect only the minimum relevant coordinate/event slice when a diagnosis requires it; inventory output must remain content-free.

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

Interpret `gpu-process-exited` as an observed GPU process failure, not proof that the GPU caused the original symptom; WebView2 normally recreates that process. Current producers deduplicate the per-window fan-out into one recovery trigger while retaining every raw per-window event. The recovery immediately hides the transparent pet overlay, waits for WebView2's normal recovery window, then probes paired render evidence. Persistent failure can request one cooldown-guarded shell restart; structured `gpu-recovery-*` records in `tauri.log.runtime*` establish what action was taken. `render-process-exited` and `browser-process-exited` remain passive evidence and are stronger signals of renderer/browser failure. Absence of events cannot rule out DirectComposition/host-HWND presentation failures because the WebView2 processes may remain healthy while Windows shows stale, black, or white pixels.

## WebView2 Render Evidence

`webview2-render-evidence-v2` retains the manual one-shot flow from v1 and adds automatic display-recovery evidence. On a real `Off/Dimmed → On` transition, the native recovery state machine captures a hidden WebView/desktop baseline before showing the pet, then captures the presented desktop and records a fail-closed assessment. Only the latest capture is retained under the bounded log directory. Manual captures still require explicit ZIP inclusion; an automatic recovery incident is included when the user deliberately exports a diagnostic bundle, without requiring prior arming or a second checkbox.

`webview2-render-evidence-v3` retains v2 and adds `webview2-gpu-hidden-baseline` / `webview2-gpu-presented-probe` automatic incidents plus near-white opaque pixel counts and ratios. Both the hidden WebView baseline and presented virtual desktop can now be rejected for an almost-solid white surface as well as black. The capture's `recoveryAssessment` records baseline/presented black and white ratios and deltas. A safe presented probe means the shell kept running; an unsafe, failed, or timed-out GPU probe is fail-closed and should be correlated with `gpu-recovery-restart-requested`, `restart-blocked`, or `restart-disabled` in the runtime journal.

Use `startDeltaMs` to judge pairing quality, and compare `webView2Image` with `desktopImage` together with process events:

| Evidence | Strongest next direction |
| --- | --- |
| WebView2 image has visible pet pixels; desktop is black | Host HWND / DirectComposition / Windows presentation path |
| Both images are black and a GPU/renderer failure is present | WebView2 renderer or shared GPU-process recovery |
| Both images are white and a GPU failure is present | WebView2 shared GPU-process recovery or a Runtime/driver regression; inspect the GPU recovery journal outcome |
| Both captured images look normal but the user physically saw black | Downstream DWM/display-driver/scan-out path; correlate adapter/driver and external app crashes |
| Only one image exists, capture errors are nonzero, or pairing delta is large | Insufficient paired evidence; do not force a root cause |

The pixel metrics intentionally treat alpha below 16 as transparent, so a transparent pet background is not mislabeled as opaque black. Metrics are triage aids; inspect the actual PNGs only when necessary and authorized. `CapturePreview` requires WebView2 `ContentLoading` to have fired and can otherwise fail or return the previous page, so preserve its capture error and timing rather than substituting another screenshot.

## Redaction and Privacy Boundary

The producer constructs diagnostic JSON from allowlisted fields, recursively replaces sensitive-key values, replaces configured credential values in text, redacts lines containing common authorization/credential markers, and replaces the user's home path with `<USER_HOME>`. Install-integrity evidence emits stable identity hashes, relative paths, counts, and status codes rather than install-root paths or process command lines. Managed execution environment evidence emits only provider identity summaries, stable errors, counts and coarse buckets; it excludes session identities, package inventories, paths, scripts, file content, environment values, commands and output. Windows Shell evidence intentionally excludes window titles, window contents, command lines, and full executable paths; it retains executable basenames and window classes for correlation.

This is credential protection, not general content review. `selected-sessions-v2` additionally strips provider reasoning continuation bodies even when they are not credentials; the inspector reports only event/redaction counts. Selected sessions, problem text, ordinary assistant/user content, tool arguments/results, log messages, filenames, attachment originals, and render screenshots may still contain private or unknown-sensitive data. The full-desktop render artifact is especially sensitive because it can contain every visible monitor and other applications. Do not display, extract, or inspect either PNG unless its pixels are necessary to distinguish the rendering hypotheses; extract only the named artifact to an agent-created temporary directory and remove it afterward.
