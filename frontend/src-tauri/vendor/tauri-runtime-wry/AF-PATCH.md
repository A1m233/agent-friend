# Windows owner-thread patch (requirement 156)

Base: published `tauri-runtime-wry 2.11.2`, registry archive SHA-256
`b83849ee63ecb27a8e8d0fe51915ca215076914aca43f96db1179f0f415f6cd9`.
Upstream source commit: `499df79be65ef8c0670abc0207cd9e37b55d8491`.
The original manifest is retained in `Cargo.toml.orig`; upstream MIT and Apache
licenses are included. No other Tauri/Tao/Wry dependency is upgraded by this patch.

`Context::clone` previously cloned `DispatcherMainThreadContext`, including Tao's
non-atomic Windows event-loop `Rc`. Window, webview, runtime and application
handles can be cloned/dropped on worker threads. Moving one application caller to
the UI thread does not protect the other callers or framework IPC callbacks.

On Windows, `Wry` now owns a non-Send guard. A thread-local registry stores a weak
reference to its complete dispatcher context; cross-thread handles own only a
thread ID, unique key and atomic liveness token. Token clone/drop/Debug never
accesses native state. Upgrading the token is owner-thread-only and yields an
Rc-backed non-Send borrow. Registry borrows end before native callbacks run.
Removing an owner revokes new borrows before dropping its payload; an outstanding
reentrant borrow keeps the payload alive until that owner-thread borrow finishes.
The native event loop field is destroyed before the owner field.

Windows dispatcher context no longer claims unsafe Send/Sync. Tracing state is
inside the same ownership boundary. Windows monitor queries use the existing
event-loop message mechanism. Windows display handles contain no platform
connection pointer and do not borrow a temporary owner. Stale handles return
closed/unavailable outcomes; non-Windows state ownership remains upstream's.

The implementation differs deliberately from upstream PR 15411, which separates
only window_target and has an unresolved borrowed display-handle lifetime. Tao
PR 1290's Rc-to-Arc substitution was not adopted: it leaves native ownership and
the thread of the last destructor unspecified.

Validation entry: `scripts/tauri-runtime-regression/run.{sh,ps1}`.
`unit` checks actual Cargo resolution, then compiles/tests default and tracing
features. `stress` runs each native lifecycle scenario in an isolated process,
without WebViews, application settings, sessions or LLM requests. Passing stress
does not prove that every cause of long-running application crashes is resolved.

Remove this patch only after a released upstream dependency passes the ownership,
teardown, feature and application regression checks. Do not silently fall back to
the registry crate during dependency updates.

`desktop-stress --installer <governed-installer> --candidate-build-id <exact-id>
--artifacts <new-directory> [--duration-seconds 1800]` observes an installed isolated
candidate. It reuses installer-smoke verification/install/cleanup helpers, refuses
existing isolated state or credentials, checks runtime and WebView identities,
and records native cursor/monitor queries, reloads, passthrough acknowledgments
and controlled native teardown. Physical click-through and mixed-DPI dragging
remain separately labelled manual checks. It never takes a full desktop screenshot.
