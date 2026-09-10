"""OpenAI-wire effort values, independent of vendor model catalogs."""


def custom_effort(preset: str) -> str | None:
    # Preserve the pre-existing custom-model default without conflating it with
    # an explicit upstream effort value of "none".
    if preset == "none":
        return None
    if not isinstance(preset, str) or not preset.startswith("effort:"):
        raise ValueError("Invalid custom reasoning selection")
    value = preset.removeprefix("effort:")
    if (
        not value
        or value != value.strip()
        or len(value) > 128
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("Invalid custom reasoning effort")
    return value
