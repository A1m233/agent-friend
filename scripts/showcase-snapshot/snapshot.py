from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT.parent / "agent-friend-showcase-snapshot"
DEFAULT_SYNC_BUILD = ROOT / ".tmp" / "showcase-snapshot-build"
SENTINEL = ".agent-friend-showcase-snapshot"


EXACT_ROOT_FILES = {
    ".gitignore",
    ".python-version",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "uv.lock",
}

EXACT_FRONTEND_FILES = {
    "frontend/.nvmrc",
    "frontend/components.json",
    "frontend/eslint.config.js",
    "frontend/package.json",
    "frontend/pnpm-lock.yaml",
    "frontend/pnpm-workspace.yaml",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.json",
    "frontend/tsconfig.node.json",
    "frontend/vite.config.ts",
    "frontend/vitest.config.ts",
}

ALLOW_PREFIXES = (
    ".cursor/rules/",
    ".cursor/skills/",
    ".Codex/skills/",
    ".claude/skills/",
    "agent/",
    "agent_bridge/",
    "llm_providers/",
    "memory/",
    "tools/",
    "runtime_config/",
    "voice_bridge/",
    "shared/",
    "frontend/src/",
    "frontend/src-tauri/",
    "frontend/public/",
    "frontend/scripts/",
)

MEMORY_EVAL_PREFIXES = (
    "memory_eval/src/",
    "memory_eval/tests/",
)

AGENT_EVAL_PREFIXES = ("agent_eval/src/",)

DOC_EXACT_FILES = {
    "docs/decisions/README.md",
    "docs/requirements/README.md",
}

DOC_SUFFIXES = (
    "/README.md",
    "/requirement.md",
    "/design.md",
    "/test-plan.md",
)

SCRIPT_DIR_ALLOWLIST = {
    "bridge",
    "check",
    "cli",
    "dev",
    "dev-bubble-spotcheck",
    "dev-fire-source",
    "dev-push-subscribe",
    "fix",
    "frontend",
    "im-smoke",
    "lint",
    "setup",
    "showcase-snapshot",
    "test",
    "typecheck",
    "voice",
}

DENY_PATTERNS = (
    "INTERVIEW.md",
    ".git/**",
    ".env",
    ".env.*",
    ".claude/settings.local.json",
    ".claude/worktrees/**",
    ".kilo/**",
    ".cache/**",
    ".venv/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/.mypy_cache/**",
    "**/node_modules/**",
    "**/.pnpm-store/**",
    "**/target/**",
    "**/dist/**",
    "**/*.tsbuildinfo",
    "client/**",
    "docs/issues/**",
    "docs/explorations/**",
    "docs/**/progress.md",
    "docs/requirements/**/smoke.py",
    "experiments/**",
    "memory_eval/data/**",
    "memory_eval/baselines/**",
    "scripts/codex-adapters/**",
    "scripts/dev-pet-bubble-demo/**",
    "scripts/memory-eval/**",
    "scripts/voice-smoke/**",
    "experiments/voice-poc/rtc-aigc-demo/Server/scenes/Custom.json",
)

TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".lock",
    ".md",
    ".mdc",
    ".mjs",
    ".ps1",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

SYNC_PRESERVE_ROOT_ENTRIES = {
    ".git",
    ".venv",
    ".tmp",
    ".env",
    ".agent-friend-data",
    ".showcase-run",
}
SYNC_PRESERVE_DIR_NAMES = {
    "node_modules",
}

EXTRA_WORKTREE_PREFIXES = (
    "scripts/showcase-snapshot",
    ".cursor/skills",
    ".Codex/skills",
    ".claude/skills",
)

PRIVATE_USER = "example" + "-user"
PRIVATE_DISPLAY_NAME = "Alex"
PRIVATE_MAC_HOME = "/Users/" + PRIVATE_USER
PRIVATE_WIN_HOME = r"C:\Users" + "\\" + "example-user"

SOURCE_SAFE_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(re.escape(PRIVATE_DISPLAY_NAME), re.IGNORECASE), "<example-user>"),
    (re.compile(re.escape(PRIVATE_USER), re.IGNORECASE), "example-user"),
    (re.compile(re.escape(PRIVATE_MAC_HOME)), "/Users/example"),
    (re.compile(re.escape(PRIVATE_WIN_HOME), re.IGNORECASE), r"C:\\Users\\example"),
    (
        re.compile(r"AppData\\Roaming\\agent-friend", re.IGNORECASE),
        r"AppData\\Roaming\\agent-friend-example",
    ),
]

DOC_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    *SOURCE_SAFE_REPLACEMENTS,
    (re.compile(r"im:qq:[A-Za-z0-9_-]+"), "im:qq:<example-openid>"),
    (re.compile(r"prod_session/[A-Za-z0-9:_#+ -]+"), "prod_session/<example-session>"),
    (re.compile(r"openid=([A-Fa-f0-9]{8})[A-Fa-f0-9]+"), r"openid=\1..."),
    (re.compile(r"user_openid\s*=\s*[A-Fa-f0-9]{8}[A-Fa-f0-9]+"), "user_openid = <example-openid>"),
    (
        re.compile(r"https://[A-Za-z0-9-]+\.trycloudflare\.com"),
        "https://example-tunnel.trycloudflare.com",
    ),
    (
        re.compile(r"https://[A-Za-z0-9-]+\.ngrok-free\.app"),
        "https://example-tunnel.ngrok-free.app",
    ),
]


@dataclass(frozen=True)
class Finding:
    severity: str
    category: str
    path: str
    line: int
    snippet: str


SCAN_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "error",
        "local absolute path",
        re.compile(
            r"("
            + re.escape(PRIVATE_MAC_HOME)
            + r"|"
            + re.escape(PRIVATE_WIN_HOME)
            + r"|AppData\\(?:Roaming|Local))",
            re.I,
        ),
    ),
    (
        "error",
        "private name",
        re.compile(
            r"\b(" + re.escape(PRIVATE_DISPLAY_NAME) + r"|" + re.escape(PRIVATE_USER) + r")\b", re.I
        ),
    ),
    (
        "error",
        "qq session id",
        re.compile(r"im:qq:(?!<example-openid>|OPENID(?:-|$))[A-Za-z0-9_-]+"),
    ),
    ("error", "prod session trace", re.compile(r"prod_session/(?!<example-session>)[^\s,`'\"]+")),
    (
        "error",
        "qq openid value",
        re.compile(r"(openid|user_openid)\s*[=:]\s*[A-Fa-f0-9]{16,}", re.I),
    ),
    (
        "error",
        "real tunnel url",
        re.compile(
            r"https://(?!example-tunnel\.)(?!xxx\.)[A-Za-z0-9-]+\.(trycloudflare\.com|ngrok-free\.app)",
            re.I,
        ),
    ),
    (
        "error",
        "credential-looking assignment",
        re.compile(
            r"\b[A-Z0-9_]*(?:API_KEY|ACCESS_KEY|SECRET_KEY|CLIENT_SECRET|SECRET|ACCESS_TOKEN|APP_ID|APPID)[A-Z0-9_]*"
            r"\s*[:=]\s*[\"']"
            r"(?!(?:your-|example|fake|test|placeholder|speech-token|speech-app-id|AKLT-test|<|xxx|xxxx|0123456789))"
            r"[A-Za-z0-9_./+=-]{12,}[\"']",
            re.I,
        ),
    ),
    (
        "error",
        "volc access key",
        re.compile(r"\bAKLT(?![-_ ]?(test|fake|example))[-A-Za-z0-9]{8,}", re.I),
    ),
    ("error", "openai key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("error", "tavily key", re.compile(r"\btvly-[A-Za-z0-9]{20,}\b")),
]

URL_PATTERN = re.compile(r"https?://[^\s)\"'<>`]+")
SELF_NAMED_CREDENTIAL_CONSTANT = re.compile(
    r"^\s*"
    r"([A-Z0-9_]*(?:API_KEY|ACCESS_KEY|SECRET_KEY|CLIENT_SECRET|SECRET|ACCESS_TOKEN|APP_ID|APPID)"
    r"[A-Z0-9_]*)"
    r"\s*=\s*[\"']\1[\"']\s*(?:#.*)?$",
    re.I,
)
ALLOWED_URL_HOSTS = (
    "127.0.0.1",
    "localhost",
    "example.com",
    "test.example.com",
    "a.example",
    "b.example",
    "github.com",
    "raw.githubusercontent.com",
    "pypi.org",
    "files.pythonhosted.org",
    "astral.sh",
    "rustup.rs",
    "platform.deepseek.com",
    "app.tavily.com",
    "console.volcengine.com",
    "rtc.volcengineapi.com",
    "unpkg.com",
    "q.qq.com",
    "example-tunnel.trycloudflare.com",
    "*.trycloudflare.com",
)


def rel(path: Path) -> str:
    return path.as_posix()


def is_denied(path: str) -> bool:
    if path == ".env.example":
        return False
    return any(fnmatch.fnmatch(path, pattern) for pattern in DENY_PATTERNS)


def is_frontend_root_html(path: str) -> bool:
    if not path.startswith("frontend/") or not path.endswith(".html"):
        return False
    return "/" not in path.removeprefix("frontend/")


def is_allowed(path: str) -> bool:
    if path in EXACT_ROOT_FILES or path in EXACT_FRONTEND_FILES or is_frontend_root_html(path):
        return True
    if path in {"pyproject.toml", ".env.example", "scripts/README.md"}:
        return True
    if path in {"agent_eval/README.md", "agent_eval/pyproject.toml"}:
        return True
    if path in {"memory_eval/README.md", "memory_eval/pyproject.toml"}:
        return True
    if path in DOC_EXACT_FILES:
        return True
    if path.startswith("docs/decisions/") and path.endswith("/README.md"):
        return True
    if path.startswith("docs/requirements/") and path.endswith(DOC_SUFFIXES):
        return True
    if path.startswith(MEMORY_EVAL_PREFIXES):
        return True
    if path.startswith(AGENT_EVAL_PREFIXES):
        return True
    if path.startswith("scripts/"):
        parts = path.split("/")
        return len(parts) >= 3 and parts[1] in SCRIPT_DIR_ALLOWLIST
    return path.startswith(ALLOW_PREFIXES)


def git_ls_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    paths = [p for p in result.stdout.decode("utf-8").split("\0") if p]
    extra = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z", *EXTRA_WORKTREE_PREFIXES],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    paths.extend(p for p in extra.stdout.decode("utf-8").split("\0") if p and p not in paths)
    return paths


def prepare_output(output: Path, replace: bool) -> None:
    if output.exists():
        if not replace:
            raise SystemExit(f"output exists: {output}. Pass --replace to regenerate it.")
        if not (output / SENTINEL).exists():
            raise SystemExit(
                f"refusing to replace non-snapshot directory without {SENTINEL}: {output}"
            )
        shutil.rmtree(output)
    output.mkdir(parents=True)
    (output / SENTINEL).write_text("generated by scripts/showcase-snapshot\n", encoding="utf-8")


def public_readme() -> str:
    return """# agent-friend 公开展示快照

这是 `agent-friend` 私有开发仓库的公开展示快照，用于简历、作品集和技术交流场景。

它不是原始开发仓库，也不承诺持续维护。公开版本经过脚本化处理：

- 不包含原始 git history
- 不包含本地凭据、私有数据、生成缓存和真实厂商 demo 配置
- 保留必要的 Coding Agent 工作流配置，用于展示工程协作和 harness 设计
- 通过 allowlist-first 的 snapshot pipeline 生成，并附带扫描报告

## 这个项目在做什么

`agent-friend` 是一个桌面陪伴型 AI 原型。目标不是做一个普通聊天工具，而是把
LLM、长期记忆、桌面形态、语音通道和多入口接入组合成一个“常驻桌面的 AI 朋友”。

当前公开快照能展示的重点包括：

- **对话引擎**：纯 Python 的多轮对话编排，包含 session、persona、system prompt 组装、
  tool calling、上下文裁剪 / 摘要压缩，以及用户消息编辑重发等交互能力。
- **长期记忆**：基于 SQLite 的记忆抽取、reconcile、持久化和检索召回，目标是跨会话保留
  用户事实、偏好和重要事件。
- **桌面前端**：Tauri 2 + React 的多窗口桌面应用，包含传统聊天页、Live2D 桌宠窗口、
  气泡窗口、设置页和记忆查看器。
- **服务边界**：`agent_bridge` 通过 HTTP/SSE 暴露 OpenAI-compatible 与 AG-UI 风格接口；
  `voice_bridge` 承载语音通话控制面、RTC 集成边界和 LLM proxy。
- **工程工作流**：需求 / 决策文档、脚本化开发入口、单元测试、记忆评测 harness，以及
  面向 Coding Agent 协作的 rules / skills。

## 仓库内容

主要模块：

| 路径 | 说明 |
| --- | --- |
| `agent/` | 对话引擎核心：conversation loop、session、persona、prompt、context、tools |
| `memory/` | 长期记忆系统：SQLite store、LLM 抽取、reconcile、检索召回 |
| `agent_eval/` | Agent 行为契约评测工具源码；公开快照不包含私有 case/report 资产 |
| `memory_eval/` | 记忆召回质量评测：benchmark adapter、LLM judge、baseline report |
| `llm_providers/` | LLM provider 适配层，当前通过 LiteLLM 收敛不同模型服务 |
| `runtime_config/` | 运行时配置与凭据解析：系统凭据、环境变量和桌面配置模式 |
| `agent_bridge/` | HTTP/SSE bridge，提供 OpenAI-compatible、AG-UI、IM 与内部 push 边界 |
| `voice_bridge/` | 语音通话控制面与 RTC / ASR / TTS 集成边界 |
| `frontend/` | Tauri 2 + React 桌面端：聊天、桌宠、气泡、设置、记忆查看器 |
| `docs/` | 产品愿景、技术决策、需求文档和配套设计 |
| `scripts/` | 跨平台开发脚本：setup、dev、check、test、lint、frontend 等 |
| `.cursor/`, `.Codex/`, `.claude/` | Coding Agent 规则、skills 与协作 harness |

可以优先看的入口：

- [`docs/decisions/0001-product-vision-and-roadmap/README.md`](docs/decisions/0001-product-vision-and-roadmap/README.md)：产品愿景和路线图
- [`docs/decisions/0002-incubation-tech-stack/README.md`](docs/decisions/0002-incubation-tech-stack/README.md)：孵化期技术栈与架构边界
- [`agent/README.md`](agent/README.md)、[`memory/README.md`](memory/README.md)：核心引擎和记忆模块说明
- [`frontend/src/pages/chat/`](frontend/src/pages/chat/) 与 [`frontend/src/pages/pet/`](frontend/src/pages/pet/)：桌面聊天页和桌宠窗口
- [`scripts/README.md`](scripts/README.md)：本地开发脚本清单

## 公开快照范围

这个仓库保留了能说明工程结构和核心实现的代码与文档，但不是完整私有开发环境：

- 没有原始 git history、issue 讨论、私有探索记录和本地调试缓存
- `.env.example` 只保留示例配置，不包含真实凭据
- IM、语音和厂商集成默认关闭或需要你自己的服务凭据
- `SHOWCASE-SCAN-REPORT.md` 是生成快照时的扫描报告，用于说明公开版本的脱敏结果

## 本地运行

先安装 Python 3.12 和 `uv`。如果要运行桌面前端，还需要 Node 22+、pnpm 和 Rust。

macOS / Linux：

```bash
./scripts/setup/run.sh
cp .env.example .env
# 在 .env 中填入必要的模型服务 key
```

Windows PowerShell：

```powershell
.\\scripts\\setup\\run.ps1
Copy-Item .env.example .env
# 在 .env 中填入必要的模型服务 key
```

启动桌面端：

```bash
./scripts/dev/run.sh
```

```powershell
.\\scripts\\dev\\run.ps1
```

这会同时启动本地 bridge 和 Tauri 桌面前端。首次运行会编译 Rust，时间会稍长。

如果只想快速验证聊天窗口，也可以使用浏览器调试模式：

```bash
./scripts/dev/run.sh --web
```

然后打开 `http://localhost:1420/chat.html`。

命令行调试入口是可选的：

```bash
./scripts/cli/run.sh
```

```powershell
.\\scripts\\cli\\run.ps1
```

公开版 `.env.example` 默认关闭 IM 和厂商集成，并把本地运行数据放在
`.agent-friend-data/` 下。填入 LLM provider key 后即可试用对话；可选厂商集成
请在换成你自己的凭据后再启用。

运行质量检查：

```bash
./scripts/check/run.sh
```

```powershell
.\\scripts\\check\\run.ps1
```

部分语音链路需要真实厂商凭据，不会在公开 snapshot pipeline 中执行。

## 快照来源

真实开发仓库保持 private。这个公开副本由 `scripts/showcase-snapshot/` 生成：
先按 allowlist 复制必要文件，再做规则化替换和隐私/凭据扫描，扫描通过后输出报告。
"""


def public_scripts_readme() -> str:
    return """# scripts/

Project operations are wrapped as scripts so contributors do not need to remember long
`uv`, `pytest`, or frontend commands.

| Script | Purpose | mac / linux | windows |
| --- | --- | --- | --- |
| `setup/` | Initialize the Python workspace, create `.env`, and check optional frontend tools | `./scripts/setup/run.sh` | `.\\scripts\\setup\\run.ps1` |
| `cli/` | Start the CLI debug UI | `./scripts/cli/run.sh` | `.\\scripts\\cli\\run.ps1` |
| `bridge/` | Start the HTTP/SSE bridge | `./scripts/bridge/run.sh` | `.\\scripts\\bridge\\run.ps1` |
| `dev/` | Start bridge plus frontend desktop/web development flow | `./scripts/dev/run.sh [--web]` | `.\\scripts\\dev\\run.ps1 [--web]` |
| `voice/` | Start the voice bridge control plane | `./scripts/voice/run.sh` | `.\\scripts\\voice\\run.ps1` |
| `test/` | Run pytest | `./scripts/test/run.sh` | `.\\scripts\\test\\run.ps1` |
| `lint/` | Run backend lint checks | `./scripts/lint/run.sh` | `.\\scripts\\lint\\run.ps1` |
| `typecheck/` | Run mypy | `./scripts/typecheck/run.sh` | `.\\scripts\\typecheck\\run.ps1` |
| `check/` | Run the combined local quality gate | `./scripts/check/run.sh` | `.\\scripts\\check\\run.ps1` |
| `frontend/*` | Install, lint, test, build, or run frontend tasks | `./scripts/frontend/*.sh` | `.\\scripts\\frontend\\*.ps1` |
| `im-smoke/` | Run the fake-LLM IM smoke test | `./scripts/im-smoke/run.sh` | `.\\scripts\\im-smoke\\run.ps1` |
| `showcase-snapshot/` | Regenerate or sync this sanitized public snapshot from the private source repo | `./scripts/showcase-snapshot/run.sh [--sync-checkout PATH]` | `.\\scripts\\showcase-snapshot\\run.ps1 [--sync-checkout PATH]` |

Scripts that intentionally trigger real LLM or vendor smoke calls are omitted from the public
snapshot command index.
"""


def sanitize_text(path: str, text: str) -> str:
    if path == "README.md":
        return public_readme()
    if path == "scripts/README.md":
        return public_scripts_readme()
    if path == "pyproject.toml":
        text = text.replace('    "experiments/agent-contract-eval-spike",\n', "")
    if path == "uv.lock":
        text = sanitize_uv_lock(text)
    if path == ".env.example":
        text = sanitize_env_example(text)

    replacements = DOC_REPLACEMENTS if is_public_doc_path(path) else SOURCE_SAFE_REPLACEMENTS
    for pattern, replacement in replacements:
        text = pattern.sub(replacement, text)
    return text


def sanitize_uv_lock(text: str) -> str:
    text = text.replace('    "agent-contract-eval-spike",\n', "")
    return re.sub(
        r'\n\[\[package\]\]\nname = "agent-contract-eval-spike"\n.*?(?=\n\[\[package\]\])',
        "\n",
        text,
        flags=re.S,
    )


def sanitize_env_example(text: str) -> str:
    public_empty_keys = {
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "TAVILY_API_KEY",
        "AGENT_API_TOKEN",
        "VOLC_ACCESS_KEY",
        "VOLC_SECRET_KEY",
        "VOLC_RTC_APP_ID",
        "VOLC_RTC_APP_KEY",
        "VOLC_ARK_API_KEY",
        "VOLC_ARK_ENDPOINT_ID",
        "VOLC_SPEECH_APP_ID",
        "VOLC_SPEECH_ACCESS_TOKEN",
        "VOICE_BRIDGE_PUBLIC_URL",
    }
    lines: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if match and match.group(1) in public_empty_keys:
            lines.append(f"{match.group(1)}=")
        else:
            lines.append(line)
    text = "\n".join(lines) + "\n"

    public_defaults = {
        "AGENT_FRIEND_DATA_DIR": ".agent-friend-data",
        "AGENT_BRIDGE_IM_ENABLED": "false",
    }
    for key, value in public_defaults.items():
        if re.search(rf"^{re.escape(key)}=", text, flags=re.M):
            text = re.sub(rf"^{re.escape(key)}=.*$", f"{key}={value}", text, flags=re.M)
        else:
            text += f"{key}={value}\n"
    return text


def is_public_doc_path(path: str) -> bool:
    return path.endswith((".md", ".mdc")) or path in {"AGENTS.md", "CLAUDE.md", ".env.example"}


def should_treat_as_text(path: Path) -> bool:
    if path.suffix in TEXT_SUFFIXES:
        return True
    return path.name in {
        ".env.example",
        ".gitignore",
        ".npmrc",
        ".prettierrc",
        ".stylelintrc",
        ".eslintrc",
    }


def copy_one(src_rel: str, output: Path) -> None:
    src = ROOT / src_rel
    dst = output / src_rel
    dst.parent.mkdir(parents=True, exist_ok=True)

    if src.is_symlink():
        target = os.readlink(src)
        os.symlink(target, dst)
        return

    if should_treat_as_text(src):
        try:
            text = src.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            shutil.copy2(src, dst)
            return
        dst.write_text(sanitize_text(src_rel, text), encoding="utf-8")
        shutil.copystat(src, dst, follow_symlinks=False)
        return

    shutil.copy2(src, dst)


def write_generated_files(output: Path) -> None:
    scan_dir = output / ".showcase-scan"
    scan_dir.mkdir(exist_ok=True)
    gitignore = output / ".gitignore"
    with gitignore.open("a", encoding="utf-8") as f:
        f.write("\n# showcase snapshot local artifacts\n")
        f.write(".showcase-scan/report.json\n")
        f.write(".agent-friend-data/\n")


def build_snapshot(output: Path) -> list[str]:
    copied: list[str] = []
    for path in git_ls_files():
        if is_denied(path) or not is_allowed(path):
            continue
        copy_one(path, output)
        copied.append(path)
    write_generated_files(output)
    return copied


def redacted(snippet: str) -> str:
    text = snippet.strip()
    text = re.sub(r"(=|:)\s*[A-Za-z0-9_./+=-]{12,}", r"\1 <redacted>", text)
    text = re.sub(r"sk-[A-Za-z0-9]{8,}", "sk-<redacted>", text)
    text = re.sub(r"tvly-[A-Za-z0-9]{8,}", "tvly-<redacted>", text)
    text = re.sub(r"AKLT[-A-Za-z0-9]{8,}", "AKLT-<redacted>", text)
    return text[:220]


def scan_text_file(path: Path, rel_path: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return findings

    for line_no, line in enumerate(text.splitlines(), start=1):
        for severity, category, pattern in SCAN_PATTERNS:
            if pattern.search(line):
                if (
                    category == "credential-looking assignment"
                    and SELF_NAMED_CREDENTIAL_CONSTANT.match(line)
                ):
                    continue
                findings.append(Finding(severity, category, rel_path, line_no, redacted(line)))

        for match in URL_PATTERN.finditer(line):
            url = match.group(0).rstrip(".,;")
            host = re.sub(r"^https?://", "", url).split("/", 1)[0].split(":", 1)[0]
            if host not in ALLOWED_URL_HOSTS:
                findings.append(
                    Finding("warning", "url inventory", rel_path, line_no, redacted(url))
                )

    return findings


def scan_snapshot(output: Path, paths: list[Path] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(output.rglob("*") if paths is None else paths):
        if path.is_symlink() or not path.is_file():
            continue
        rel_path = path.relative_to(output).as_posix()
        if rel_path in {".showcase-scan/report.json", "SHOWCASE-SCAN-REPORT.md"}:
            continue
        if rel_path == "scripts/showcase-snapshot/snapshot.py":
            continue
        if rel_path.endswith("experiments/voice-poc/rtc-aigc-demo/Server/scenes/Custom.json"):
            findings.append(
                Finding("error", "forbidden P0 path", rel_path, 0, "Custom.json must not ship")
            )
        if rel_path == ".claude/settings.local.json" or rel_path.startswith(".claude/worktrees/"):
            findings.append(Finding("error", "forbidden local claude state", rel_path, 0, rel_path))
        findings.extend(scan_text_file(path, rel_path))
    return findings


def write_report(output: Path, copied: list[str], findings: list[Finding]) -> None:
    created_at = datetime.now(timezone.utc).isoformat()  # noqa: UP017 - bootstrap supports Python 3.9.
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    report = {
        "created_at": created_at,
        "copied_file_count": len(copied),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "findings": [f.__dict__ for f in findings],
    }
    (output / ".showcase-scan").mkdir(exist_ok=True)
    (output / ".showcase-scan" / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Showcase Snapshot Scan Report",
        "",
        f"- Generated: `{created_at}`",
        f"- Copied files: `{len(copied)}`",
        f"- Errors: `{len(errors)}`",
        f"- Warnings: `{len(warnings)}`",
        "",
    ]
    if not findings:
        lines.append("No findings.")
    else:
        lines.extend(
            ["| Severity | Category | File | Line | Snippet |", "| --- | --- | --- | ---: | --- |"]
        )
        for finding in findings:
            lines.append(
                f"| {finding.severity} | {finding.category} | `{finding.path}` | "
                f"{finding.line} | `{finding.snippet.replace('|', '/')}` |"
            )
    lines.append("")
    (output / "SHOWCASE-SCAN-REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def normalized_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def ensure_inside(path: Path, root: Path) -> None:
    root_abs = normalized_absolute(root)
    path_abs = normalized_absolute(path)
    if path_abs != root_abs and root_abs not in path_abs.parents:
        raise SystemExit(f"refusing to touch path outside sync root: {path_abs}")


def sync_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_sync_preserved(rel_path: str) -> bool:
    if not rel_path:
        return True
    parts = rel_path.split("/")
    return parts[0] in SYNC_PRESERVE_ROOT_ENTRIES or any(
        part in SYNC_PRESERVE_DIR_NAMES for part in parts
    )


def remove_sync_path(path: Path, root: Path) -> int:
    ensure_inside(path, root)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    return 1


def ensure_parent_dir(path: Path, root: Path) -> None:
    ensure_inside(path, root)
    parent = path.parent
    if parent.exists() and not parent.is_dir():
        remove_sync_path(parent, root)
    parent.mkdir(parents=True, exist_ok=True)


def mirror_symlink(src: Path, dst: Path, root: Path) -> bool:
    link_target = os.readlink(src)
    if dst.exists() or dst.is_symlink():
        remove_sync_path(dst, root)
    ensure_parent_dir(dst, root)
    target_is_directory = (src.parent / link_target).is_dir()
    try:
        os.symlink(link_target, dst, target_is_directory=target_is_directory)
        return True
    except OSError:
        # Windows checkouts without symlink privilege use git's text placeholder form.
        dst.write_text(link_target, encoding="utf-8")
        return False


def mirror_file(src: Path, dst: Path, root: Path) -> None:
    if dst.is_symlink() or (dst.exists() and not dst.is_file()):
        remove_sync_path(dst, root)
    ensure_parent_dir(dst, root)
    shutil.copy2(src, dst)


def source_inventory(snapshot: Path) -> tuple[set[str], set[str], dict[str, Path]]:
    dirs = {""}
    files: set[str] = set()
    file_paths: dict[str, Path] = {}
    for path in sorted(snapshot.rglob("*")):
        rel_path = sync_rel(path, snapshot)
        if path.is_symlink():
            files.add(rel_path)
            file_paths[rel_path] = path
        elif path.is_dir():
            dirs.add(rel_path)
        else:
            files.add(rel_path)
            file_paths[rel_path] = path
    return dirs, files, file_paths


def remove_checkout_extras(checkout: Path, source_dirs: set[str], source_files: set[str]) -> int:
    removed = 0
    for path in sorted(checkout.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        rel_path = sync_rel(path, checkout)
        if is_sync_preserved(rel_path):
            continue
        if path.is_dir() and not path.is_symlink():
            if rel_path not in source_dirs:
                try:
                    path.rmdir()
                    removed += 1
                except OSError:
                    pass
            continue
        if rel_path not in source_files:
            removed += remove_sync_path(path, checkout)
    return removed


def checkout_git(checkout: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=checkout,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode:
        raise SystemExit(f"cannot inspect showcase checkout {checkout}: {result.stderr.strip()}")
    return result.stdout


def validate_checkout(checkout: Path, *, require_clean: bool = False) -> Path:
    checkout = checkout.expanduser().resolve()
    if not checkout.is_dir():
        raise SystemExit(f"showcase checkout does not exist: {checkout}")
    if not (checkout / ".git").exists():
        raise SystemExit(f"refusing showcase checkout without .git: {checkout}")
    if not (checkout / SENTINEL).is_file():
        raise SystemExit(f"refusing showcase checkout without {SENTINEL}: {checkout}")
    git_root = Path(checkout_git(checkout, "rev-parse", "--show-toplevel").strip()).resolve()
    if git_root != checkout:
        raise SystemExit(f"showcase path is not the Git root: {checkout}")
    if require_clean:
        status = checkout_git(checkout, "status", "--porcelain=v1", "--untracked-files=all")
        if status.strip():
            raise SystemExit(
                f"refusing to sync dirty showcase checkout: {checkout}\n{status.rstrip()}\n"
                "Preserve or commit these changes before retrying; sync does not discard them."
            )
    return checkout


def verify_checkout(checkout: Path) -> int:
    checkout = validate_checkout(checkout)
    names = checkout_git(checkout, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    paths = [checkout / name for name in sorted(set(names.split("\0")) - {""})]
    for path in paths:
        if not path.is_symlink() and checkout not in path.resolve().parents:
            raise SystemExit(f"refusing to scan a path outside showcase checkout: {path}")
    findings = scan_snapshot(checkout, paths)
    errors = sum(f.severity == "error" for f in findings)
    warnings = sum(f.severity == "warning" for f in findings)
    print(f"verified checkout: {checkout}")
    print(f"scan errors: {errors}")
    print(f"scan warnings: {warnings}")
    for finding in findings:
        print(
            f"{finding.severity}: {finding.path}:{finding.line} "
            f"[{finding.category}] {finding.snippet}"
        )
    return 1 if errors else 0


def validate_sync_paths(snapshot: Path, checkout: Path) -> None:
    snapshot = snapshot.expanduser().resolve()
    checkout = checkout.expanduser().resolve()
    if snapshot == checkout or snapshot in checkout.parents or checkout in snapshot.parents:
        raise SystemExit("snapshot output and sync checkout must not overlap")


def sync_snapshot_to_checkout(snapshot: Path, checkout: Path) -> tuple[int, int, int]:
    validate_sync_paths(snapshot, checkout)
    checkout = validate_checkout(checkout, require_clean=True)

    source_dirs, source_files, file_paths = source_inventory(snapshot)
    removed = remove_checkout_extras(checkout, source_dirs, source_files)

    for rel_path in sorted(source_dirs):
        if rel_path and not is_sync_preserved(rel_path):
            (checkout / rel_path).mkdir(parents=True, exist_ok=True)

    copied = 0
    symlink_fallbacks = 0
    for rel_path, src in sorted(file_paths.items()):
        if is_sync_preserved(rel_path):
            continue
        dst = checkout / rel_path
        if src.is_symlink():
            if not mirror_symlink(src, dst, checkout):
                symlink_fallbacks += 1
        else:
            mirror_file(src, dst, checkout)
        copied += 1

    if symlink_fallbacks:
        subprocess.run(["git", "config", "core.symlinks", "false"], cwd=checkout, check=False)

    return copied, removed, symlink_fallbacks


def init_git(output: Path) -> None:
    subprocess.run(["git", "init"], cwd=output, check=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a sanitized public showcase snapshot.")
    parser.add_argument("--output", type=Path, default=None, help="snapshot output directory")
    parser.add_argument(
        "--replace", action="store_true", help="replace an existing generated snapshot"
    )
    parser.add_argument(
        "--init-git", action="store_true", help="run git init in the generated snapshot"
    )
    parser.add_argument(
        "--verify-checkout",
        type=Path,
        help="scan an existing showcase checkout without generating or writing files",
    )
    parser.add_argument(
        "--sync-checkout",
        type=Path,
        help=(
            "after a clean scan, mirror the generated snapshot into an existing showcase "
            "clean git checkout while preserving ignored local runtime artifacts"
        ),
    )
    args = parser.parse_args(argv)
    if args.verify_checkout is not None and (
        args.output is not None or args.replace or args.init_git or args.sync_checkout is not None
    ):
        parser.error(
            "--verify-checkout cannot be combined with snapshot generation or sync options"
        )
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.verify_checkout is not None:
        return verify_checkout(args.verify_checkout)
    if args.sync_checkout and args.init_git:
        raise SystemExit("--sync-checkout cannot be combined with --init-git")

    output_arg = args.output
    if output_arg is None:
        output_arg = DEFAULT_SYNC_BUILD if args.sync_checkout else DEFAULT_OUTPUT
    output = output_arg.expanduser().resolve()
    if args.sync_checkout:
        validate_sync_paths(output, args.sync_checkout)
        validate_checkout(args.sync_checkout, require_clean=True)
    prepare_output(output, args.replace or bool(args.sync_checkout))
    copied = build_snapshot(output)
    findings = scan_snapshot(output)
    write_report(output, copied, findings)

    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    print(f"snapshot: {output}")
    print(f"copied files: {len(copied)}")
    print(f"scan errors: {len(errors)}")
    print(f"scan warnings: {len(warnings)}")
    print(f"report: {output / 'SHOWCASE-SCAN-REPORT.md'}")
    if errors:
        return 1

    if args.init_git:
        init_git(output)

    if args.sync_checkout:
        synced, removed, symlink_fallbacks = sync_snapshot_to_checkout(output, args.sync_checkout)
        print(f"synced checkout: {args.sync_checkout.expanduser().resolve()}")
        print(f"synced files: {synced}")
        print(f"removed stale paths: {removed}")
        print(f"symlink text fallbacks: {symlink_fallbacks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
