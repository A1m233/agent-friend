from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

from agent.execution_environment import PythonEnvironmentProvider


def test_baseline_installs_offline_into_one_session_without_polluting_another(
    tmp_path: Path,
) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    provider = PythonEnvironmentProvider.from_runtime(
        data_root=tmp_path / "data", uv_executable=Path(uv)
    )
    health = provider.prepare_global()
    first = provider.prepare_session("first", health)
    other = provider.prepare_session("other", health)
    wheelhouse = tmp_path / "product" / "baseline-wheels"
    wheelhouse.mkdir(parents=True)
    dist = "baseline_fixture-1.0.dist-info"
    with zipfile.ZipFile(wheelhouse / "baseline_fixture-1.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr("baseline_fixture.py", "VALUE=139\n")
        wheel.writestr(
            f"{dist}/METADATA", "Metadata-Version: 2.1\nName: baseline-fixture\nVersion: 1.0\n"
        )
        wheel.writestr(
            f"{dist}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )
        wheel.writestr(
            f"{dist}/RECORD",
            f"baseline_fixture.py,,\n{dist}/METADATA,,\n{dist}/WHEEL,,\n{dist}/RECORD,,\n",
        )
    provider._base_root = tmp_path / "product" / "base"
    provider._packaged_manifest = {"baselineRequirements": ["baseline-fixture==1.0"]}
    provider._install_baseline(first.executable_aliases["python"])
    for environment, expected in ((first, True), (other, False)):
        subprocess.run(
            [
                str(environment.executable_aliases["python"]),
                "-I",
                "-B",
                "-c",
                f"import importlib.util; assert (importlib.util.find_spec('baseline_fixture') is not None) is {expected}",
            ],
            env=environment.environment,
            check=True,
            capture_output=True,
            timeout=15,
        )
