#!/usr/bin/env python3
"""Fetch Volcengine docs through the doc detail API."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

DOCS_RE = re.compile(r"^/docs/(?P<library_id>\d+)/(?P<document_id>\d+)")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


@dataclass(frozen=True)
class DocRef:
    original_url: str
    library_id: str
    document_id: str
    lang: str

    @property
    def api_url(self) -> str:
        query = urlencode(
            {
                "LibraryID": self.library_id,
                "DocumentID": self.document_id,
                "lang": self.lang,
            }
        )
        return f"https://www.volcengine.com/api/doc/getDocDetail?{query}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Volcengine docs URL")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--max-chars", type=int, default=0, help="0 means no truncation")
    parser.add_argument("--dry-run", action="store_true", help="Only parse the URL")
    args = parser.parse_args()

    doc_ref = parse_doc_ref(args.url)
    if args.dry_run:
        print_json(
            {
                "url": doc_ref.original_url,
                "library_id": doc_ref.library_id,
                "document_id": doc_ref.document_id,
                "lang": doc_ref.lang,
                "api_url": doc_ref.api_url,
            }
        )
        return 0

    result = fetch_doc(doc_ref)
    text = extract_text(result.get("Content", ""))
    if args.max_chars > 0 and len(text) > args.max_chars:
        text = text[: args.max_chars] + "\n...[truncated]"

    payload = {
        "url": doc_ref.original_url,
        "api_url": doc_ref.api_url,
        "library_id": result.get("LibraryID") or doc_ref.library_id,
        "document_id": result.get("DocumentID") or doc_ref.document_id,
        "title": result.get("Title", ""),
        "document_code": result.get("DocumentCode", ""),
        "text": text,
    }

    if args.format == "json":
        print_json(payload)
        return 0

    print(f"Title: {payload['title']}")
    print(f"URL: {payload['url']}")
    print(f"API: {payload['api_url']}")
    print(f"LibraryID: {payload['library_id']}")
    print(f"DocumentID: {payload['document_id']}")
    print()
    print(payload["text"])
    return 0


def parse_doc_ref(url: str) -> DocRef:
    parsed = urlparse(url)
    if parsed.netloc not in {"www.volcengine.com", "volcengine.com"}:
        raise SystemExit(f"not a Volcengine docs URL: {url}")
    match = DOCS_RE.match(parsed.path)
    if not match:
        raise SystemExit(f"unsupported Volcengine docs URL path: {parsed.path}")
    query = parse_qs(parsed.query)
    lang = query.get("lang", ["zh"])[0] or "zh"
    return DocRef(
        original_url=url,
        library_id=match.group("library_id"),
        document_id=match.group("document_id"),
        lang=lang,
    )


def fetch_doc(doc_ref: DocRef) -> dict[str, Any]:
    request = Request(
        doc_ref.api_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": doc_ref.original_url,
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
    payload = json.loads(raw)
    error = payload.get("ResponseMetadata", {}).get("Error")
    if error:
        raise SystemExit(f"Volcengine API error: {error}")
    result = payload.get("Result")
    if not isinstance(result, dict):
        raise SystemExit("Volcengine API response did not contain Result")
    return result


def extract_text(content: Any) -> str:
    if not isinstance(content, str):
        return ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content

    data = parsed.get("data") if isinstance(parsed, dict) else None
    if not isinstance(data, dict):
        return content

    pieces: list[str] = []
    for key in sorted(data, key=sort_key):
        block = data.get(key)
        if not isinstance(block, dict):
            continue
        ops = block.get("ops")
        if not isinstance(ops, list):
            continue
        for op in ops:
            if not isinstance(op, dict):
                continue
            insert = op.get("insert")
            if isinstance(insert, str):
                pieces.append(insert)
    return "".join(pieces).strip()


def sort_key(value: str) -> tuple[int, str]:
    return (0, f"{int(value):08d}") if value.isdigit() else (1, value)


def print_json(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    raise SystemExit(main())
