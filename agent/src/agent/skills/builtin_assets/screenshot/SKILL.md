---
name: "screenshot"
description: "Use when the user explicitly asks for a desktop or system screenshot (full screen, specific app or window, or a pixel region), or when tool-specific capture capabilities are unavailable and an OS-level capture is needed."
---


# Screenshot Capture

Follow these save-location rules every time:

1) If the user specifies a path, save there.
2) If the user asks for a screenshot without a path, use the host-managed session screenshot directory (provided through `AGENT_FRIEND_SCREENSHOT_OUTPUT_DIR`). Outside agent-friend, the helper falls back to the OS default screenshot location.
3) If Codex needs a screenshot for its own inspection, save to the temp directory.

## agent-friend execution and viewing

Use `exec_command` with the activated Skill binding (`skill.entry` and package-relative `skill.workdir`), `mode=buffered`, `access.filesystem=read_write`, and `access.network=disabled`. Windows uses the product-provided `pwsh.exe`. Do not add the entire home or disk root to `additional_roots` for a screenshot. The host includes the bounded default output directory in the same command approval; no absolute Skill installation path is needed.

For a default Windows screenshot, run `pwsh.exe -NoProfile -ExecutionPolicy Bypass -File scripts/take_screenshot.ps1` with `skill.entry=scripts/take_screenshot.ps1` and `skill.workdir=.`. Capture arguments such as `-Region` and `-ActiveWindow` still apply. An explicit external `-Path` needs its actual parent directory authorized in that command; a single file directly under a broad root can be saved through the existing file-operation authorization after capture to the managed directory.

After a successful command, its `output_directory.filesystem_scope_id` provides current-turn `read_image` access to the default output directory. Pass that exact scope and the printed image filename (not its absolute path) to `view_image`. Explicit external paths and temp outputs require their corresponding `read_image` scope instead. A successful `view_image` automatically provides a collapsible thumbnail and full-size preview to the user; do not invoke a separate publishing tool or repeat the image in Markdown.

An exit code alone does not prove a valid screenshot. Inspect the image and respect visible-window capture limits. For unknown start/termination outcomes, stop and report uncertainty; do not rerun the command or switch to unsandboxed execution. Ordinary permission failures require correcting the actual output scope, not widening it to home.

## Tool priority

- Prefer tool-specific screenshot capabilities when available (for example: a Figma MCP/skill for Figma files, or Playwright/agent-browser tools for browsers and Electron apps).
- Use this skill when explicitly asked, for whole-system desktop captures, or when a tool-specific capture cannot get what you need.
- Otherwise, treat this skill as the default for desktop apps without a better-integrated capture tool.

## macOS permission preflight (reduce repeated prompts)

On macOS, run the preflight helper once before window/app capture. It checks
Screen Recording permission, explains why it is needed, and requests it in one
place.

The helpers route Swift's module cache to `$TMPDIR/codex-swift-module-cache`
to avoid extra sandbox module-cache prompts.

```bash
bash scripts/ensure_macos_permissions.sh
```

To avoid multiple sandbox approval prompts, combine preflight + capture in one
command when possible:

```bash
bash scripts/ensure_macos_permissions.sh && \
python3 scripts/take_screenshot.py --app "Codex"
```

For Codex inspection runs, keep the output in temp:

```bash
bash scripts/ensure_macos_permissions.sh && \
python3 scripts/take_screenshot.py --app "<App>" --mode temp
```

Use the bundled scripts to avoid re-deriving OS-specific commands.

## macOS and Linux (Python helper)

Run the helper from the repo root:

```bash
python3 scripts/take_screenshot.py
```

Common patterns:

- Default location (user asked for "a screenshot"):

```bash
python3 scripts/take_screenshot.py
```

- Temp location (Codex visual check):

```bash
python3 scripts/take_screenshot.py --mode temp
```

- Explicit location (user provided a path or filename):

```bash
python3 scripts/take_screenshot.py --path output/screen.png
```

- App/window capture by app name (macOS only; substring match is OK; captures all matching windows):

```bash
python3 scripts/take_screenshot.py --app "Codex"
```

- Specific window title within an app (macOS only):

```bash
python3 scripts/take_screenshot.py --app "Codex" --window-name "Settings"
```

- List matching window ids before capturing (macOS only):

```bash
python3 scripts/take_screenshot.py --list-windows --app "Codex"
```

- Pixel region (x,y,w,h):

```bash
python3 scripts/take_screenshot.py --mode temp --region 100,200,800,600
```

- Focused/active window (captures only the frontmost window; use `--app` to capture all windows):

```bash
python3 scripts/take_screenshot.py --mode temp --active-window
```

- Specific window id (use --list-windows on macOS to discover ids):

```bash
python3 scripts/take_screenshot.py --window-id 12345
```

The script prints one path per capture. When multiple windows or displays match, it prints multiple paths (one per line) and adds suffixes like `-w<windowId>` or `-d<display>`. View each path sequentially with the image viewer tool, and only manipulate images if needed or requested.

### Workflow examples

- "Take a look at <App> and tell me what you see": capture to temp, then view each printed path in order.

```bash
bash scripts/ensure_macos_permissions.sh && \
python3 scripts/take_screenshot.py --app "<App>" --mode temp
```

- "The design from Figma is not matching what is implemented": use a Figma MCP/skill to capture the design first, then capture the running app with this skill (typically to temp) and compare the raw screenshots before any manipulation.

### Multi-display behavior

- On macOS, full-screen captures save one file per display when multiple monitors are connected.
- On Linux and Windows, full-screen captures use the virtual desktop (all monitors in one image); use `--region` to isolate a single display when needed.

### Linux prerequisites and selection logic

The helper automatically selects the first available tool:

1) `scrot`
2) `gnome-screenshot`
3) ImageMagick `import`

If none are available, ask the user to install one of them and retry.

Coordinate regions require `scrot` or ImageMagick `import`.

`--app`, `--window-name`, and `--list-windows` are macOS-only. On Linux, use
`--active-window` or provide `--window-id` when available.

## Windows (PowerShell helper)

Run the PowerShell helper:

```powershell
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File scripts/take_screenshot.ps1
```

Common patterns:

- Default location:

```powershell
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File scripts/take_screenshot.ps1
```

- Temp location (Codex visual check):

```powershell
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File scripts/take_screenshot.ps1 -Mode temp
```

- Explicit path:

```powershell
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File scripts/take_screenshot.ps1 -Path "C:\Temp\screen.png"
```

- Pixel region (x,y,w,h):

```powershell
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File scripts/take_screenshot.ps1 -Mode temp -Region 100,200,800,600
```

- Active window (ask the user to focus it first):

```powershell
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File scripts/take_screenshot.ps1 -Mode temp -ActiveWindow
```

- Specific window handle (only when provided):

```powershell
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File scripts/take_screenshot.ps1 -WindowHandle 123456
```

## Direct OS commands (fallbacks)

Use these when you cannot run the helpers.

### macOS

- Full screen to a specific path:

```bash
screencapture -x output/screen.png
```

- Pixel region:

```bash
screencapture -x -R100,200,800,600 output/region.png
```

- Specific window id:

```bash
screencapture -x -l12345 output/window.png
```

- Interactive selection or window pick:

```bash
screencapture -x -i output/interactive.png
```

### Linux

- Full screen:

```bash
scrot output/screen.png
```

```bash
gnome-screenshot -f output/screen.png
```

```bash
import -window root output/screen.png
```

- Pixel region:

```bash
scrot -a 100,200,800,600 output/region.png
```

```bash
import -window root -crop 800x600+100+200 output/region.png
```

- Active window:

```bash
scrot -u output/window.png
```

```bash
gnome-screenshot -w -f output/window.png
```

## Error handling

- On macOS, run `bash scripts/ensure_macos_permissions.sh` first to request Screen Recording in one place.
- If you see "screen capture checks are blocked in the sandbox", "could not create image from display", or Swift `ModuleCache` permission errors in a sandboxed run, report the platform permission limitation; follow the host permission flow without silently disabling the sandbox or repeating an unknown operation.
- If macOS app/window capture returns no matches, run `--list-windows --app "AppName"` and retry with `--window-id`, and make sure the app is visible on screen.
- If Linux region/window capture fails, check tool availability with `command -v scrot`, `command -v gnome-screenshot`, and `command -v import`.
- If saving to the OS default location fails with permission errors in a sandbox, report the platform permission limitation; follow the host permission flow without silently disabling the sandbox or repeating an unknown operation.
- Report the saved location when requested. In agent-friend, use view_image for image inspection and automatic user preview; do not claim success from an exit code alone.
