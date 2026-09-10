"""Non-secret desktop configuration decoding at the assembly boundary."""

from typing import Any

from .definitions import ConnectionDefinition, CustomModel


def decode_connections(value: Any) -> tuple[ConnectionDefinition, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or set(value) != {"revision", "connections"}:
        raise ValueError("Invalid model connection settings")
    if type(value["revision"]) is not int or value["revision"] < 0:
        raise ValueError("Invalid model connection revision")
    if not isinstance(value["connections"], list):
        raise ValueError("Invalid connection list")
    result = []
    for item in value["connections"]:
        if not isinstance(item, dict) or set(item) != {
            "id",
            "name",
            "baseUrl",
            "protocol",
            "models",
            "credentialRevision",
        }:
            raise ValueError("Invalid connection fields")
        if not all(
            isinstance(item[key], str)
            for key in ("id", "name", "baseUrl", "protocol", "credentialRevision")
        ):
            raise ValueError("Invalid connection values")
        if not isinstance(item["models"], list):
            raise ValueError("Invalid connection models")
        models = []
        for model in item["models"]:
            if (
                not isinstance(model, dict)
                or not {"id", "name", "contextWindow"}.issubset(model)
                or (
                    isinstance(model, dict)
                    and set(model) - {"id", "name", "contextWindow", "images", "documents"}
                )
            ):
                raise ValueError("Invalid model fields")
            if not isinstance(model["id"], str) or not isinstance(model["name"], str):
                raise ValueError("Invalid model values")
            models.append(
                CustomModel(
                    model["id"],
                    model["name"],
                    model["contextWindow"],
                    model.get("images", False),
                    model.get("documents", False),
                )
            )
        result.append(
            ConnectionDefinition(
                item["id"],
                item["name"],
                item["baseUrl"],
                item["protocol"],
                tuple(models),
                item["credentialRevision"],
            )
        )
    return tuple(result)
