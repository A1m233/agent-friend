"""Windows screenshot -> view_image smoke with product binaries and no LLM calls."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from agent.approvals import ApprovalBroker
from agent.attachments import AttachmentStore
from agent.command_runtime.runtime import CommandRuntime
from agent.command_runtime.tools import (
    COMMAND_RUN_CONTEXT_ARG,
    COMMAND_RUN_ID_ARG,
    COMMAND_SESSION_ID_ARG,
    COMMAND_SKILL_SNAPSHOT_ARG,
    COMMAND_SURFACE_ARG,
    COMMAND_TOOL_CALL_ID_ARG,
    ExecCommandTool,
)
from agent.command_runtime.vendor import verify_codex_executable
from agent.execution_environment.native import NativeTools
from agent.filesystem import FilesystemService, ProtectedPathPolicy
from agent.filesystem.tools import FILESYSTEM_LEDGER_ARG
from agent.image_reading import ImageReadingService
from agent.sessions.metadata import SessionMetadataStore
from agent.skills.models import SkillSelection
from agent.skills.service import SkillCatalogService
from agent.tool_runtime.context import ToolRunContext
from agent.tools.builtin.view_image import ViewImageTool
from agent.tools.protocol import IMAGE_INPUT_SUPPORTED_ARG


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--codex-home", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This smoke requires an existing Windows product runtime and sandbox setup.")
    root = Path(tempfile.mkdtemp(prefix="agent-friend-screenshot-smoke-"))
    report: dict[str, object] = {"status": "started", "llm_calls": 0, "region": "0,0,64,64"}
    native = NativeTools(args.runtime_root / "execution-environments/native")
    native.validate()
    # Some desktop tool hosts create TEMP folders with OWNER RIGHTS only.
    # Sandbox-owned output files must remain readable by the host, as they are
    # under the product's normal per-user data directory. Grant only this new,
    # initially empty fixture root; never alter the existing product ACLs.
    pwsh = native.resolve("pwsh")
    assert pwsh is not None
    sid = subprocess.run(
        [
            str(pwsh),
            "-NoProfile",
            "-Command",
            "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout.strip()
    if not sid.startswith("S-1-") or any(char not in "S0123456789-" for char in sid):
        raise RuntimeError("invalid_host_identity")
    subprocess.run(
        [
            str(Path(os.environ["SYSTEMROOT"]) / "System32/icacls.exe"),
            str(root),
            "/grant",
            f"*{sid}:(OI)(CI)(F)",
            "/Q",
        ],
        capture_output=True,
        check=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    codex = verify_codex_executable(args.runtime_root / "vendor/codex/bin/codex.exe")
    runtime = CommandRuntime(
        codex,
        codex_home=args.codex_home,
        execution_root=root / "execution",
        diagnostics_path=root / "diagnostics.json",
        payload_status="validated",
    )
    context = ToolRunContext.create("controlled screenshot smoke")
    skills = SkillCatalogService(
        personal_root=root / "skills", shared_root=root / "shared", watcher_enabled=False
    )
    uncertain = False
    try:
        if not runtime.windows_sandbox_ready:
            raise RuntimeError(
                "sandbox_not_ready: no setup or elevation is performed by this smoke"
            )
        skills.initialize()
        turn = skills.begin_turn((SkillSelection("builtin/screenshot"),))
        if turn.activation_issues or len(turn.activations) != 1:
            raise RuntimeError("screenshot_skill_activation_failed")
        broker = ApprovalBroker(
            mode_store=SessionMetadataStore(root / "metadata.json"),
            session_exists=lambda session_id: session_id == "smoke",
        )
        broker.set_session_mode("smoke", mode="session_auto", expected_version=0)
        tool = ExecCommandTool(
            runtime,
            broker,
            base_env={
                **os.environ,
                "AGENT_FRIEND_EXECUTION_ENVIRONMENT_ROOT": str(
                    args.runtime_root / "execution-environments"
                ),
            },
            personal_skills_root=root / "skills",
            session_workspaces_root=root / "workspaces",
        )
        result = tool.invoke(
            {
                "cmd": "pwsh.exe -NoProfile -ExecutionPolicy Bypass -File scripts/take_screenshot.ps1 -Region 0,0,64,64",
                "skill": {"entry": "scripts/take_screenshot.ps1", "workdir": "."},
                "mode": "buffered",
                "timeout_seconds": 30,
                "access": {"filesystem": "read_write", "network": "disabled"},
                "reason": "Controlled local screenshot and image snapshot verification",
                COMMAND_SESSION_ID_ARG: "smoke",
                COMMAND_RUN_ID_ARG: "smoke-run",
                COMMAND_TOOL_CALL_ID_ARG: "screenshot",
                COMMAND_SURFACE_ARG: "desktop_chat",
                COMMAND_RUN_CONTEXT_ARG: context,
                COMMAND_SKILL_SNAPSHOT_ARG: turn,
            }
        )
        payload = json.loads(result.text)
        report["command"] = payload
        uncertain = payload.get("error") in {
            "process_start_outcome_unknown",
            "process_termination_unconfirmed",
        }
        if result.is_error:
            raise RuntimeError(str(payload.get("error", "screenshot_failed")))
        images = list((root / "workspaces").rglob("*.png"))
        if len(images) != 1:
            raise RuntimeError("expected_one_screenshot")
        assets = AttachmentStore(root / "assets")
        viewer = ViewImageTool(
            FilesystemService(
                ProtectedPathPolicy(
                    product_roots=(), personal_skill_roots=(), builtin_skill_roots=()
                )
            ),
            ImageReadingService(assets),
        )
        viewed = viewer.invoke(
            {
                "filesystem_scope_id": payload["output_directory"]["filesystem_scope_id"],
                "path": images[0].name,
                FILESYSTEM_LEDGER_ARG: context.filesystem_authorizations,
                IMAGE_INPUT_SUPPORTED_ARG: True,
            }
        )
        if viewed.is_error or len(viewed.images) != 1:
            raise RuntimeError("view_image_failed: " + viewed.text)
        source = images[0]
        report["image"] = json.loads(viewed.text)
        report["asset"] = viewed.images[0].to_dict()
        source.unlink()
        context.close_commands()
        report["asset_after_source_cleanup"] = str(assets.verify(viewed.images[0].asset_id))
        report["status"] = "passed"
        return 0
    except Exception as exc:
        report.update(status="failed", error=str(exc), cleanup_unconfirmed=uncertain)
        return 1
    finally:
        if not uncertain:
            context.close_commands()
        runtime.close()
        skills.stop()
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        print(
            json.dumps(
                {"status": report["status"], "report": str(root / "report.json")},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
