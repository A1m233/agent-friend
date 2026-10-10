from __future__ import annotations

import os

import pytest
from resource_view import junction_view


@pytest.mark.skipif(os.name != "nt", reason="Windows junction resource adapter")
@pytest.mark.parametrize("failed", [False, True])
def test_resource_view_removes_only_link_and_preserves_payload(tmp_path, failed):
    target = tmp_path / "build"
    target.mkdir()
    payload = target / "keep.bin"
    payload.write_bytes(b"payload")
    parent = tmp_path / "views"
    try:
        with junction_view(target, parent) as view:
            assert view.is_junction()
            assert (view / "keep.bin").read_bytes() == b"payload"
            if failed:
                raise RuntimeError("build failed")
    except RuntimeError as error:
        assert failed and str(error) == "build failed"
    assert payload.read_bytes() == b"payload"
    assert list(parent.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows junction resource adapter")
def test_resource_view_refuses_to_remove_replaced_entry(tmp_path):
    target = tmp_path / "build"
    target.mkdir()
    with (
        pytest.raises(RuntimeError, match="changed; refusing cleanup"),
        junction_view(target, tmp_path / "views") as view,
    ):
        view.rmdir()
        view.mkdir()
        (view / "unexpected").write_bytes(b"keep")
    assert target.is_dir()
    assert (view / "unexpected").read_bytes() == b"keep"
