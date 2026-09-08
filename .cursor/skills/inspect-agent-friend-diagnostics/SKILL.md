---
name: inspect-agent-friend-diagnostics
description: Safely inspect and interpret agent-friend local diagnostic ZIP bundles. Use in the agent-friend repository whenever the user provides an `agent-friend-diagnostics-*.zip`, asks what a diagnostic bundle contains or whether it is complete, or asks to diagnose an agent-friend symptom using a generated diagnostic package. Use alongside `diagnose-bug` for root-cause investigation; this skill owns bundle validation, build-to-source binding, format knowledge, privacy boundaries, and agent-friend-specific evidence routing, not generic diagnosis or fixes.
---

# Inspect Agent-Friend Diagnostics

Interpret an agent-friend diagnostic bundle as a versioned runtime evidence snapshot. Treat `frontend/src-tauri/src/diagnostics/bundle-contract-v1.json` as the machine-readable structural authority for the current producer; this skill owns safe consumer interpretation, evidence routing, and privacy semantics. Keep generic hypothesis ranking and confidence semantics under `diagnose-bug`; keep changes and fixes outside this read-only skill.

## Start Safely

1. Confirm the current repository is agent-friend using its git remote, falling back to the repository root name only when no remote is configured. Stop applying this project skill when the repository does not match.
2. Treat the ZIP as private user data. It may contain complete selected conversations, tool arguments/results, user-written problem text, and attachment originals.
3. Do not bulk-print session bodies, logs, or attachment contents. Do not extract the archive into the repository.
4. Run the bundled inspector before manually reading entries:

   ```powershell
   py .cursor/skills/inspect-agent-friend-diagnostics/scripts/inspect_bundle.py <bundle.zip>
   ```

   On macOS/Linux, use `python3`. Add `--json` for machine-readable output or `--skip-crc` only when full CRC verification is disproportionately expensive.

The inspector reads the archive in place, rejects unsafe entry names, validates the known structure, and emits metadata and counts without conversation or log bodies.

## Bind Runtime Evidence to Source

Read `diagnostics/application.json` before explaining behavior. Prefer `packageBuild.source.commit` as the runtime code identity.

- Verify the commit object with `git cat-file -e <commit>^{commit}`.
- Inspect matching source with `git show <commit>:<path>`; do not switch the user's checkout merely to inspect it.
- Treat `packageBuild.source.dirty: true` as an exact-source gap because uncommitted build inputs are unknown.
- If the commit is absent locally, distinguish bundle/runtime facts from explanations based only on the current checkout.
- Treat `formatVersion` as the breaking format boundary, `schemaRevision` as the additive producer revision, and `capabilities` as the list of evidence families the producer claims to emit.
- If the inspector reports `unhandledCapabilities`, record a consumer-coverage gap and inspect the producer at the recorded build commit before interpreting those artifacts. Do not silently treat an unknown capability as irrelevant.
- Do not assume `formatVersion: 1` means every v1 bundle has identical optional fields; older v1 bundles may predate `schemaRevision` and `capabilities`.

## Establish Evidence Coverage

Read [references/bundle-format-v1.md](references/bundle-format-v1.md) when `manifest.formatVersion` is `1`. For an unknown version, use only fields proven by the bundle and inspect the producer at the recorded build commit before assigning semantics.

Before diagnosing, establish:

- archive validity, duplicate/unsafe names, and CRC result;
- format version, schema revision, declared capabilities, and unhandled capabilities;
- manifest outcome and every collection failure;
- requested versus included sessions;
- generated time, timezone, application version, distribution, build mode, commit, and dirty state;
- log source, modification time, original size, tail start offset, and included size;
- attachment request, inclusion status, integrity flags, and byte limits;
- selected-session provider reasoning state count, redaction count, and any v2 payload-leak contract violation; never print the payload;
- Windows Shell current snapshot and journal coverage, including dropped/truncated event counts and referenced-artifact gaps;
- pet input evidence coverage, retained occurrence identities, independent incident retention and durability gaps; never print coordinate samples or input-event bodies in an inventory;
- WebView2 render-evidence consent, manual/display/GPU recovery trigger and timing, internal-versus-desktop black/white pixel metrics, included/missing screenshot artifacts, and capture/recovery errors;
- pet render evidence coverage, independent incident retention, per-record process/build identity and CPU-render versus actual-presentation limits; see the v1 reference;
- explicit exclusions.
- managed execution environment provider status, runtime/version identity, overlay/cache size buckets, payload validation and recent stable error codes; never expect session ids, paths, package names, scripts or command output in this evidence family.

`outcome: complete` means the configured collectors reported no failure. It does not prove that the user selected the relevant session, that the retained log tail covers the occurrence, or that an excluded source was unnecessary.

## Correlate the Smallest Relevant Slice

1. Use `problem.txt` only to frame the reported symptom; corroborate it with runtime evidence.
2. Locate the relevant selected session and reconstruct its event order. Account for `turn_rewrite`, `compaction`, tool request/result pairs, model/persona metadata, and attachment references.
3. Start correlation with exact identifiers such as session id, event UUID, run id, tool call id, trace id, or attachment asset id. Then inspect a narrow surrounding time/event window.
4. Route evidence to the correct log family using the v1 reference. Compare application-owned logs with packaged stdout/stderr capture when both exist.
5. For Windows taskbar, fullscreen, focus, or overlay symptoms, start with the summarized Windows Shell journal. Correlate foreground/move-size/minimize/reorder events with snapshots, and treat nonzero dropped/truncated counts as explicit timeline gaps. Read individual journal records only within the smallest relevant time window.
   For pet hover/click/drag passthrough symptoms, start with `petInputEvidence` and the v1 reference's Pet Input Evidence section. Bind historical records to their own process/page/build identities, not the export-time application snapshot. Correlate request, sample, DPR revision and actual apply identifiers before reading the smallest relevant event slice; a missing acknowledgement is not a failed native operation.
6. Use the source tree at the recorded build commit to explain the first known-wrong boundary. Treat the current checkout as comparison evidence only.
7. Stop expanding when the evidence distinguishes the live hypotheses. Preserve disconfirming evidence and coverage gaps.

Open an attachment original only when its content is necessary to evaluate the symptom. Extract only the referenced entry to an agent-created temporary directory, inspect it, and remove that temporary directory afterward. Never treat `processingStatus: notRecorded` as proof that downstream processing succeeded or failed.

## Report

When only inventorying the bundle, report structure, completeness, runtime identity, and evidence gaps without forcing a root cause.

When diagnosing with `diagnose-bug`, report:

1. the runtime occurrence and exact build identity;
2. the decisive session/log/attachment evidence chain;
3. collector coverage and exclusions;
4. root cause or best explanation with calibrated confidence;
5. rejected alternatives and remaining evidence gaps;
6. minimal fix direction without editing.

Do not expose credentials or unnecessary private content in the report. Do not implement a fix unless the user separately authorizes code changes.
