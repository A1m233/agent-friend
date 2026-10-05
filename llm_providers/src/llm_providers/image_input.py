"""Narrow image-modality error classification at the provider boundary."""


def is_image_input_rejection(message: str) -> bool:
    text = message.lower()
    return any(
        marker in text
        for marker in (
            "image input is not supported",
            "image inputs are not supported",
            "does not support image",
            "doesn't support image",
            "images are not supported",
            "unsupported image input",
            "image_url is not supported",
            "不支持图片输入",
        )
    )
