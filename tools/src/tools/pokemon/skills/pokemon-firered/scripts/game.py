"""Personal Skill entry; stdlib only, no model calls."""

import sys
from pathlib import Path

sys.dont_write_bytecode = True

from pokemon_runtime.commands import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).parent.parent / "binding.json"))
