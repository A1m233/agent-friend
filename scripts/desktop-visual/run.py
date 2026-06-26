from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WINDOW_QUERY = "agent-friend"


def has_command(name: str) -> bool:
    return shutil.which(name) is not None


def print_check(label: str, ok: bool, detail: str = "") -> None:
    status = "OK" if ok else "WARN"
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


def run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)


def run_passthrough(cmd: list[str], env: dict[str, str] | None = None) -> int:
    return subprocess.run(cmd, cwd=ROOT, env=env).returncode


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
        print(
            "[INFO] DOM via Playwright is not assumed on macOS WKWebView. Use screenshots unless a dev-only DOM probe exists."
        )
    elif system == "Windows":
        print("[INFO] For DOM, start with: desktop-visual start --cdp-port 9222")
        print("[INFO] Then run: desktop-visual dom --cdp http://127.0.0.1:9222")
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
    if args.cdp_port:
        flag = f"--remote-debugging-port={args.cdp_port}"
        current = env.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            current if flag in current else f"{current} {flag}".strip()
        )
        print(f"===> CDP requested on http://127.0.0.1:{args.cdp_port}")
        print("     This is intended for Windows WebView2. Other platforms may ignore it.")

    cmd = [*dev_script_command(), "--tauri", *forwarded]
    print("===> launching:", " ".join(cmd))
    if platform.system() == "Windows":
        return run_passthrough(cmd, env)
    os.execvpe(cmd[0], cmd, env)
    return 0


def apple_quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def mac_osascript(script: str) -> subprocess.CompletedProcess[str]:
    return run_capture(["osascript", "-e", script])


def cmd_list_windows(_args: argparse.Namespace) -> int:
    if platform.system() != "Darwin":
        print("list-windows is currently implemented for macOS only.")
        return 2

    script = """
tell application "System Events"
  set rows to {}
  repeat with proc in application processes
    repeat with w in windows of proc
      set end of rows to ((name of proc as text) & "\t" & (name of w as text) & "\t" & (id of w as text))
    end repeat
  end repeat
  return rows
end tell
"""
    result = mac_osascript(script)
    if result.returncode != 0:
        print(result.stderr.strip() or "Unable to list windows.")
        print("macOS may require Accessibility/Screen Recording permission for the terminal.")
        return result.returncode
    print(result.stdout.strip())
    return 0


def find_mac_window_id(query: str) -> str | None:
    q = apple_quote(query)
    script = f"""
tell application "System Events"
  repeat with proc in application processes
    repeat with w in windows of proc
      set windowTitle to name of w as text
      if windowTitle contains "{q}" then
        return id of w as text
      end if
    end repeat
  end repeat
end tell
"""
    result = mac_osascript(script)
    if result.returncode != 0:
        print(result.stderr.strip() or "Unable to find window.", file=sys.stderr)
        return None
    window_id = result.stdout.strip()
    return window_id or None


def default_screenshot_path() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / f"agent-friend-desktop-{stamp}.png"


def cmd_screenshot(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser() if args.output else default_screenshot_path()
    output.parent.mkdir(parents=True, exist_ok=True)

    if platform.system() != "Darwin":
        print("screenshot is currently implemented for macOS only.")
        print("Use DOM/CDP screenshot on Windows when WebView2 remote debugging is enabled.")
        return 2

    if args.full_screen:
        cmd = ["screencapture", "-x", str(output)]
    else:
        window_id = find_mac_window_id(args.window)
        if not window_id:
            print(f"Window not found for query: {args.window!r}")
            print("Run: ./scripts/desktop-visual/run.sh list-windows")
            return 2
        cmd = ["screencapture", "-x", f"-l{window_id}", str(output)]

    result = run_passthrough(cmd)
    if result == 0:
        print(f"===> screenshot: {output}")
    return result


def cmd_dom(args: argparse.Namespace) -> int:
    node_script = ROOT / "scripts/desktop-visual/dom-probe.mjs"
    cmd = [
        "pnpm",
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
    sub.add_parser("list-windows", help="List macOS windows visible to System Events.")

    start = sub.add_parser("start", help="Start the real Tauri desktop app via scripts/dev.")
    start.add_argument(
        "--cdp-port", type=int, help="Expose WebView2 CDP on this port when supported."
    )

    screenshot = sub.add_parser("screenshot", help="Capture a real desktop window screenshot.")
    screenshot.add_argument(
        "--window", default=DEFAULT_WINDOW_QUERY, help="Window title substring."
    )
    screenshot.add_argument("--output", help="Output PNG path. Defaults to the system temp dir.")
    screenshot.add_argument(
        "--full-screen", action="store_true", help="Capture the full screen instead of a window."
    )

    dom = sub.add_parser("dom", help="Dump DOM via a CDP endpoint with Playwright.")
    dom.add_argument("--cdp", required=True, help="CDP endpoint, for example http://127.0.0.1:9222")
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
