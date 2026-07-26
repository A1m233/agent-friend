from __future__ import annotations

import socket
from io import BytesIO

import agent.tools.builtin.web_fetch.pdf as pdf_module
import pytest
from agent.tools.builtin.web_fetch.pdf import extract_pdf_text
from agent.tools.builtin.web_fetch.provider import (
    WebContentEmptyError,
    WebContentResourceLimitError,
)
from agent.tools.builtin.web_fetch.url_safety import (
    UnsafeUrlError,
    resolve_redirect,
    validate_public_url,
)
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def test_blank_pdf_is_reported_as_no_text_layer() -> None:
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(stream)
    with pytest.raises(WebContentEmptyError):
        extract_pdf_text(stream.getvalue())


def test_text_layer_pdf_returns_page_markers() -> None:
    stream = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=100)
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
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 10 50 Td (Official release date) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(stream)
    text, page_count, skipped, truncated = extract_pdf_text(stream.getvalue(), focus="release date")
    assert "[page 1]" in text
    assert "Official release date" in text
    assert (page_count, skipped, truncated) == (1, 0, False)


def test_private_and_loopback_addresses_are_rejected() -> None:
    with pytest.raises(UnsafeUrlError):
        validate_public_url("http://127.0.0.1/file.pdf")
    with pytest.raises(UnsafeUrlError):
        validate_public_url("http://localhost/file.pdf")


def test_nonstandard_port_is_rejected_before_dns() -> None:
    with pytest.raises(UnsafeUrlError):
        validate_public_url("https://example.com:8443/file.pdf")


def test_mixed_public_and_private_dns_answers_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("10.0.0.2", 0)),
        ],
    )
    with pytest.raises(UnsafeUrlError):
        validate_public_url("https://example.com/file.pdf")


def test_redirect_target_is_resolved_and_revalidated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_dns(host: str, *_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        address = "10.0.0.2" if host == "internal.example" else "93.184.216.34"
        return [(2, 1, 6, "", (address, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)
    assert (
        resolve_redirect("https://example.com/a/start.pdf", "../next.pdf")
        == "https://example.com/next.pdf"
    )
    with pytest.raises(UnsafeUrlError):
        resolve_redirect("https://example.com/start.pdf", "https://internal.example/file.pdf")


def test_oversized_page_stream_is_reported_as_resource_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_module, "MAX_PAGE_STREAM_BYTES", 10)
    stream = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    content = DecodedStreamObject()
    content.set_data(b"BT (too much content) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(stream)
    with pytest.raises(WebContentResourceLimitError):
        extract_pdf_text(stream.getvalue())
