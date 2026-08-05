from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
RUN_PATH = ROOT / "scripts" / "desktop-visual" / "run.py"


def _load_run_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_friend_desktop_visual", RUN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RUN = _load_run_module()


def test_parse_cdp_pair_result_uses_last_nonempty_json_line() -> None:
    payload = {"status": "captured", "captureStartedUnixMs": 123}
    result = RUN.parse_cdp_pair_result("\n" + json.dumps(payload) + "\n")

    assert result == payload


def test_parse_cdp_pair_result_rejects_non_capture_payload() -> None:
    try:
        RUN.parse_cdp_pair_result('{"status":"ready"}')
    except ValueError as error:
        assert "invalid result" in str(error)
    else:
        raise AssertionError("non-capture payload should fail")


def test_pair_capture_parser_defaults_to_local_cdp_and_bounded_lead() -> None:
    parser = RUN.build_parser()
    args = parser.parse_args(["pair-capture"])

    assert isinstance(args, argparse.Namespace)
    assert args.cdp == "http://127.0.0.1:9222"
    assert args.lead_ms == 2000
    assert args.output_dir is None
