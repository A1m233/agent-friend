# Public-source privacy cleanup

This is a record of privacy edits prepared on 2026-10-29 against
`635b81079d4ca9b6ae5b6aba3055b8ef9ea0e87e`. It is not a publication approval.

- Removed account credentials, application IDs, and the account-specific endpoint
  from the legacy voice demo configuration and its tracked textual references.
- Moved personal interview preparation out of the source tree.
- Replaced personal home-directory names and personal display names with examples.
- Replaced real QQ conversation identifiers consistently across their references.
- Removed production session/event identifiers from image-fixture documentation
  and gave the attachment a neutral filename. The image pixels are unchanged.

Historical reports and conversation excerpts containing these identifiers are now
privacy-edited copies. Their original run dates, results, and source/build hashes
still describe the original runs; privacy editing is not a rerun and does not
establish a new exact-byte reproduction. Original copies and the replacement map
are retained locally outside this repository for private investigation.

The existing image fixture still contains third-party video-page content. Renaming
it does not establish redistribution permission or remove third-party information
visible in the screenshot.

These edits sanitize the current source files. Old Git revisions still contain
the original values until a separate history cleanup is reviewed and applied.
Credential revocation status is outside this file cleanup. No credential was
tested against its provider during cleanup.

Git author/committer email addresses are a separate publication surface. The
local history preview does not yet establish that those identities are suitable
for publication; the owner's personal email also needs a verified public
identity mapping before any rewritten history is published.
