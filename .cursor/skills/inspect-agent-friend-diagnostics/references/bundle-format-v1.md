# Agent-Friend Diagnostic Bundle Format v1

Use this reference to interpret bundles whose `manifest.json` has `formatVersion: 1`. The recorded build commit remains authoritative when an older or branch-specific producer differs from the current source.

## Producer Flow

The settings feedback panel invokes the Tauri `generate_diagnostic_bundle` command with an optional problem description, selected session ids, locale, timezone, and an attachment-originals flag. Rust then:

1. snapshots application, environment, runtime-service, and runtime-config state;
2. fetches each selected session from the local bridge and removes `active_events`;
3. derives attachment candidates from selected-session `user_message` events;
4. validates attachment metadata, blob size, and SHA-256 before optionally including originals;
5. collects allowlisted logs from `paths::log_dir()`;
6. redacts managed credentials and replaces the user home path;
7. writes a `.zip.part` file in Downloads and atomically renames it after closing and syncing the ZIP.

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
| `diagnostics/attachments.json` | Attachment references, integrity checks, and original inclusion status | Candidates come only from included sessions |
| `sessions/session-NNN.json` | Selected session projection with ordered events | Complete private content may be present; `active_events` is removed |
| `logs/<allowlisted-name>` | Bounded log content after redaction | Per file 5 MiB tail; total 20 MiB |
| `attachments/NNN-<name>` | Optional verified attachment original | Total originals capped at 200 MiB |

## Manifest Semantics

- `outcome: complete`: no collector added a failure.
- `outcome: partial`: a non-fatal collection failed; the ZIP is still usable.
- `requestedSessionIds` versus `includedSessionIds`: establish whether selected session fetches succeeded.
- `failures`: preserve collection, item, and message; do not collapse these into a generic warning.
- `excluded`: currently calls out the memory database, persona assets, raw settings, full environment, and unselected sessions.
- `logs[].tailStartOffset > 0`: earlier log content is absent. Absence before that offset is not evidence.
- `logs[].includedBytes` is measured after text decoding/redaction and may differ from the raw byte span.
- Attachment summary reports requested originals, item count, estimated/included/skipped/failed bytes, and the 200 MiB cap.

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

## Redaction and Privacy Boundary

The producer constructs diagnostic JSON from allowlisted fields, recursively replaces sensitive-key values, replaces configured credential values in text, redacts lines containing common authorization/credential markers, and replaces the user's home path with `<USER_HOME>`.

This is credential protection, not general content review. Selected sessions, problem text, tool arguments/results, log messages, filenames, and attachment originals may still contain private or unknown-sensitive data. Read and report only the slice required by the task.
