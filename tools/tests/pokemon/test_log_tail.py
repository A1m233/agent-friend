from pathlib import Path

from tools.pokemon.log_tail import RotatingLogTail, shared_reader


def test_rotation_while_open_does_not_block_or_lose_lines(tmp_path: Path) -> None:
    path = tmp_path / "agent_bridge.log"
    path.write_bytes(b"old\n")
    tail = RotatingLogTail(path)
    with path.open("ab") as stream:
        stream.write("new中".encode())
    assert tail.readlines() == []
    with shared_reader(path):
        path.rename(tmp_path / "agent_bridge.log.1")
        path.write_bytes(b"next\n")
    with (tmp_path / "agent_bridge.log.1").open("ab") as stream:
        stream.write(b"\n")
    assert tail.readlines() == ["new中", "next"]
    assert tail.readlines() == []


def test_multiple_rotations_and_new_log(tmp_path: Path) -> None:
    path = tmp_path / "agent_bridge.log"
    tail = RotatingLogTail(path)
    path.write_bytes(b"one\n")
    path.rename(tmp_path / "agent_bridge.log.2")
    path.write_bytes(b"two\n")
    path.rename(tmp_path / "agent_bridge.log.1")
    path.write_bytes(b"three\n")
    assert tail.readlines() == ["one", "two", "three"]
    assert tail.readlines() == []
