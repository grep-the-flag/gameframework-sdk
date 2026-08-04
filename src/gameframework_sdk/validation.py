import json
from importlib.resources import files

from jsonschema import Draft202012Validator


def _schema(name: str) -> dict:
    return json.loads((files("gameframework_sdk") / "schemas" / name).read_text())


def _schema_errors(schema_file: str, manifest: dict) -> list[str]:
    validator = Draft202012Validator(_schema(schema_file))
    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path))
    ]


def validate_minigame(manifest: dict) -> list[str]:
    """Validate a parsed minigame.yaml. Returns [] when valid."""
    return _schema_errors("minigame.schema.json", manifest)
