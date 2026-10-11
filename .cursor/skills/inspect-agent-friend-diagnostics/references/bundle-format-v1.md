# Agent-Friend Diagnostic Bundle Format v1

Use this reference to interpret bundles whose `manifest.json` has `formatVersion: 1`. The recorded build commit remains authoritative when an older or branch-specific producer differs from the current source. The current producer's structural source of truth is `frontend/src-tauri/src/diagnostics/bundle-contract-v1.json`; this reference explains evidence semantics rather than duplicating that machine contract.

## Desktop Application Launch Evidence

`desktop-application-launch-v1` adds `diagnostics/desktop-applications.json`.
It projects at most 128 launch records from a bounded scan of persisted tool evidence
(1,000 files and 4 MiB read budget). It does not promise full history or recency;
deleted sessions and scan limits restrict coverage. It never includes application paths,
arguments, raw session/run IDs or desktop contents. The inspector prints counts only.
`accepted` means Explorer accepted a launch request, not application readiness or survival.
`unknown` includes an interrupted dispatch without a retained acknowledgement; do not infer
the application was never opened or replay the launch. `launchRef` is a hashed correlation
key, not a PID. `user_desktop` describes intended ownership, not a live process inventory.
Bridge unavailability is a collector gap; ordinary command cleanup does not imply the
desktop application was terminated. Inspect selected tool events and actual application
evidence separately when determining who closed an application.

## Startup timing evidence

`startup-timeline-v1` (schema revision 20) describes startup records in the retained
`logs/tauri.log.runtime*` journals, not a new export-time measurement. The inspector's
`startupTimeline` groups whitelisted phases and frontend milestones by their historical
`processSessionId`. It reads at most 4 MiB / 10,000 records / 64 processes and reports limits.
The producer retains at most three 1 MiB journals for 30 days; rotation, crash or write failure
can leave missing starts or finishes. A start without a finish is unfinished, never zero or success.
An error/incomplete finish retains partial counts. Old bundles without these events remain readable.

`payload-verification` contains aggregate metadata/read/hash times and file/byte counts from
ordinary startup validation. Diagnostic export's own integrity check is not a startup sample.
The tauri-initialize span ends at setup entry and contains build plus any event-loop preparation. Nested initialize/build and service phases overlap and must not be summed. Rust elapsed times share
only one process-local monotonic clock. Frontend `elapsedMs` is native IPC receipt time;
`pageElapsedMs` is the separate page clock and is never subtracted from the Rust clock.
`frontend-ready` requires settings, root commit, model initialization, restoration and an
actual enabled editor. Hidden-window preparation, visibility and a completed real user action
remain distinct; this marker proves neither successful model generation nor first interaction.
No message bodies, file paths, credentials or per-file names are collected by these probes.

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
| `diagnostics/command-runtime.json` | Current sandbox state, helper presence and bounded content-free command setup/network/recovery history | `command-runtime-health-v2` (v1 remains readable); counts and stable codes only in inventory; raw output, credentials and paths are excluded |

`command-runtime-health-v2` adds `automatic_recovery`, `manual_recovery`, and
`recovery_resume` event stages. A repair flight and the actions resumed after it
share a hashed `operation` identifier. `requested` means a repair flight began;
`ok` on recovery requires the existing execution probe to pass. An `ok` resume
means preparation or process dispatch returned successfully, not that the business
command completed. Original failures remain separate start/visibility records;
correlate their runtime and time ordering without claiming an unobserved native
root cause. Inventory exposes only allowlisted stage/code counts. Missing stages
in older bundles mean recovery evidence was not collected, not proof of no faults.
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
| `logs/<allowlisted-name>` | Complete retained log prefix after redaction | No export byte cap with `full-retained-logs-v1`; old tail capability: 5 MiB/file, 20 MiB total |
| `attachments/NNN-<name>` | Optional verified attachment original | No export byte cap with `attachment-integrity-v2`; v1 capped at 200 MiB |

## Manifest Semantics

- `formatVersion`: breaking bundle-format boundary. Unknown values require producer-source inspection.
- `schemaRevision`: additive revision within the format. It may be absent from older v1 bundles.
- `capabilities`: evidence families the producer contract declares. An unknown capability is a consumer-coverage gap even when the ZIP remains structurally readable.
- `outcome: complete`: no failure was added to the bundle manifest; historical evidence warnings may still be present.
- `outcome: partial`: a non-fatal collection failed; the ZIP is still usable.
- `requestedSessionIds` versus `includedSessionIds`: establish whether selected session fetches succeeded.
- `failures`: preserve collection, item, and message; do not collapse these into a generic warning.
- `warnings` (native-crash-evidence-v2): preserve historical evidence gaps separately; they do not change the bundle outcome. See Native Crash Evidence for the narrowly classified code.
- `excluded`: currently calls out the memory database, persona assets, raw settings, full environment, and unselected sessions.
- `logs[].tailStartOffset > 0`: earlier log content is absent. Absence before that offset is not evidence.
- `logs[].includedBytes` is measured after text decoding/redaction and may differ from the raw byte span.
- Attachment summary reports requested originals, item count, estimated/included/skipped/failed bytes, and `maxOriginalBytes` (`null` means no export cap; older v1 uses 200 MiB).
- `windowsShell.supported` distinguishes platform support; `artifacts` names the shell entries expected in the archive; `failureCount` reports collection/export failures.
- `diagnostics/webview2.json` records up to 128 `ProcessFailed`, `BrowserProcessExited`, and pet page-visibility events. A nonzero `droppedEventCount` means earlier events were evicted. Empty events prove only that registered handlers observed no failure; inspect `registeredWindows` and `registrationFailures` before treating the absence as evidence.
- `renderEvidence.includeRequested` remains the second consent boundary for manual captures. `automaticIncidentIncluded` means the latest bounded display- or GPU-recovery incident was included by the user's diagnostic-bundle export without a separate checkbox. `pendingCapture` means a local capture existed; `artifacts` lists only screenshots actually included. Missing referenced screenshots, hash/size mismatch, partial capture errors, or a large `startDeltaMs` are explicit evidence gaps.
- `install-integrity-v1` means `install/integrity.json` was collected. Older bundles without the capability remain valid; the inspector reports `evidence: not-collected` and must not infer either consistency or corruption.
- `desktop-instance-governance-v1` means the bundle records the running shell's resolved domain and current owner. A missing owner is an evidence gap, not proof that no competing historical launch occurred; correlate `tauri.log.runtime*` instance-governance events for recent arbitration.
- `managed-execution-environment-v1` means the bundle records a bounded, privacy-preserving execution environment summary. `runtimePayload.declared: true` with `validated: false` is strong current-payload failure evidence; `declared: false` is expected in source mode and does not itself mean Python is unavailable. Size and duration values are coarse buckets, not exact disk accounting. Older bundles without this capability remain valid and establish no managed-environment state.
- `pet-input-evidence-v1` adds `petInputEvidence` and required coverage metadata. Inspect both coverage and collection failures: successful ZIP generation and current healthy input do not establish complete historical coverage. Missing declared artifacts, malformed records or invalid metadata are consumer evidence gaps; no capability in an old bundle means `not-collected`, not proof that no failure occurred.

Format v1 permits additive fields. Ignore unknown fields safely and do not require newer optional fields when reading an older v1 bundle.

## Command Runtime Evidence

`sandbox-incidents-v3` retains v1/v2 incidents and adds `failureHelperProbe`
to selected helper launch/incomplete failures. The original state, log window and
error are persisted before this bounded, non-elevated, argument-parsing-only probe;
its result is a later observation, not proof that the original elevated helper
loaded or that sandbox accounts work. `in_progress` means no completion was retained.
The probe never passes a setup payload or changes accounts. A failed probe can
distinguish ordinary executable loading from the elevation path; a successful probe
does not rule out elevation, identity, ACL or intermittent failures.
`initialProbeFailure` preserves the explicit repair's pre-provisioning probe error
separately from its final error. Intact on-disk identity is tested even when a prior
failure is latched; only actual probe success clears that latch. Missing identities
still require explicit setup. These fields do not recover a first native error
already overwritten inside the pinned Codex binary. Native 1223 never establishes
manual user cancellation. Old retained records may lack both fields.

`command-runtime-health-v1` adds a read-only Bridge snapshot and at most 128 recent events. Events carry their own hashed build, runtime and operation identities, timestamps, stage, stable code, optional native code and proxy kind. Hashes cannot reconstruct a command or session. Native 1223 means the launch API returned cancellation; it does not establish which user action caused it. Unknown module names remain unknown.

History is persisted as a bounded 64 KiB JSON snapshot under the canonical log directory; policy observations are flushed at process completion or runtime close. A crash can lose the unflushed policy tail. `evicted`, `history_unavailable`, `invalid_record` and `write_failed` are observer gaps, not business failures. Helper presence is not hash or load verification. Current ready status cannot replace occurrence-time evidence. Export never starts setup or a network probe. Older bundles lacking the capability are not-collected.

## Session Evidence

`selected-sessions-v4` removes scan, individual event and projected-output byte caps for both recent and all-turn exports. The desktop defaults to all turns; explicit recent-range selection still applies. Coverage schema 2 reports null `scanBytes`, `eventBytes` and `exportBytes` limits. A frozen persisted prefix bounds each export in time, not size. Corrupt records or concurrent truncation still produce explicit gaps; raw internal snapshot bodies and provider reasoning remain excluded. Full log export streams redacted data through owned temporary files and uses ZIP64; original attachments remain opt-in and integrity checked. No export cap can recover records already deleted by runtime retention.

`pet-input-evidence-v2` reports an inherited `metadataUnavailable` flag as a `historical-metadata-unavailable` coverage gap, without counting it as a current export failure. Actual read/parse/write failures still make the export partial. Never interpret a complete export as complete historical observation.

Older `selected-sessions-v3` adds a bounded diagnostic projection. The default is the
latest 10 effective user/system-trigger turns per selected session; `all` requests
all turns subject to the same byte guards. An ongoing turn includes only its
persisted prefix. `coverage` records requested/included turns, source offsets,
read/output bytes, completeness, exclusions and gaps. Do not treat the selected
range as full history. Tool sequences within the range are retained; references
to earlier rewrites/compactions are not recursively expanded. Internal rollout
snapshots are summarized to prevent older chat or private continuation content
from re-entering the range. Original attachments are derived from included events.
An oversized or corrupt boundary makes the range partial, without preventing
system evidence export. The maximum scan is 64 MiB, decoded event 4 MiB and event
output 16 MiB per session; full JSON envelope overhead is additional. Older
capabilities retain their original semantics.

`sandbox-incidents-v1` adds `diagnostics/command-runtime-incidents.json`. Failure
records are independent of the 128-event health queue: up to 32 records, 128 KiB
each, at most 30 days. Repair records retain their before state, related incident
IDs, chosen repair path and result; a later ready snapshot does not erase a
failure. Tauri can read persisted records when Bridge is unavailable. Read each
record's build/runtime identity and report attribution: unchanged reports and
concurrent observations are not uniquely attributable. `expectedHelpers` refers
to expected packaged images, not observed loaded modules. Extracted log error
codes are candidates from a bounded tail, not per-operation causality. No raw
secrets, arbitrary error text, commands or helper logs are exported in this family.
An in-progress record after restart means the final result was not retained; it
does not prove setup failed. Durability is limited to persisted records.

`sandbox-incidents-v2` keeps v1 records readable and adds `logWindow` to newly
completed records. At operation start the observer captures private file offsets;
at completion it reads up to 64 KiB per file from at most two logs and exports
only fixed signals, byte offsets and native codes (at most 64 events per file).
`appended` means bytes appended to a matching baseline file, not unique operation
attribution: other processes can share the log. `unattributed_tail` means a new
or replaced file with no usable baseline. Preserve byte/event limits, partial
lines, missing baselines and read failures; absence of a signal is not proof
that a branch did not execute. Old retained incidents can lack `logWindow`.
Setup reasons distinguish missing/incompatible users, missing/incompatible
markers and offline firewall settings changes. Upstream deletion-after-login
failure is debug-only; normal logging may omit it, and the product does not
enable debug logging for this collector. A deletion signal describes an attempt,
not a successful delete. No raw lines, paths, usernames, ports or arguments are
exported. The repair probe now tests writable execution followed by fresh
read-only directory preparation and read-only execution; success covers these
probes only, not every future command or automatic recovery.

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

Interpret `gpu-process-exited` as an observed GPU process failure, not proof that the GPU caused the original symptom; WebView2 normally recreates that process. Producers retain every raw per-window event. v3 recovery hides the transparent pet overlay and uses paired render evidence to decide whether to restore it. v4 also coalesces delayed fan-out while recovery is active and keeps the failed host hidden through bounded evidence collection, then requests one cooldown-guarded shell restart regardless of screenshot health; structured `gpu-recovery-*` records in `tauri.log.runtime*` establish what action was taken. `render-process-exited` and `browser-process-exited` remain passive evidence and are stronger signals of renderer/browser failure. Absence of events cannot rule out DirectComposition/host-HWND presentation failures because the WebView2 processes may remain healthy while Windows shows stale, black, or white pixels.

## WebView2 Render Evidence

`webview2-render-evidence-v2` retains the manual one-shot flow from v1 and adds automatic display-recovery evidence. On a real `Off/Dimmed → On` transition, the native recovery state machine captures a hidden WebView/desktop baseline before showing the pet, then captures the presented desktop and records a fail-closed assessment. Only the latest capture is retained under the bounded log directory. Manual captures still require explicit ZIP inclusion; an automatic recovery incident is included when the user deliberately exports a diagnostic bundle, without requiring prior arming or a second checkbox.

`webview2-render-evidence-v3` retains v2 and adds `webview2-gpu-hidden-baseline` / `webview2-gpu-presented-probe` automatic incidents plus near-white opaque pixel counts and ratios. Both the hidden WebView baseline and presented virtual desktop can now be rejected for an almost-solid white surface as well as black. The capture's `recoveryAssessment` records baseline/presented black and white ratios and deltas. A safe presented probe means the shell kept running; an unsafe, failed, or timed-out GPU probe is fail-closed and should be correlated with `gpu-recovery-restart-requested`, `restart-blocked`, or `restart-disabled` in the runtime journal.

`webview2-render-evidence-v4` preserves older evidence for backward compatibility but
changes new GPU incidents to containment: the pet stays hidden, a baseline is
attempted with a bounded deadline, and the existing guarded restart is requested
without a presented probe. A normal GDI/CapturePreview image cannot establish
physical presentation health. `gpu-recovery-evidence-timeout` records unavailable
bounded evidence; `gpu-recovery-suppressed` with stage `gpu-context-invalidated`
precedes the restart outcome. Disabled/blocked restarts leave the pet hidden;
explicit user visibility changes cancel the pending recovery. Display-resume
policy is unchanged. Retained captures can predate the current build, so use the
capture and process journal identities, not capability v4 alone, to attribute an
individual historical incident. No extra screenshots or new retention are added.

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

Native crash summaries use structured metadata rather than raw panic messages, command lines, environment values or memory. Raw crash dumps are a separate opt-in and are not redacted: they may contain credentials, conversation text and other private process memory. An opted-in ZIP is not safe for public sharing merely because ordinary logs were redacted. Inventory streams raw entries only to verify integrity; it never prints their bytes. Extract only the incident's named dump into an agent-owned temporary directory when needed for a bounded diagnosis, then remove that temporary copy.

## Native Crash Evidence

`native-crash-evidence-v1` introduces required crash coverage and retained incident metadata. Use the producer-owned machine contract for exact archive entry patterns. Older bundles without this capability and without its entries are `not-collected`; absence is neither corruption nor proof that the application did not crash. A declared capability with missing coverage is a collection gap.

`native-crash-evidence-v2` (schema revision 22) adds `manifest.warnings` and the
same warning list to the desktop export result. Only the retained
`observer-and-process-ended-without-result` code is classified as a historical
evidence warning instead of a bundle collection failure. All other failure codes
retain their existing failure semantics. Coverage/session `failures` and incident
metadata are preserved unchanged; no missing exit result or dump is recovered by
this classification. The inspector separates `historicalGapCount` from
`failureCount`, and still marks native-crash evidence as partial when history is
incomplete. `outcome: complete` with warnings means the export completed, not that
all past incidents have complete evidence. V1 bundles retain their original
failure interpretation; absent `warnings` in older bundles means an empty list.

The coverage snapshot records the export's `rawDumpsRequested`, retained incident/raw-dump counts, per-session capture availability, failures and retention limits. `captureStatus: per-session` requires reading the retained sessions' actual state; it does not promise that every incident was captured. `unsupported-platform` does not establish Windows-style capture on macOS or Linux. Historical incidents retain their own `buildIdentity`, `sessionId`, `processId` and operating-system `processCreatedTicks`; never replace them with `diagnostics/application.json` from a later restart or upgrade.

Interpret events separately from process death:

- `native-exception` records an unhandled native exception; `exceptionCode` and `exitCode` have separate meanings.
- `rust-panic` records a panic observation. A caught panic may leave the application running; correlate a separate process-exit event before claiming death.
- `stack-overflow-observed` only proves the covered exception observer ran. Its dump result determines whether usable context was retained.
- `process-exit` records the observed process termination and an optional orderly reason. Native evidence is not overwritten by a later normal marker.
- `service-exit` preserves a product-owned service termination observed while the shell was still alive; it does not assert that the desktop also exited.
- `unexpected-end-unknown` records an incomplete previous lifecycle without inventing an exception code or stack.

Service entries bind a role/generation to a process creation identity. Their `order` distinguishes `service-first`, `shell-first`, `simultaneous` and `order-unknown`; temporal order alone does not prove causation. `fastFailCapture` and `forcedTerminationCapture` are exit-only coverage and do not promise an exception stack.

Each incident's `dump` declares status and, when included, `archiveEntry`, `sizeBytes` and `sha256`. `excluded` normally means retained and integrity-checked but not requested; with `errorCode: export-budget-exceeded`, the export did not finish verifying that dump and reports a collection failure. `included` means raw bytes were explicitly requested and exported. `pending`, `not-captured`, `failed`, `missing`, `corrupt` and `expired` each limit available evidence. Expiry does not invalidate a retained incident summary. A complete ZIP does not prove every prior crash had a dump or that a small dump contains every earlier heap write.

The inspector validates incident-to-entry identity, raw consent, size/hash and minidump magic without extracting memory. Missing references, unexpected raw entries, malformed metadata and wrong hashes are explicit gaps; raw data without consent is a privacy-contract violation. Integrity verification remains active when ordinary ZIP CRC checking is skipped. Bounds mirror the producer's current 100 incident metadata records, 5 dumps, 128 MiB per dump and 256 MiB total raw dump budget; metadata is read with a separate small limit.

For offline diagnosis, use `scripts/crash-diagnostics/run.*` with the exact private symbol archive for the incident build. PDB GUID/Age is the symbol binding key; archive file hashes establish archive integrity. Installer resource changes can change the whole EXE hash without changing code, so follow the tool's explicit code-section and binding checks. Missing symbols or mismatch are unresolved attribution, never permission to force-load an unrelated PDB. Product symbols/EXEs are developer-private release artifacts and are not expected inside a user diagnostic bundle. Do not fetch remote symbols automatically or display debugger memory/locals as part of inventory.

## Pet Render Evidence

`pet-render-evidence-v1` adds required `diagnostics/pet-render/coverage.json` and bounded `history-*.jsonl` / `incident-*.json`. Inventory reports counts, stable signals and gaps only; do not print event bodies. The default observer records structured runtime data, not pixels or chat. Histories and incidents have independent budgets; the latter preserve a bounded before/after window. Each record carries its own process session and build identity, so records retained across restart must not be rebound to the export-time process.

`recordedAt` is Unix milliseconds; `monotonicMs` and `recordSeq` are process-local ordering aids. Renderer `renders` counts CPU-side completion of screen-target render calls; it is neither GPU completion nor proof of successful desktop presentation. Ticker and page-timer counters are separate. `observation-gap` means the native observer stopped receiving expected samples, not a proven renderer deadlock. `render-progress-missing` is a progress signal, not a flicker root-cause verdict. Hide/pause/context-loss and dropped samples restrict interpretation.

A user mark is acknowledged as persisted only after the initial incident file is synced and published. `postWindowComplete: false` is expected while collecting the following ten seconds, on early exit, or when evidence remains incomplete. A timeout means acknowledgement is unknown; inspect retained files rather than inferring no write. Export reads bounded disk data without asking the pet page or forcing a writer flush. Coverage retains dropped/invalid/write failure/eviction counts, unknown tail completeness, missing artifacts and truncated tails. An old v1 bundle lacking this capability means not-collected, not corrupted. Raw system ETL/PresentMon files are not part of the default diagnostic ZIP.
