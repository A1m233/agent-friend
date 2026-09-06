"""Stable identity for notification facts, independent of conversation events."""

NOTIFICATION_EPISODE_PREFIX = "notification-presented:"
NOTIFICATION_SOURCE_PREFIX = "notification:"


def validate_notification_id(notification_id: str) -> None:
    """Accept an opaque, nonempty ID that cannot resemble a session source ref."""
    if (
        not notification_id
        or len(notification_id) > 256
        or any(char.isspace() or ord(char) < 32 or char == "#" for char in notification_id)
    ):
        raise ValueError("notification_id must be a nonempty opaque ID without whitespace or #")


def notification_episode_id(notification_id: str) -> str:
    validate_notification_id(notification_id)
    return f"{NOTIFICATION_EPISODE_PREFIX}{notification_id}"


def notification_source_ref(notification_id: str) -> str:
    validate_notification_id(notification_id)
    return f"{NOTIFICATION_SOURCE_PREFIX}{notification_id}"


def is_notification_episode_id(value: str) -> bool:
    """Recognize either a stored row ID or one search evidence ID wrapper."""
    value = value.removeprefix("episodic:")
    if not value.startswith(NOTIFICATION_EPISODE_PREFIX):
        return False
    try:
        validate_notification_id(value.removeprefix(NOTIFICATION_EPISODE_PREFIX))
    except ValueError:
        return False
    return True


def is_notification_episode_source(episode_id: str, source_ref: str) -> bool:
    """Recognize a matching pair; neither a prefix alone nor a fake session suffices."""
    if not is_notification_episode_id(episode_id):
        return False
    notification_id = episode_id.removeprefix("episodic:").removeprefix(NOTIFICATION_EPISODE_PREFIX)
    return source_ref == notification_source_ref(notification_id)
