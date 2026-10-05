"""Bounded CPA effort/tool-image smoke and deterministic document-reference checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID

import httpx
import yaml
from agent.attachments.input_projection import project_file_message
from agent_bridge.model_connections.inspection import error_code
from llm_providers.catalog import ModelRuntimeConfig
from llm_providers.connections import (
    ConnectionDefinition,
    CustomModel,
    ModelDirectory,
    model_reference,
)
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from agent import AttachmentRef, Message
from llm_providers import LLMClient, ModelCallControl, ModelRequest, MultimodalInputAdapter

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:8317/v1"


def image_fixture() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
        )

    row = b"\0" + b"\0\0\xff" * 50 + b"\xff\0\0" * 50
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!2I5B", 100, 80, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * 80))
        + chunk(b"IEND", b"")
    )


def pdf_fixture() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 18 Tf 20 120 Td (violet-246) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-llm", action="store_true")
    parser.add_argument(
        "--protocol", choices=["responses", "chat_completions"], default="responses"
    )
    parser.add_argument(
        "--extended", action="store_true", help="five additional fixture/default cases"
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.allow_llm:
        parser.error("requires --allow-llm; at most 3 generations")
    if args.output.exists():
        parser.error("output already exists")
    key = yaml.safe_load(
        (Path.home() / ".local/share/cliproxyapi/config.yaml").read_text(encoding="utf-8")
    )["api-keys"][0]
    connection = ConnectionDefinition(
        str(UUID(int=1)),
        "CPA smoke",
        BASE,
        args.protocol,
        (CustomModel(str(UUID(int=2)), "gpt-5.6-luna", 100000),),
    )
    directory = ModelDirectory([connection])
    spec = replace(
        directory.build_spec(
            ModelRuntimeConfig(
                model_reference(connection.id, connection.models[0].id), "effort:low"
            ),
            api_key=key,
        ),
        defaults={"timeout": 60, "reasoning_effort": "low"},
    )
    client = LLMClient(spec, network_retries=0, engine_overload_retries=0)
    report: dict[str, Any] = {
        "requests": [],
        "cases": [],
        "source_hashes": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / "llm_providers/src/llm_providers").rglob("*.py")
        },
    }
    original = httpx.AsyncHTTPTransport.handle_async_request
    sync_original = httpx.HTTPTransport.handle_request

    def observe(request: httpx.Request) -> None:
        if (
            str(request.url)
            != BASE + ("/responses" if args.protocol == "responses" else "/chat/completions")
            or request.method != "POST"
            or len(report["requests"]) >= (5 if args.extended else 3)
        ):
            raise RuntimeError("smoke request budget or endpoint exceeded")
        body = json.loads(request.content)
        report["requests"].append(
            {
                "endpoint": str(request.url),
                "model": body.get("model"),
                "reasoning": body.get("reasoning", body.get("reasoning_effort")),
                "types": [
                    part.get("type")
                    for item in body.get("input", body.get("messages", []))
                    for part in item.get("content", [])
                    if isinstance(part, dict)
                ],
                "body_sha256": hashlib.sha256(request.content).hexdigest(),
            }
        )

    async def send(transport: Any, request: httpx.Request) -> httpx.Response:
        observe(request)
        return await original(transport, request)

    def sync_send(transport: Any, request: httpx.Request) -> httpx.Response:
        observe(request)
        return sync_original(transport, request)

    with (
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", send),
        patch.object(httpx.HTTPTransport, "handle_request", sync_send),
    ):
        cases = [
            ("effort", None, "Reply only OK."),
            (
                "image",
                (image_fixture(), "image/png", "image"),
                "Name the left and right colors in this image, in English.",
            ),
            (
                "pdf",
                (pdf_fixture(), "application/pdf", "document"),
                "Return only the code printed in the attached PDF.",
            ),
        ]
        if args.extended:
            folder = Path(__file__).parent / "fixtures"
            cases = [
                ("effort_default", None, "Reply only OK."),
                (
                    "jpeg",
                    ((folder / "colors.jpg").read_bytes(), "image/jpeg", "image"),
                    "Name the left and right colors in this image, in English.",
                ),
                (
                    "webp",
                    ((folder / "colors.webp").read_bytes(), "image/webp", "image"),
                    "Name the left and right colors in this image, in English.",
                ),
                (
                    "scan",
                    ((folder / "scan.pdf").read_bytes(), "application/pdf", "document"),
                    "Return only the code visible in this PDF.",
                ),
                (
                    "txt",
                    ((folder / "note.txt").read_bytes(), "text/plain", "document"),
                    "Return only the archive code in the attachment.",
                ),
            ]
        for name, fixture, prompt in cases:
            if name == "effort_default":
                client = LLMClient(
                    replace(spec, defaults={"timeout": 60}),
                    network_retries=0,
                    engine_overload_retries=0,
                )
            else:
                client = LLMClient(spec, network_retries=0, engine_overload_retries=0)
            messages = [{"role": "user", "content": prompt}]
            if fixture:
                data, media, kind = fixture
                adapter = MultimodalInputAdapter(open_asset=lambda _, data=data: BytesIO(data))
                attachment = AttachmentRef(
                    asset_id=hashlib.sha256(data).hexdigest(),
                    sha256=hashlib.sha256(data).hexdigest(),
                    display_name="fixture",
                    media_type=media,
                    kind=kind,
                    size_bytes=len(data),
                )
                if kind == "document":
                    projected = project_file_message(
                        Message(role="user", content=prompt, attachments=[attachment])
                    )
                    report["cases"].append(
                        {
                            "name": name,
                            "passed": not projected.attachments,
                            "scope": "deterministic file-reference projection; no generation",
                        }
                    )
                    continue
                messages = [
                    {"role": "user", "content": prompt},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "fixture-image",
                                "type": "function",
                                "function": {"name": "view_image", "arguments": "{}"},
                            }
                        ],
                    },
                    adapter.render_message(
                        Message(
                            role="tool",
                            content="synthetic fixture image",
                            images=(attachment,),
                            meta={"tool_call_id": "fixture-image"},
                        ),
                        client,
                    ),
                ]
            try:
                result = client.generate(
                    ModelRequest(
                        messages=messages,
                        settings={"max_tokens": 4096},
                        control=ModelCallControl(allow_retries=False),
                    )
                )
                answer = result.text.strip()
                expected = {
                    "effort": "OK",
                    "effort_default": "OK",
                    "pdf": "violet-246",
                    "scan": "amber-583",
                    "txt": "silver-917",
                }
                passed = (
                    ("blue" in answer.lower() and "red" in answer.lower())
                    if name in {"image", "jpeg", "webp"}
                    else answer == expected[name]
                )
                report["cases"].append({"name": name, "passed": passed, "answer": answer})
            except Exception as error:
                report["cases"].append(
                    {
                        "name": name,
                        "passed": False,
                        "error": error_code(error),
                        "type": type(error).__name__,
                    }
                )
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    print(json.dumps({"cases": report["cases"], "requests": len(report["requests"])}))
    return 0 if all(c["passed"] for c in report["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
