"""Windows shell paths used by both NSIS and installer smoke verification."""

from __future__ import annotations

import ctypes
import os
import tempfile
from pathlib import Path


class SmokePathRedirection(RuntimeError):
    """A packaged desktop host redirected a fresh shell-folder write."""


def shell_folder(csidl: int) -> Path:
    # Current-user shell context, matching the per-user NSIS installer.
    function = ctypes.WinDLL("shell32", use_last_error=True).SHGetFolderPathW
    function.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_wchar_p,
    ]
    function.restype = ctypes.c_long
    buffer = ctypes.create_unicode_buffer(32768)
    result = function(None, csidl, None, 0, buffer)
    if result != 0 or not buffer.value:
        raise RuntimeError(f"Windows shell folder lookup failed: csidl={csidl}, HRESULT={result}")
    return Path(buffer.value)


def shell_paths() -> dict[str, Path]:
    return {
        name: shell_folder(csidl)
        for name, csidl in (("local", 28), ("roaming", 26), ("programs", 2), ("desktop", 16))
    }


def verify_smoke_environment() -> None:
    paths = shell_paths()
    for name, variable in (("local", "LOCALAPPDATA"), ("roaming", "APPDATA")):
        declared = paths[name]
        environment = os.environ.get(variable)
        if not environment or os.path.normcase(os.path.abspath(environment)) != os.path.normcase(
            str(declared.absolute())
        ):
            raise RuntimeError(
                f"installer smoke environment mismatch: {variable}={environment!r}, "
                f"Windows shell={declared}. Restore the normal user environment; "
                "changing AppData variables does not relocate NSIS shell folders."
            )
        # A fresh, empty directory detects write redirection that resolving an
        # existing parent cannot reveal. Delete only this owned empty probe.
        probe = Path(tempfile.mkdtemp(prefix="agent-friend-smoke-path-probe-", dir=declared))
        try:
            actual = probe.resolve()
            if actual.parent != declared.resolve():
                raise SmokePathRedirection(
                    f"installer smoke filesystem redirection: {probe} -> {actual}. "
                    "Run the package script from a normal, non-virtualized Windows terminal. "
                    "No installation or smoke-state cleanup has been started."
                )
        finally:
            probe.rmdir()
