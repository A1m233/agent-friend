"""Read PE/CodeView, MSF7 PDB identity and minidump metadata without loading code.

Inputs are untrusted binary artifacts. Every RVA/length is checked before use;
paths embedded in a dump or CodeView record are never opened by this module.
"""

from __future__ import annotations

import hashlib
import struct
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any

MAX_ARTIFACT_BYTES = 512 * 1024 * 1024


class BinaryFormatError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data

    @classmethod
    def from_file(cls, path: Path) -> Reader:
        with path.open("rb") as stream:
            data = stream.read(MAX_ARTIFACT_BYTES + 1)
        if len(data) > MAX_ARTIFACT_BYTES:
            raise BinaryFormatError("artifact exceeds parser size limit")
        return cls(data)

    def bytes(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise BinaryFormatError("binary range outside artifact")
        return self.data[offset : offset + size]

    def unpack(self, fmt: str, offset: int) -> tuple[Any, ...]:
        return struct.unpack("<" + fmt, self.bytes(offset, struct.calcsize("<" + fmt)))

    def u32(self, offset: int) -> int:
        return int(self.unpack("I", offset)[0])


def codeview(data: bytes) -> dict[str, Any]:
    if len(data) < 25 or data[:4] != b"RSDS":
        raise BinaryFormatError("unsupported or missing RSDS CodeView record")
    tail = data[24:]
    if b"\0" not in tail or len(tail) > 32768:
        raise BinaryFormatError("invalid PDB name")
    name = PureWindowsPath(tail.split(b"\0", 1)[0].decode("utf-8", errors="replace")).name
    if not name or name in {".", ".."} or ":" in name:
        raise BinaryFormatError("invalid PDB basename")
    return {
        "format": "RSDS",
        "guid": str(uuid.UUID(bytes_le=data[4:20])),
        "age": struct.unpack_from("<I", data, 20)[0],
        "pdbName": name,
    }


class PeImage:
    def __init__(self, path: Path) -> None:
        self.reader = Reader.from_file(path)
        r = self.reader
        if r.bytes(0, 2) != b"MZ":
            raise BinaryFormatError("missing DOS signature")
        pe = r.u32(0x3C)
        if r.bytes(pe, 4) != b"PE\0\0":
            raise BinaryFormatError("missing PE signature")
        machine, count, timestamp, _, _, optional_size, _ = r.unpack("HHIIIHH", pe + 4)
        optional = pe + 24
        magic = r.unpack("H", optional)[0]
        if magic not in {0x10B, 0x20B} or not 1 <= count <= 96:
            raise BinaryFormatError("unsupported PE layout")
        directory_offset = 112 if magic == 0x20B else 96
        if optional_size < directory_offset + 8 * 7:
            raise BinaryFormatError("PE data directories missing")
        self.machine = machine
        self.timestamp = timestamp
        self.size_of_image = r.u32(optional + 56)
        self.headers_size = r.u32(optional + 60)
        if self.size_of_image > MAX_ARTIFACT_BYTES or self.headers_size > self.size_of_image:
            raise BinaryFormatError("invalid PE image size")
        self.sections: list[dict[str, Any]] = []
        for index in range(count):
            offset = optional + optional_size + index * 40
            name = r.bytes(offset, 8).split(b"\0", 1)[0].decode("ascii", errors="replace")
            virtual_size, virtual_address, raw_size, raw_offset = r.unpack("IIII", offset + 8)
            flags = r.u32(offset + 36)
            if virtual_address + max(raw_size, virtual_size) > self.size_of_image:
                raise BinaryFormatError("PE section outside image")
            raw = r.bytes(raw_offset, raw_size)
            self.sections.append(
                dict(
                    name=name,
                    virtualAddress=virtual_address,
                    virtualSize=virtual_size,
                    rawSize=raw_size,
                    rawOffset=raw_offset,
                    executable=bool(flags & 0x20000000),
                    sha256=hashlib.sha256(raw).hexdigest(),
                )
            )
        debug_rva, debug_size = r.unpack("II", optional + directory_offset + 6 * 8)
        if debug_size % 28 or debug_size > 28 * 256:
            raise BinaryFormatError("invalid debug directory")
        identities = []
        for offset in range(0, debug_size, 28):
            entry = self.rva_bytes(debug_rva + offset, 28)
            kind, size, _, file_offset = struct.unpack_from("<IIII", entry, 12)
            if kind == 2:
                identities.append(codeview(r.bytes(file_offset, size)))
        if len(identities) != 1:
            raise BinaryFormatError("exactly one RSDS binding required")
        self.codeview = identities[0]
        self.exception_rva, self.exception_size = r.unpack(
            "II", optional + directory_offset + 3 * 8
        )

    def rva_bytes(self, rva: int, size: int) -> bytes:
        if rva >= 0 and rva + size <= self.headers_size:
            return self.reader.bytes(rva, size)
        for s in self.sections:
            delta = rva - s["virtualAddress"]
            if delta >= 0 and delta + size <= s["rawSize"]:
                return self.reader.bytes(s["rawOffset"] + delta, size)
        raise BinaryFormatError("RVA has no file-backed data")

    def metadata(self) -> dict[str, Any]:
        return {
            "machine": self.machine,
            "timeDateStamp": self.timestamp,
            "sizeOfImage": self.size_of_image,
            "codeView": self.codeview,
            "codeSections": [
                {k: s[k] for k in ("name", "virtualAddress", "rawSize", "sha256")}
                for s in self.sections
                if s["executable"]
            ],
        }


def pdb_identity(path: Path) -> dict[str, Any]:
    r = Reader.from_file(path)
    if r.bytes(0, 32) != b"Microsoft C/C++ MSF 7.00\r\n\x1aDS\0\0\0":
        raise BinaryFormatError("unsupported PDB format (MSF7 required)")
    page_size, _, pages, directory_size, _, block_map = r.unpack("IIIIII", 32)
    if page_size not in {512, 1024, 2048, 4096, 8192} or pages * page_size != len(r.data):
        raise BinaryFormatError("invalid PDB pages")
    if directory_size > len(r.data):
        raise BinaryFormatError("invalid PDB directory size")
    directory_pages = (directory_size + page_size - 1) // page_size
    if directory_pages * 4 > page_size:
        raise BinaryFormatError("unsupported multi-page PDB block map")
    blocks = r.unpack("I" * directory_pages, block_map * page_size)
    directory = Reader(b"".join(r.bytes(p * page_size, page_size) for p in blocks)[:directory_size])
    count = directory.u32(0)
    if not 2 <= count <= 65536:
        raise BinaryFormatError("invalid PDB stream count")
    sizes = directory.unpack("I" * count, 4)
    cursor = 4 + 4 * count
    for index, size in enumerate(sizes):
        if size == 0xFFFFFFFF:
            if index == 1:
                raise BinaryFormatError("missing PDB identity stream")
            continue
        if size > len(r.data):
            raise BinaryFormatError("PDB stream outside file")
        n = (size + page_size - 1) // page_size
        blocks = directory.unpack("I" * n, cursor)
        cursor += n * 4
        if index == 1:
            stream = Reader(b"".join(r.bytes(p * page_size, page_size) for p in blocks)[:size])
            return {"guid": str(uuid.UUID(bytes_le=stream.bytes(12, 16))), "age": stream.u32(8)}
    raise BinaryFormatError("missing PDB identity")


class Minidump:
    def __init__(self, path: Path) -> None:
        r = self.reader = Reader.from_file(path)
        if r.bytes(0, 4) != b"MDMP":
            raise BinaryFormatError("missing minidump signature")
        count, offset = r.unpack("II", 8)
        if count > 128:
            raise BinaryFormatError("too many minidump streams")
        self.streams: dict[int, tuple[int, int]] = {}
        for i in range(count):
            kind, size, position = r.unpack("III", offset + i * 12)
            r.bytes(position, size)
            if kind:
                if kind in self.streams:
                    raise BinaryFormatError("duplicate minidump stream")
                self.streams[kind] = (position, size)
        self.modules: list[dict[str, Any]] = []
        position, size = self.streams.get(4, (0, 0))
        if size < 4 or r.u32(position) > (size - 4) // 108:
            raise BinaryFormatError("invalid minidump modules")
        for i in range(r.u32(position)):
            off = position + 4 + i * 108
            base, image_size, _, timestamp, name = r.unpack("QIIII", off)
            name_size = r.u32(name)
            if name_size > 32768 or name_size % 2:
                raise BinaryFormatError("invalid module name length")
            basename = PureWindowsPath(r.bytes(name + 4, name_size).decode("utf-16le")).name
            cv_size, cv_offset = r.unpack("II", off + 76)
            cv = codeview(r.bytes(cv_offset, cv_size)) if cv_size else None
            self.modules.append(
                dict(
                    name=basename,
                    base=base,
                    sizeOfImage=image_size,
                    timeDateStamp=timestamp,
                    codeView=cv,
                )
            )
        off, size = self.streams.get(6, (0, 0))
        self.exception = None
        self.context = b""
        if size:
            if size < 168:
                raise BinaryFormatError("truncated exception stream")
            context_size, context_offset = r.unpack("II", off + 160)
            self.context = r.bytes(context_offset, context_size)
            address = int(r.unpack("Q", off + 24)[0])
            module = self.module_at(address)
            self.exception = {
                "threadId": r.u32(off),
                "code": r.u32(off + 8),
                "address": address,
                "module": module["name"] if module else None,
                "rva": address - module["base"] if module else None,
            }
        off, size = self.streams.get(7, (0, 0))
        self.architecture = r.unpack("H", off)[0] if size >= 2 else None
        self.memory: list[tuple[int, int, int]] = []
        off, size = self.streams.get(5, (0, 0))
        if size:
            count = r.u32(off)
            if count > (size - 4) // 16:
                raise BinaryFormatError("invalid memory list")
            for i in range(count):
                base, length, position = r.unpack("QII", off + 4 + i * 16)
                r.bytes(position, length)
                self.memory.append((base, length, position))
        off, size = self.streams.get(9, (0, 0))
        if size:
            count, position = r.unpack("QQ", off)
            if count > (size - 16) // 16:
                raise BinaryFormatError("invalid memory64 list")
            for i in range(count):
                base, length = r.unpack("QQ", off + 16 + i * 16)
                r.bytes(position, length)
                self.memory.append((base, length, position))
                position += length

    def module_at(self, address: int) -> dict[str, Any] | None:
        return next(
            (m for m in self.modules if m["base"] <= address < m["base"] + m["sizeOfImage"]),
            None,
        )

    def read_memory(self, address: int, size: int) -> bytes:
        for base, length, position in self.memory:
            if base <= address < base + length:
                return self.reader.bytes(
                    position + address - base, min(size, base + length - address)
                )
        return b""

    def metadata(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "architecture": self.architecture,
            "exception": self.exception,
            "modules": self.modules,
            "memoryRangeCount": len(self.memory),
        }
