"""One-shot launch of applications owned by the interactive desktop."""

from .launcher import NativeApplicationLauncher
from .tool import LaunchDesktopApplicationTool

__all__ = ["LaunchDesktopApplicationTool", "NativeApplicationLauncher"]
