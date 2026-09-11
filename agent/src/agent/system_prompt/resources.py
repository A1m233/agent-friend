"""Shared packaged prompt resources; no conversation or persona dependencies."""

from importlib.resources import files


def load_prompt_resource(filename: str) -> str:
    """Load a trusted, code-selected filename from the existing resource package."""
    return files("agent.prompt_sections").joinpath(filename).read_text(encoding="utf-8").strip()


def load_identity_contract() -> str:
    """One identity contract for every entry point speaking as the friend."""
    return load_prompt_resource("identity_continuity.md")
