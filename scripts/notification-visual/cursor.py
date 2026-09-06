"""Move the real Windows cursor so the pet's native passthrough feed observes it.

Other platforms use the CDP input fallback in run.mjs; this helper is Windows only.
"""

import argparse
import ctypes
import sys

parser = argparse.ArgumentParser()
parser.add_argument("x", type=int)
parser.add_argument("y", type=int)
args = parser.parse_args()
if sys.platform != "win32":
    raise SystemExit("native cursor positioning is Windows-only")
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
if not ctypes.windll.user32.SetCursorPos(args.x, args.y):
    raise OSError("SetCursorPos failed")
