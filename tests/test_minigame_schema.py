from pathlib import Path

import yaml

from gameframework_sdk.validation import validate_minigame

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return yaml.safe_load((FIXTURES / name).read_text())


def test_valid_manifest_passes() -> None:
    assert validate_minigame(load("minigame_valid.yaml")) == []


def test_missing_required_field_fails() -> None:
    # NOTE: the brief's Step 3 deletes "rewards" here, but the contract
    # document (sdk-contract-v1.md §2.1) marks `rewards` optional (default
    # {consumes: [], produces: []}), unlike the brief's schema draft which
    # required it. `resources` is required by both the brief and the
    # contract, so it is used here to keep testing "missing required field
    # fails" against a field that is actually required per the contract.
    manifest = load("minigame_valid.yaml")
    del manifest["resources"]
    errors = validate_minigame(manifest)
    assert any("resources" in e for e in errors)


def test_invalid_id_pattern_fails() -> None:
    manifest = load("minigame_valid.yaml")
    manifest["id"] = "Not_A_Valid_ID!"
    assert validate_minigame(manifest) != []


def test_name_must_be_language_map() -> None:
    manifest = load("minigame_valid.yaml")
    manifest["name"] = "plain string"
    assert validate_minigame(manifest) != []


def test_empty_language_map_fails() -> None:
    manifest = load("minigame_valid.yaml")
    manifest["description"] = {}
    assert validate_minigame(manifest) != []


def test_unknown_top_level_field_fails() -> None:
    manifest = load("minigame_valid.yaml")
    manifest["solution"] = "never ship solutions in manifests"
    assert validate_minigame(manifest) != []


def test_invalid_reward_type_fails() -> None:
    manifest = load("minigame_valid.yaml")
    manifest["rewards"]["produces"][0]["type"] = "flag"
    assert validate_minigame(manifest) != []
