from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

SETTINGS_NAMESPACE = "com.agentfriend.desktop"
FILE_NAME = "owner-capabilities.json"


def capability_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / SETTINGS_NAMESPACE / FILE_NAME


def read_enabled(path: Path) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return value == {"schemaVersion": 1, "memoryInspector": True}


def write_enabled(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps({"schemaVersion": 1, "memoryInspector": True}, ensure_ascii=False, indent=2)
        + "\n"
    )
    fd, temp_name = tempfile.mkstemp(prefix=f".{FILE_NAME}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    action = argv[1] if len(argv) > 1 else "status"
    path = capability_path()
    if action == "enable-memory-inspector":
        write_enabled(path)
        print(f"memoryInspector=enabled path={path}")
        print("Restart agent-friend to apply the capability to both shell and Bridge.")
        return 0
    if action == "disable-memory-inspector":
        path.unlink(missing_ok=True)
        print(f"memoryInspector=disabled path={path}")
        print("Restart agent-friend to apply the capability to both shell and Bridge.")
        return 0
    if action == "status":
        print(f"memoryInspector={'enabled' if read_enabled(path) else 'disabled'} path={path}")
        return 0
    print("usage: run.py enable-memory-inspector|disable-memory-inspector|status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
