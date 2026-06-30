from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WINDOW_QUERY = "agent-friend"
DEFAULT_CDP_ENDPOINT = "http://127.0.0.1:9222"
SWIFT_MODULE_CACHE = Path(tempfile.gettempdir()) / "agent-friend-swift-module-cache"


@dataclass(frozen=True)
class MacWindowRow:
    source: str
    owner: str
    title: str
    window_id: str = ""
    layer: str = ""
    bounds: str = ""


@dataclass(frozen=True)
class MacWindowLookup:
    window_id: str | None
    source: str
    row: MacWindowRow | None
    title_matches: list[MacWindowRow]
    errors: list[str]


def has_command(name: str) -> bool:
    return shutil.which(name) is not None


def command_path(name: str) -> str:
    return shutil.which(name) or name


def print_check(label: str, ok: bool, detail: str = "") -> None:
    status = "OK" if ok else "WARN"
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


def run_capture(
    cmd: list[str],
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        detail = stderr or f"Timed out after {timeout:g}s."
        return subprocess.CompletedProcess(cmd, 124, stdout, detail)


def run_passthrough(cmd: list[str], env: dict[str, str] | None = None) -> int:
    return subprocess.run(cmd, cwd=ROOT, env=env).returncode


def cdp_endpoint_ok(endpoint: str) -> bool:
    url = endpoint.rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def cmd_doctor(_args: argparse.Namespace) -> int:
    system = platform.system()
    print(f"===> desktop-visual doctor ({system})")
    print_check("uv", has_command("uv"))
    print_check("pnpm", has_command("pnpm"))
    print_check("dev script", (ROOT / "scripts/dev/run.sh").exists())
    print_check(
        "frontend playwright dependency", (ROOT / "frontend/node_modules/playwright").exists()
    )

    if system == "Darwin":
        print_check("macOS screencapture", has_command("screencapture"))
        print_check("macOS osascript", has_command("osascript"))
        print_check(
            "macOS CoreGraphics window id fallback",
            has_command("swift"),
            "uses swift + CGWindowListCopyWindowInfo",
        )
        print(
            "[INFO] DOM via Playwright is not assumed on macOS WKWebView. Use screenshots unless a dev-only DOM probe exists."
        )
    elif system == "Windows":
        cdp_ok = cdp_endpoint_ok(DEFAULT_CDP_ENDPOINT)
        print_check("WebView2 CDP endpoint", cdp_ok, DEFAULT_CDP_ENDPOINT)
        print("[INFO] Windows Tauri dev enables CDP by default via scripts/dev/run.ps1.")
        print("[INFO] Use DOM with: desktop-visual dom")
    else:
        print("[INFO] Linux desktop screenshot capture is not implemented in this harness yet.")
        print("[INFO] Tauri WebDriver/tauri-driver is the preferred Linux DOM automation path.")

    return 0


def dev_script_command() -> list[str]:
    if platform.system() == "Windows":
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts/dev/run.ps1"),
        ]
    return [str(ROOT / "scripts/dev/run.sh")]


def cmd_start(args: argparse.Namespace, forwarded: list[str]) -> int:
    env = os.environ.copy()
    dev_args = [*forwarded]
    if args.cdp_port:
        flag = f"--remote-debugging-port={args.cdp_port}"
        current = env.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            current if flag in current else f"{current} {flag}".strip()
        )
        dev_args = ["--cdp-port", str(args.cdp_port), *dev_args]
        print(f"===> CDP requested on http://127.0.0.1:{args.cdp_port}")
        print("     This is intended for Windows WebView2. Other platforms may ignore it.")

    cmd = [*dev_script_command(), "--tauri", *dev_args]
    print("===> launching:", " ".join(cmd))
    if platform.system() == "Windows":
        return run_passthrough(cmd, env)
    os.execvpe(cmd[0], cmd, env)
    return 0


def mac_osascript(script: str, timeout: float | None = 5) -> subprocess.CompletedProcess[str]:
    return run_capture(["osascript", "-e", script], timeout=timeout)


MAC_CG_WINDOW_SWIFT = r"""
import CoreGraphics
import Foundation

func intValue(_ value: Any?) -> Int {
    if let value = value as? Int {
        return value
    }
    if let value = value as? Double {
        return Int(value)
    }
    if let value = value as? CGFloat {
        return Int(value)
    }
    if let value = value as? NSNumber {
        return value.intValue
    }
    return 0
}

let windows = CGWindowListCopyWindowInfo(
    [.optionOnScreenOnly, .excludeDesktopElements],
    kCGNullWindowID
) as? [[String: Any]] ?? []

var rows: [[String: String]] = []
for window in windows {
    let owner = window[kCGWindowOwnerName as String] as? String ?? ""
    let title = window[kCGWindowName as String] as? String ?? ""
    let windowId = intValue(window[kCGWindowNumber as String])
    let layer = intValue(window[kCGWindowLayer as String])
    let bounds = window[kCGWindowBounds as String] as? [String: Any] ?? [:]
    let boundsText = [
        "x=\(intValue(bounds["X"]))",
        "y=\(intValue(bounds["Y"]))",
        "w=\(intValue(bounds["Width"]))",
        "h=\(intValue(bounds["Height"]))",
    ].joined(separator: ",")

    if owner.isEmpty && title.isEmpty {
        continue
    }
    rows.append([
        "source": "cg",
        "owner": owner,
        "title": title,
        "window_id": "\(windowId)",
        "layer": "\(layer)",
        "bounds": boundsText,
    ])
}

let data = try JSONSerialization.data(withJSONObject: rows, options: [])
FileHandle.standardOutput.write(data)
"""


def parse_mac_window_rows(stdout: str) -> list[MacWindowRow]:
    rows: list[MacWindowRow] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        parts.extend([""] * (6 - len(parts)))
        source, owner, title, window_id, layer, bounds = parts[:6]
        rows.append(
            MacWindowRow(
                source=source,
                owner=owner,
                title=title,
                window_id=window_id,
                layer=layer,
                bounds=bounds,
            )
        )
    return rows


def list_mac_accessibility_windows() -> tuple[list[MacWindowRow], str | None]:
    script = """
tell application "System Events"
  set windowRows to {}
  set procCount to count of application processes
  repeat with procIndex from 1 to procCount
    try
      set proc to item procIndex of application processes
      set procName to name of proc as text
      set windowCount to count of windows of proc
      repeat with windowIndex from 1 to windowCount
        try
          set w to item windowIndex of windows of proc
          set windowTitle to name of w as text
          set windowId to ""
          try
            set windowId to id of w as text
          end try
          set end of windowRows to ("ax" & tab & procName & tab & windowTitle & tab & windowId & tab & "" & tab & "")
        end try
      end repeat
    end try
  end repeat
  set AppleScript's text item delimiters to linefeed
  return windowRows as text
end tell
"""
    result = mac_osascript(script)
    if result.returncode != 0:
        detail = result.stderr.strip() or "Unable to list Accessibility windows."
        return [], f"Accessibility window list failed: {detail}"
    return parse_mac_window_rows(result.stdout), None


def list_mac_coregraphics_windows() -> tuple[list[MacWindowRow], str | None]:
    if not has_command("swift"):
        return [], "CoreGraphics window list unavailable: swift command not found."

    env = os.environ.copy()
    SWIFT_MODULE_CACHE.mkdir(parents=True, exist_ok=True)
    env["CLANG_MODULE_CACHE_PATH"] = str(SWIFT_MODULE_CACHE)

    result = run_capture(["swift", "-e", MAC_CG_WINDOW_SWIFT], env=env)
    if result.returncode != 0:
        detail = result.stderr.strip() or "Unable to list CoreGraphics windows."
        return [], f"CoreGraphics window list failed: {detail}"
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        return [], f"CoreGraphics window list returned invalid JSON: {exc}"

    rows = [
        MacWindowRow(
            source=str(item.get("source", "cg")),
            owner=str(item.get("owner", "")),
            title=str(item.get("title", "")),
            window_id=str(item.get("window_id", "")),
            layer=str(item.get("layer", "")),
            bounds=str(item.get("bounds", "")),
        )
        for item in payload
        if isinstance(item, dict)
    ]
    return rows, None


def list_mac_windows() -> tuple[list[MacWindowRow], list[str]]:
    rows: list[MacWindowRow] = []
    errors: list[str] = []
    for list_source in (list_mac_accessibility_windows, list_mac_coregraphics_windows):
        source_rows, error = list_source()
        rows.extend(source_rows)
        if error:
            errors.append(error)
    return rows, errors


def mac_row_matches(row: MacWindowRow, query: str) -> bool:
    needle = query.casefold()
    return needle in row.title.casefold() or needle in row.owner.casefold()


def choose_mac_window_id(
    query: str,
    rows: list[MacWindowRow],
    errors: list[str],
) -> MacWindowLookup:
    matches = [row for row in rows if mac_row_matches(row, query)]
    needle = query.casefold()

    # Prefer CoreGraphics ids because screencapture -l expects a CGWindowID.
    for source in ("cg", "ax"):
        for field_name in ("title", "owner"):
            for row in matches:
                field_value = getattr(row, field_name).casefold()
                if row.source == source and row.window_id and needle in field_value:
                    return MacWindowLookup(
                        window_id=row.window_id,
                        source=source,
                        row=row,
                        title_matches=matches,
                        errors=errors,
                    )

    return MacWindowLookup(
        window_id=None,
        source="none",
        row=None,
        title_matches=matches,
        errors=errors,
    )


def find_mac_window_id(query: str) -> MacWindowLookup:
    cg_rows, cg_error = list_mac_coregraphics_windows()
    cg_lookup = choose_mac_window_id(query, cg_rows, [cg_error] if cg_error else [])
    if cg_lookup.window_id:
        return cg_lookup

    ax_rows, ax_error = list_mac_accessibility_windows()
    errors = [error for error in (cg_error, ax_error) if error]
    return choose_mac_window_id(query, [*cg_rows, *ax_rows], errors)


def format_mac_window_row(row: MacWindowRow) -> str:
    return "\t".join([row.source, row.owner, row.title, row.window_id, row.layer, row.bounds])


def cmd_list_windows(_args: argparse.Namespace) -> int:
    if platform.system() != "Darwin":
        print("list-windows is currently implemented for macOS only.")
        return 2

    rows, errors = list_mac_windows()
    print("source\towner\ttitle\twindow_id\tlayer\tbounds")
    for row in rows:
        print(format_mac_window_row(row))
    for error in errors:
        print(f"[WARN] {error}", file=sys.stderr)
    if not rows:
        print(
            "[WARN] No windows were returned. macOS may require Accessibility and/or "
            "Screen Recording permission for this terminal.",
            file=sys.stderr,
        )
        return 1
    return 0


def default_screenshot_path() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / f"agent-friend-desktop-{stamp}.png"


def write_metadata(path: str | None, payload: dict[str, object]) -> None:
    if not path:
        return
    metadata_path = Path(path).expanduser()
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"===> metadata: {metadata_path}")


def print_window_lookup_failure(query: str, lookup: MacWindowLookup) -> None:
    if lookup.title_matches:
        print(
            f"Window title/owner matched query {query!r}, but no usable CGWindowID was found.",
            file=sys.stderr,
        )
        print(
            "Matched windows may be Accessibility-only, transparent NSPanel/WKWebView windows, "
            "or hidden behind missing Screen Recording permission.",
            file=sys.stderr,
        )
        for row in lookup.title_matches[:10]:
            print(f"  {format_mac_window_row(row)}", file=sys.stderr)
    else:
        print(f"No macOS window matched query: {query!r}", file=sys.stderr)

    for error in lookup.errors:
        print(f"[WARN] {error}", file=sys.stderr)
    print("Run: ./scripts/desktop-visual/run.sh list-windows", file=sys.stderr)
    print(
        "Use --fallback-full-screen or --full-screen to capture the real desktop instead.",
        file=sys.stderr,
    )


def capture_full_screen(
    output: Path,
    metadata_path: str | None,
    reason: str,
    extra_metadata: dict[str, object] | None = None,
) -> int:
    result = run_passthrough(["screencapture", "-x", str(output)])
    if result == 0:
        print(f"===> full-screen screenshot: {output}")
        print(f"[WARN] This is not a window screenshot. Reason: {reason}")
        metadata: dict[str, object] = {
            "capture_kind": "full-screen",
            "output": str(output),
            "reason": reason,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        write_metadata(
            metadata_path,
            metadata,
        )
    return result


def cmd_screenshot(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser() if args.output else default_screenshot_path()
    output.parent.mkdir(parents=True, exist_ok=True)

    if platform.system() != "Darwin":
        print("screenshot is currently implemented for macOS only.")
        print("Use DOM/CDP screenshot on Windows when WebView2 remote debugging is enabled.")
        return 2

    if args.full_screen:
        return capture_full_screen(output, args.metadata, "--full-screen was requested")

    lookup = find_mac_window_id(args.window)
    if not lookup.window_id:
        print_window_lookup_failure(args.window, lookup)
        write_metadata(
            args.metadata,
            {
                "capture_kind": "window",
                "query": args.window,
                "status": "no-usable-window-id",
                "matches": [asdict(row) for row in lookup.title_matches],
                "errors": lookup.errors,
            },
        )
        if args.fallback_full_screen:
            return capture_full_screen(
                output,
                args.metadata,
                f"no usable window id for query {args.window!r}",
                {
                    "query": args.window,
                    "window_lookup_status": "no-usable-window-id",
                    "matches": [asdict(row) for row in lookup.title_matches],
                    "errors": lookup.errors,
                },
            )
        return 2

    cmd = ["screencapture", "-x", f"-l{lookup.window_id}", str(output)]
    result = run_passthrough(cmd)
    if result == 0:
        print(f"===> window screenshot: {output}")
        if lookup.row:
            print(
                "===> window: "
                f"source={lookup.source} owner={lookup.row.owner!r} "
                f"title={lookup.row.title!r} id={lookup.window_id}"
            )
        write_metadata(
            args.metadata,
            {
                "capture_kind": "window",
                "query": args.window,
                "output": str(output),
                "window_id": lookup.window_id,
                "source": lookup.source,
                "matched_window": asdict(lookup.row) if lookup.row else None,
                "errors": lookup.errors,
            },
        )
    elif args.fallback_full_screen:
        print(
            f"[WARN] Window screenshot failed for id {lookup.window_id}; falling back to full screen.",
            file=sys.stderr,
        )
        return capture_full_screen(
            output,
            args.metadata,
            f"screencapture -l failed for window id {lookup.window_id}",
            {
                "query": args.window,
                "window_id": lookup.window_id,
                "source": lookup.source,
                "matched_window": asdict(lookup.row) if lookup.row else None,
                "errors": lookup.errors,
            },
        )
    return result


def cmd_dom(args: argparse.Namespace) -> int:
    node_script = ROOT / "scripts/desktop-visual/dom-probe.mjs"
    cmd = [
        command_path("pnpm"),
        "--dir",
        "frontend",
        "exec",
        "node",
        str(node_script),
        "--cdp",
        args.cdp,
    ]
    if args.title_contains:
        cmd.extend(["--title-contains", args.title_contains])
    if args.url_contains:
        cmd.extend(["--url-contains", args.url_contains])
    if args.output:
        cmd.extend(["--output", args.output])
    if args.html_output:
        cmd.extend(["--html-output", args.html_output])
    if args.screenshot:
        cmd.extend(["--screenshot", args.screenshot])
    return run_passthrough(cmd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect real desktop UI state for agent-friend.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Report available desktop inspection capabilities.")
    sub.add_parser(
        "list-windows",
        help="List macOS windows from Accessibility and CoreGraphics.",
    )

    start = sub.add_parser("start", help="Start the real Tauri desktop app via scripts/dev.")
    start.add_argument(
        "--cdp-port",
        type=int,
        help="Expose WebView2 CDP on this port when supported. Windows dev defaults to 9222.",
    )

    screenshot = sub.add_parser("screenshot", help="Capture a real desktop screenshot.")
    screenshot.add_argument(
        "--window", default=DEFAULT_WINDOW_QUERY, help="Window title substring."
    )
    screenshot.add_argument("--output", help="Output PNG path. Defaults to the system temp dir.")
    screenshot.add_argument(
        "--full-screen", action="store_true", help="Capture the full screen instead of a window."
    )
    screenshot.add_argument(
        "--fallback-full-screen",
        action="store_true",
        help=(
            "If the window title/owner is found but no usable window id is available, "
            "capture the full screen and mark it as a non-window screenshot."
        ),
    )
    screenshot.add_argument(
        "--metadata",
        help="Write JSON capture metadata and macOS window lookup diagnostics.",
    )

    dom = sub.add_parser("dom", help="Dump DOM via a CDP endpoint with Playwright.")
    dom.add_argument(
        "--cdp",
        default=DEFAULT_CDP_ENDPOINT,
        help=f"CDP endpoint. Defaults to {DEFAULT_CDP_ENDPOINT}",
    )
    dom.add_argument(
        "--title-contains", help="Select the first page whose title contains this text."
    )
    dom.add_argument("--url-contains", help="Select the first page whose URL contains this text.")
    dom.add_argument("--output", help="Write JSON summary to this path.")
    dom.add_argument("--html-output", help="Write full outerHTML to this path.")
    dom.add_argument("--screenshot", help="Write a Playwright screenshot to this path.")
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args, forwarded = parser.parse_known_args(argv)

    if args.command != "start" and forwarded:
        parser.error(f"unrecognized arguments: {' '.join(forwarded)}")

    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "start":
        return cmd_start(args, forwarded)
    if args.command == "list-windows":
        return cmd_list_windows(args)
    if args.command == "screenshot":
        return cmd_screenshot(args)
    if args.command == "dom":
        return cmd_dom(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
