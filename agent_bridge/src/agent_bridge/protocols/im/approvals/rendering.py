"""Lossless approval details; unrepresentable or long requests use the desktop."""

from __future__ import annotations

import unicodedata

# Application budget, deliberately conservative; not a claimed QQ platform limit.
MAX_APPROVAL_BYTES = 1800
RISK_TEXT = (
    "开启后，本会话（包括桌面端）后续普通请求将不再逐项询问。\n"
    "Agent 可以读取、创建、修改、覆盖、移动和删除本地文件，运行命令，"
    "使用所申请的网络能力，并执行显式申请的 host_user 非沙箱命令。\n"
    "现有普通待审批请求也会自动允许；灾难性高危请求仍会拒绝。\n"
    "推荐继续使用“每次授权”。只有理解上述风险才确认。"
)


def mode_label(mode: str) -> str:
    return "本会话无需授权（高风险）" if mode == "session_auto" else "每次授权（推荐）"


def markdown_literal(value: str) -> str:
    # QQ interprets backslash-escaped square brackets as LaTeX delimiters.
    # Numeric character references render literally while preserving buttons.
    special = frozenset("\\`*_{}[]()<>#+.!|~&-$")
    return "".join(f"&#{ord(char)};" if char in special else char for char in plain_literal(value))


def bounded(text: str) -> str:
    if len(text.encode("utf-8")) > MAX_APPROVAL_BYTES:
        raise ValueError("approval text exceeds presentation budget")
    return text


_LABELS = {
    "items": "文件范围",
    "actions": "破坏性操作",
    "item_id": "项目",
    "action_id": "操作标识",
    "path": "路径",
    "target": "目标",
    "root_kind": "对象类型",
    "permissions": "申请权限",
    "action": "操作",
    "recursive": "包含子目录",
    "cmd": "命令",
    "argv": "实际程序与参数",
    "execution": "执行方式",
    "workdir": "工作目录",
    "cwd_source": "目录来源",
    "mode": "运行方式",
    "timeout_seconds": "超时（秒）",
    "filesystem_access": "文件权限",
    "additional_roots": "额外授权目录",
    "host_derived_roots": "宿主附加目录",
    "access": "权限",
    "network_access": "网络权限",
    "interactive": "交互输入",
    "skill": "技能",
    "skill_id": "技能标识",
    "entry": "技能入口",
    "fingerprint": "内容指纹",
    "execution_environment": "执行环境",
}
_ENUMS = {
    "root_kind": {"file": "文件", "directory": "目录"},
    "permissions": {
        "create": "新建",
        "edit": "修改",
        "read_text": "读取文本",
        "read_image": "读取图片",
        "search_metadata": "查找名称与路径",
    },
    "action": {"overwrite": "覆盖", "move": "移动", "delete": "删除"},
    "execution": {"sandboxed": "沙箱", "host_user": "宿主用户（非沙箱）"},
    "cwd_source": {
        "explicit": "本次指定",
        "session_managed": "会话目录",
        "skill_snapshot": "技能快照",
    },
    "mode": {"buffered": "等待完成", "streaming": "持续输出"},
    "filesystem_access": {
        "read_only": "只读",
        "read_write": "读写",
        "unrestricted": "不受沙箱限制",
    },
    "access": {"read_only": "只读", "read_write": "读写", "unrestricted": "不受沙箱限制"},
    "network_access": {"disabled": "关闭", "enabled": "开启", "unrestricted": "不受沙箱限制"},
}


def plain_literal(value: str) -> str:
    # Validate source values before the final Markdown transport encoding.
    if any(unicodedata.category(char) in {"Cf", "Cs"} for char in value):
        raise ValueError("unrepresentable approval text")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError("control character in approval text")
    return value


def _fields(value: object, *, key: str = "", label: str = "") -> list[str]:
    if isinstance(value, dict):
        lines = [label + "："] if label else []
        for child_key, child in value.items():
            lines.extend(
                _fields(
                    child, key=str(child_key), label=_LABELS.get(str(child_key), str(child_key))
                )
            )
        return lines
    if isinstance(value, (list, tuple)):
        if not value:
            return [label + "：无"]
        lines = []
        for index, child in enumerate(value):
            child_label = (
                ("程序" if index == 0 else f"参数 {index}")
                if key == "argv"
                else f"{label} {index + 1}"
            )
            lines.extend(_fields(child, key=key, label=child_label))
        return lines
    if value is None:
        rendered = "无"
    elif isinstance(value, bool):
        rendered = "是" if value else "否"
    else:
        rendered = str(value)
        rendered = _ENUMS.get(key, {}).get(rendered, rendered)
        if rendered == "":
            rendered = "（空字符串）"
    return [f"{label}：{plain_literal(rendered)}"]


def approval_text(snapshot: dict[str, object], title: str) -> str:
    kind = snapshot.get("kind")
    details: object
    if kind == "filesystem":
        details = {key: snapshot[key] for key in ("items", "actions")}
    elif kind == "command":
        details = snapshot["details"]
    else:
        raise ValueError("unsupported approval kind")
    body = "\n".join(_fields(details))
    risk = snapshot.get("risk")
    risk_text = ""
    if isinstance(risk, dict) and risk.get("level") == "catastrophic":
        risk_text = "\n高危请求：请仔细检查；免授权模式不会自动允许此项。"
    text = (
        "本地操作等待授权\n"
        + plain_literal(f"会话：{title}\n")
        + mode_label(str(snapshot.get("approval_mode")))
        + "\n"
        + plain_literal(f"理由：{snapshot.get('reason', '')}\n\n{body}")
        + risk_text
        + "\n仅允许本次完整范围。批准后仍需等待执行结果；请求约 5 分钟内有效。"
    )
    return bounded(markdown_literal(text))
