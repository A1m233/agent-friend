"""Shared user-facing failures for automatic and explicit session-file preparation."""

FILE_ERROR_MESSAGES = {
    "session_context_missing": "当前会话上下文不可用，不能访问上传文件。",
    "session_not_found": "当前会话已不存在，不能继续读取附件。",
    "session_file_not_found": "当前会话没有此有效 file_ref。重新 list 并使用返回的引用，不要猜测其他会话引用。",
    "session_file_reference_invalid": "持久附件引用损坏，请重新上传；不要用同名文件替代。",
    "attachment_asset_missing": "附件原件缺失，请重新上传；不能用已有副本或同名本地文件冒充原件。",
    "attachment_integrity_failed": "附件原件缺失或校验失败，请重新上传；不能用已有副本或同名本地文件冒充原件。",
    "session_file_too_large": "附件超过当前文件大小限制，不能准备副本。请提供较小文件。",
    "session_workspace_quota_exceeded": "会话工作目录容量不足，未创建副本。原件仍保留；清理不再需要的工作文件后重试。",
    "session_workspace_unsafe_path": "工作目录或输入路径存在链接等不安全对象。停止，待路径修复后再准备。",
    "session_file_io_error": "文件读写失败。请检查磁盘空间和访问权限，解决后再重试。",
}
