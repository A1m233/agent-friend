"""Start a real Tauri worktree with isolated data and a deterministic model double."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    data = (args.data_dir or Path(tempfile.mkdtemp(prefix="agent-friend-154-desktop-"))).resolve()
    data.mkdir(parents=True, exist_ok=True)
    marker = data / ".scheduled-request-desktop"
    if any(data.iterdir()) and not marker.is_file():
        parser.error("--data-dir must be empty or owned by scheduled-request-desktop")
    marker.write_text("isolated model-double fixture\n", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        {
            "AGENT_FRIEND_DATA_DIR": str(data),
            "AGENT_FRIEND_LOG_DIR": str(data / "logs"),
            "AGENT_FRIEND_CONFIG_MODE": "env-only",
            "AGENT_BRIDGE_MEMORY_ENABLED": "false",
            "AGENT_BRIDGE_IM_ENABLED": "false",
            "AGENT_FRIEND_DESKTOP_TEST_DOUBLE": str(data),
            "DEEPSEEK_API_KEY": "desktop-test-not-a-real-credential",
            "LLM_CHAT_MODEL": "deepseek/deepseek-flash",
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            "PYTHONPATH": str(Path(__file__).parent / "model_double"),
        }
    )
    proof = (
        "from llm_providers import LLMClient; assert LLMClient.stream.__module__ == 'sitecustomize'"
    )
    subprocess.run([sys.executable, "-c", proof], env=env, cwd=ROOT, check=True)
    print(f"desktop_test_data={data}", flush=True)
    print(
        "Model double enabled. No real provider requests; ordinary Tauri and Bridge code.",
        flush=True,
    )
    if os.name == "nt":
        command = [
            "powershell",
            "-NoProfile",
            "-File",
            str(ROOT / "scripts/desktop-visual/run.ps1"),
            "start",
        ]
    else:
        command = [str(ROOT / "scripts/desktop-visual/run.sh"), "start"]
    return subprocess.call(command, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
