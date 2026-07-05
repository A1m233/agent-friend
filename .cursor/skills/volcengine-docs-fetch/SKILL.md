---
name: volcengine-docs-fetch
description: Fetch and summarize Volcengine documentation pages reliably. Use when the user gives a volcengine.com/docs URL, asks to read 火山引擎 / Volcengine docs, or needs official Volcengine documentation content where the static HTML may only contain the dynamic app shell.
---

# Volcengine Docs Fetch

## Workflow

Use the bundled script first for `https://www.volcengine.com/docs/<LibraryID>/<DocumentID>?lang=...` URLs:

```powershell
python .cursor/skills/volcengine-docs-fetch/scripts/fetch_volcengine_doc.py "https://www.volcengine.com/docs/6348/1756939?lang=zh"
```

The script extracts `LibraryID`, `DocumentID`, and `lang`, then calls Volcengine's document API:

```text
https://www.volcengine.com/api/doc/getDocDetail?LibraryID=<LibraryID>&DocumentID=<DocumentID>&lang=<lang>
```

It parses `Result.Content` JSON and concatenates `data.*.ops[].insert` into plain text. This avoids the common trap where the public docs page static HTML only returns the React app shell and no article body.

## Answering

- Cite the original docs URL in the final answer.
- Report the title, `LibraryID`, and `DocumentID` when useful for traceability.
- Summarize or quote only short excerpts; do not paste the full document unless explicitly needed and copyright limits allow it.
- If the API request fails, say that the official API fetch failed and then fall back to browser-rendered inspection or web search. Do not infer article content from the sidebar, title, or app-shell HTML alone.

## Script Notes

- Use `--format json` when downstream processing needs metadata and text separately.
- Use `--max-chars N` for previews; omit it for full extracted text.
- Use `--dry-run` to validate URL parsing and API URL construction without network access.
