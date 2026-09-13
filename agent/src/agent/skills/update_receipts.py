"""Run-owned receipts from the exact bundled installer, not from command stdout."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from .builtin import BUILTIN_PACKAGE
from .models import SkillTurnSnapshot
from .package_snapshot import package_digest

if TYPE_CHECKING:
    from ..command_runtime.models import CommandApprovalDetails, CommandProcessSnapshot
    from ..tool_runtime.context import ToolRunContext

RECEIPT_ENV = "AGENT_FRIEND_SKILL_UPDATE_RECEIPT"
NONCE_ENV = "AGENT_FRIEND_SKILL_UPDATE_NONCE"


@dataclass(frozen=True)
class SkillUpdateResult:
    skill_id: str
    previous_version: str
    version: str


@dataclass
class PendingSkillUpdate:
    skill_id: str
    destination: Path
    receipt: Path
    nonce: str
    before_digest: str = ""
    consumed: bool = False

    def consume(self) -> SkillUpdateResult | None:
        if self.consumed:
            return None
        self.consumed = True
        if not self.receipt.exists():
            if self.before_digest and package_digest(self.destination) != self.before_digest:
                raise ValueError("Skill changed but the update receipt is missing")
            return None
        if self.receipt.is_symlink() or self.receipt.stat().st_size > 8192:
            raise ValueError("Invalid Skill update receipt")
        raw = json.loads(self.receipt.read_text(encoding="utf-8"))
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != 1
            or raw.get("nonce") != self.nonce
            or raw.get("skill_name") != self.destination.name
            or raw.get("root") != str(self.destination.parent)
            or raw.get("status") not in {"committed", "already_up_to_date"}
        ):
            raise ValueError("Skill update receipt does not match this operation")
        version = raw.get("new_sha256")
        previous = raw.get("old_sha256")
        if not isinstance(version, str) or not isinstance(previous, str):
            raise ValueError("Skill update receipt has no versions")
        if self.destination.is_symlink() or package_digest(self.destination) != version:
            raise ValueError("Updated Skill no longer matches the committed version")
        return SkillUpdateResult(self.skill_id, previous, version)


def prepare_update_receipt(
    details: CommandApprovalDetails,
    snapshot: SkillTurnSnapshot | None,
    *,
    personal_root: Path,
    run_temp: Path,
) -> PendingSkillUpdate | None:
    """Recognize only a direct, managed Python invocation of our unchanged installer."""
    binding = details.skill
    managed = details.managed_command
    if binding is None or managed is None or snapshot is None:
        return None
    activation = next((a for a in snapshot.activations if a.record.id == binding.skill_id), None)
    if (
        activation is None
        or activation.record.source != "builtin"
        or activation.record.name != "skill-installer"
        or binding.entry != "scripts/install-skill-from-github.py"
        or managed.provider.identity.provider != "python"
    ):
        return None
    argv = managed.display_argv
    if len(argv) < 4 or Path(argv[1]).name != "install-skill-from-github.py":
        return None
    script = Path(argv[1])
    if not script.is_absolute():
        if details.launch_workdir is None:
            return None
        script = (Path(details.launch_workdir) / script).resolve()
    bundle = resources.files(BUILTIN_PACKAGE).joinpath("skill-installer", "scripts")
    for filename in ("install-skill-from-github.py", "managed_install.py", "github_utils.py"):
        candidate = script.parent / filename
        if (
            candidate.is_symlink()
            or candidate.read_bytes() != bundle.joinpath(filename).read_bytes()
        ):
            return None
    options: dict[str, str] = {}
    index = 2
    while index < len(argv):
        flag = argv[index]
        if flag == "--replace-local-changes":
            index += 1
            continue
        if flag not in {"--update", "--ref", "--method", "--dest"} or flag in options:
            return None
        if index + 1 >= len(argv):
            return None
        options[flag] = argv[index + 1]
        index += 2
    name = options.get("--update")
    if not name or Path(name).name != name or name in {".", ".."}:
        return None
    root = personal_root.resolve()
    if "--dest" in options and Path(options["--dest"]).resolve() != root:
        return None
    target = next(
        (
            r
            for r in snapshot.catalog.entries
            if r.source == "personal" and r.package_root.resolve() == root / name
        ),
        None,
    )
    if target is None:
        return None
    return PendingSkillUpdate(
        target.id,
        root / name,
        run_temp / f"skill-update-{uuid4().hex}.json",
        uuid4().hex,
        package_digest(root / name),
    )


def consume_process_update(
    context: ToolRunContext,
    snapshot: CommandProcessSnapshot,
) -> dict[str, object]:
    pending = context.command_resource(f"skill-update:{snapshot.process_id}")
    if not isinstance(pending, PendingSkillUpdate) or not snapshot.terminal():
        return {}
    try:
        result = pending.consume()
    except (OSError, ValueError) as exc:
        return {"_skill_update_failed": pending.skill_id, "skill_update_error": str(exc)}
    return {"_skill_update": result} if result is not None else {}
