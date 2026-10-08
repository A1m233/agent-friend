"""Offline AMD64 stack walking using the system DbgHelp; never attaches to a process."""

from __future__ import annotations

import bisect
import ctypes as c
import os
import re
import struct
from pathlib import Path, PureWindowsPath
from typing import Any

from .formats import BinaryFormatError, Minidump, PeImage


def display_symbol(raw: str) -> str:
    """Render legacy Rust names returned by DbgHelp; preserve unknown/v0 encodings."""
    match = re.fullmatch(r"_?ZN(.*)E(?:\.llvm\..*)?", raw)
    if not match:
        return raw
    rest = match[1]
    parts = []
    while rest:
        length = re.match(r"\d+", rest)
        if not length:
            return raw
        count = int(length[0])
        rest = rest[len(length[0]) :]
        if not 0 < count <= len(rest):
            return raw
        parts.append(rest[:count])
        rest = rest[count:]
    if not parts or not re.fullmatch(r"h[0-9a-f]{16}", parts[-1]):
        return raw
    value = "::".join(parts[:-1]).replace("..", "::")
    for encoded, decoded in {
        "LT": "<",
        "GT": ">",
        "SP": "@",
        "BP": "*",
        "RF": "&",
        "LP": "(",
        "RP": ")",
        "C": ",",
    }.items():
        value = value.replace(f"${encoded}$", decoded)
    return re.sub(
        r"\$u([0-9a-f]{2,6})\$",
        lambda m: chr(int(m[1], 16)) if int(m[1], 16) <= 0x10FFFF else m[0],
        value,
    )


def walk(dump: Minidump, image_path: Path, symbol_directory: Path) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("windows-stackwalk-unsupported-on-this-platform")
    # AMD64 CONTEXT is 1232 bytes. RIP ends at byte 256, but StackWalk64 may
    # inspect the rest of the native structure; never pass a truncated buffer.
    if dump.architecture != 9 or len(dump.context) < 1232 or dump.exception is None:
        raise RuntimeError("amd64-exception-context-required")
    from ctypes import wintypes as w

    u64 = c.c_uint64
    u32 = c.c_uint32
    pointer = c.c_void_p

    class Address(c.Structure):
        _fields_ = [("offset", u64), ("segment", c.c_uint16), ("mode", u32)]

    class KdHelp(c.Structure):
        _fields_ = [
            ("thread", u64),
            ("offsets", u32 * 4),
            ("addresses", u64 * 6),
            ("build", u32),
            ("tableSize", u32),
            ("table", u64),
            ("stubOffset", u32),
            ("stubSize", u32),
            ("reserved", u64 * 2),
        ]

    class Frame(c.Structure):
        _fields_ = [
            ("pc", Address),
            ("ret", Address),
            ("frame", Address),
            ("stack", Address),
            ("backing", Address),
            ("function", pointer),
            ("params", u64 * 4),
            ("far", w.BOOL),
            ("virtual", w.BOOL),
            ("reserved", u64 * 3),
            ("kd", KdHelp),
        ]

    class Symbol(c.Structure):
        _fields_ = [
            ("sizeOfStruct", u32),
            ("typeIndex", u32),
            ("reserved", u64 * 2),
            ("index", u32),
            ("size", u32),
            ("modBase", u64),
            ("flags", u32),
            ("value", u64),
            ("address", u64),
            ("register", u32),
            ("scope", u32),
            ("tag", u32),
            ("nameLen", u32),
            ("maxNameLen", u32),
            ("name", c.c_char * 1),
        ]

    class Line(c.Structure):
        _fields_ = [
            ("sizeOfStruct", u32),
            ("key", pointer),
            ("line", u32),
            ("file", c.c_char_p),
            ("address", u64),
        ]

    # Windows SDK DbgHelp.h layout. Do not silently walk using a different ABI.
    if c.sizeof(Frame) != 264 or c.sizeof(KdHelp) != 112:
        raise RuntimeError("unsupported-dbghelp-ABI")
    system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
    dbg = c.WinDLL(str(system / "dbghelp.dll"), use_last_error=True)
    dbg.SymSetOptions.argtypes = [u32]
    dbg.SymSetOptions.restype = u32
    # EXACT_SYMBOLS, IGNORE_NT_SYMPATH, FAIL_CRITICAL_ERRORS, SECURE, NO_PROMPTS.
    dbg.SymSetOptions(0x2 | 0x4 | 0x10 | 0x200 | 0x400 | 0x1000 | 0x40000 | 0x80000)
    dbg.SymInitializeW.argtypes = [pointer, c.c_wchar_p, w.BOOL]
    dbg.SymInitializeW.restype = w.BOOL
    dbg.SymLoadModuleExW.argtypes = [
        pointer,
        pointer,
        c.c_wchar_p,
        c.c_wchar_p,
        u64,
        u32,
        pointer,
        u32,
    ]
    dbg.SymLoadModuleExW.restype = u64
    dbg.SymFromAddr.argtypes = [pointer, u64, c.POINTER(u64), c.POINTER(Symbol)]
    dbg.SymFromAddr.restype = w.BOOL
    dbg.SymGetLineFromAddr64.argtypes = [pointer, u64, c.POINTER(u32), c.POINTER(Line)]
    dbg.SymGetLineFromAddr64.restype = w.BOOL
    dbg.SymCleanup.argtypes = [pointer]
    dbg.SymCleanup.restype = w.BOOL
    handle = pointer(0xAF156)
    if not dbg.SymInitializeW(handle, str(symbol_directory.resolve()), False):
        raise RuntimeError(f"dbghelp-initialize-failed:{c.get_last_error()}")
    images: dict[int, PeImage | None] = {}
    functions: dict[int, tuple[list[int], list[tuple[int, int, int]]]] = {}
    function_buffers: dict[tuple[int, int], Any] = {}
    gaps: set[str] = set()
    af_image = PeImage(image_path)
    af_module = next(
        (
            m
            for m in dump.modules
            if m["codeView"]
            and all(m["codeView"].get(k) == af_image.codeview[k] for k in ("guid", "age"))
        ),
        None,
    )
    if af_module is None:
        dbg.SymCleanup(handle)
        raise RuntimeError("dump-symbol-binding-not-found")

    def image_for(module: dict[str, Any]) -> PeImage | None:
        base = module["base"]
        if base not in images:
            candidate = image_path if base == af_module["base"] else system / module["name"]
            try:
                if any(x in module["name"] for x in "\\/:"):
                    raise BinaryFormatError("invalid module basename")
                image = PeImage(candidate)
                if (
                    image.machine != 0x8664
                    or image.timestamp != module["timeDateStamp"]
                    or image.size_of_image != module["sizeOfImage"]
                ):
                    raise BinaryFormatError("image identity mismatch")
                images[base] = image
            except (OSError, ValueError):
                images[base] = None
                gaps.add("image-unavailable-or-mismatched:" + module["name"])
        return images[base]

    read_callback = c.WINFUNCTYPE(w.BOOL, pointer, u64, pointer, u32, c.POINTER(u32))
    function_callback = c.WINFUNCTYPE(pointer, pointer, u64)
    base_callback = c.WINFUNCTYPE(u64, pointer, u64)

    @read_callback
    def read_memory(_handle: Any, address: int, buffer: Any, size: int, done: Any) -> int:
        try:
            raw = dump.read_memory(address, size)
            if not raw:
                module = dump.module_at(address)
                image = image_for(module) if module else None
                if image and module:
                    raw = image.rva_bytes(address - module["base"], size)
            if raw:
                c.memmove(buffer, raw, len(raw))
                done[0] = len(raw)
                return 1
        except (OSError, ValueError):
            pass
        done[0] = 0
        return 0

    @function_callback
    def function_table(_handle: Any, address: int) -> int | None:
        module = dump.module_at(address)
        image = image_for(module) if module else None
        if not module or image is None:
            return None
        base = module["base"]
        try:
            if base not in functions:
                raw = image.rva_bytes(image.exception_rva, image.exception_size)
                if len(raw) % 12:
                    raise BinaryFormatError("invalid unwind table")
                entries = list(struct.iter_unpack("<III", raw))
                functions[base] = ([f[0] for f in entries], entries)
            starts, entries = functions[base]
            rva = address - base
            index = bisect.bisect_right(starts, rva) - 1
            if index < 0 or not entries[index][0] <= rva < entries[index][1]:
                return None
            key = (base, index)
            if key not in function_buffers:
                function_buffers[key] = (u32 * 3)(*entries[index])
            return c.addressof(function_buffers[key])
        except (OSError, ValueError):
            gaps.add("unwind-data-unavailable:" + module["name"])
            return None

    @base_callback
    def module_base(_handle: Any, address: int) -> int:
        module = dump.module_at(address)
        return module["base"] if module else 0

    dbg.StackWalk64.argtypes = [
        u32,
        pointer,
        pointer,
        c.POINTER(Frame),
        pointer,
        read_callback,
        function_callback,
        base_callback,
        pointer,
    ]
    dbg.StackWalk64.restype = w.BOOL
    frame = Frame()
    frame.pc.offset = struct.unpack_from("<Q", dump.context, 248)[0]
    frame.stack.offset = struct.unpack_from("<Q", dump.context, 152)[0]
    frame.frame.offset = struct.unpack_from("<Q", dump.context, 160)[0]
    frame.pc.mode = frame.stack.mode = frame.frame.mode = 3
    context = c.create_string_buffer(dump.context)
    frames: list[dict[str, Any]] = []
    seen = set()
    try:
        if not dbg.SymLoadModuleExW(
            handle,
            None,
            str(image_path.resolve()),
            None,
            af_module["base"],
            af_module["sizeOfImage"],
            None,
            0,
        ):
            raise RuntimeError(f"dbghelp-load-symbols-failed:{c.get_last_error()}")
        for index in range(128):
            ok = dbg.StackWalk64(
                0x8664,
                handle,
                pointer(1),
                c.byref(frame),
                context,
                read_memory,
                function_table,
                module_base,
                None,
            )
            if not ok:
                if frame.ret.offset:
                    gaps.add("stack-incomplete")
                break
            address = frame.pc.offset
            key = (address, frame.stack.offset)
            if not address or key in seen:
                break
            seen.add(key)
            module = dump.module_at(address)
            result = {
                "index": index,
                "module": module["name"] if module else None,
                "rva": address - module["base"] if module else None,
                "function": None,
                "source": None,
            }
            if module and module["base"] == af_module["base"]:
                lookup_address = address if index == 0 else address - 1
                buffer = c.create_string_buffer(c.sizeof(Symbol) + 4096)
                info = c.cast(buffer, c.POINTER(Symbol))
                info.contents.sizeOfStruct = c.sizeof(Symbol)
                info.contents.maxNameLen = 4096
                displacement = u64()
                if dbg.SymFromAddr(handle, lookup_address, c.byref(displacement), info):
                    raw_name = c.string_at(
                        c.addressof(buffer) + Symbol.name.offset, min(info.contents.nameLen, 4096)
                    ).decode("utf-8", errors="replace")
                    result["function"] = display_symbol(raw_name)
                else:
                    gaps.add("function-symbol-unavailable")
                line = Line()
                line.sizeOfStruct = c.sizeof(Line)
                delta = u32()
                if dbg.SymGetLineFromAddr64(handle, lookup_address, c.byref(delta), c.byref(line)):
                    result["source"] = {
                        "file": PureWindowsPath(line.file.decode("utf-8", errors="replace")).name,
                        "line": line.line,
                    }
                else:
                    gaps.add("source-line-unavailable")
            frames.append(result)
        return {"frames": frames, "gaps": sorted(gaps), "backend": "windows-dbghelp-offline"}
    finally:
        dbg.SymCleanup(handle)
