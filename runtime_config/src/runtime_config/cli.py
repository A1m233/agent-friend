"""Command-line credential management for local development."""

from __future__ import annotations

import argparse
import getpass
from collections.abc import Sequence

from .credentials import ALL_CREDENTIAL_NAMES, CREDENTIAL_BUNDLES
from .policy import ConfigMode, credential_source_for_mode, normalize_config_mode
from .sources import KeyringSource


def _credential_name(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in ALL_CREDENTIAL_NAMES:
        known = ", ".join(ALL_CREDENTIAL_NAMES)
        raise argparse.ArgumentTypeError(f"未知凭据 {value!r}；可选：{known}")
    return normalized


def _config_mode(value: str) -> ConfigMode:
    try:
        return normalize_config_mode(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def _print_status(mode: ConfigMode) -> None:
    source = credential_source_for_mode(mode)
    for name in ALL_CREDENTIAL_NAMES:
        status = source.status(name)
        state = "set" if status.configured else "missing"
        source_label = status.source or "-"
        suffix = f" error={status.error}" if status.error and not status.configured else ""
        print(f"{name:30} {state:8} source={source_label}{suffix}")


def _set_one(store: KeyringSource, name: str) -> None:
    value = getpass.getpass(f"{name}: ")
    if not value.strip():
        print(f"{name}: skipped empty value")
        return
    store.set(name, value.strip())
    print(f"{name}: saved to system keyring")


def _set_bundle(store: KeyringSource, bundle: str) -> None:
    for name in CREDENTIAL_BUNDLES[bundle]:
        _set_one(store, name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-friend-credentials",
        description="Manage agent-friend credentials in the system keyring.",
    )
    parser.add_argument(
        "--mode",
        type=_config_mode,
        default="desktop",
        help="status source mode: desktop, legacy-dotenv, env-only",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="show credential status without printing values")

    set_parser = subparsers.add_parser("set", help="write one credential to system keyring")
    set_parser.add_argument("name", type=_credential_name)

    delete_parser = subparsers.add_parser(
        "delete", help="delete one credential from system keyring"
    )
    delete_parser.add_argument("name", type=_credential_name)

    bundle_parser = subparsers.add_parser("set-bundle", help="write a known credential bundle")
    bundle_parser.add_argument("bundle", choices=sorted(CREDENTIAL_BUNDLES))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode: ConfigMode = args.mode

    if args.command == "status":
        _print_status(mode)
        return 0

    store = KeyringSource()
    if args.command == "set":
        _set_one(store, args.name)
        return 0
    if args.command == "delete":
        store.delete(args.name)
        print(f"{args.name}: deleted from system keyring")
        return 0
    if args.command == "set-bundle":
        _set_bundle(store, args.bundle)
        return 0

    raise AssertionError(f"unhandled command: {args.command}")
