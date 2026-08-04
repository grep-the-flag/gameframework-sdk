from pathlib import Path

import yaml

from gameframework_sdk.validation import validate_event

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return yaml.safe_load((FIXTURES / name).read_text())


def test_valid_event_passes() -> None:
    assert validate_event(load("event_valid.yaml")) == []


def test_duplicate_challenge_id_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][1]["id"] = "entry"
    assert any("duplicate challenge id" in e for e in validate_event(manifest))


def test_missing_story_fails() -> None:
    manifest = load("event_valid.yaml")
    del manifest["story"]
    errors = validate_event(manifest)
    assert any("story" in e for e in errors)


def test_bad_scoring_mode_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["scoring"] = "hardcore"
    assert validate_event(manifest) != []


def test_duplicate_order_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][1]["order"] = 1
    assert any("duplicate order" in e for e in validate_event(manifest))


def test_unknown_dependency_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][1]["depends_on"] = ["ghost"]
    assert any("unknown challenge" in e for e in validate_event(manifest))


def test_self_dependency_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["depends_on"] = ["entry"]
    assert any("depends on itself" in e for e in validate_event(manifest))


def test_consuming_unproduced_reward_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][1]["rewards"]["consumes"] = [{"name": "ghost_key"}]
    assert any("not produced by any challenge" in e for e in validate_event(manifest))


def test_duplicate_produced_reward_name_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][1]["rewards"]["produces"] = [
        {"name": "player_ssh_keypair", "type": "ssh_keypair"}
    ]
    assert any("produced more than once" in e for e in validate_event(manifest))


# --- §3.4 check 9: a minigame.id may appear in at most one challenge. ---
# Missing from the brief's test list; added per the project owner's explicit
# reading of §3.4 check 9 (see task-10-report.md).
def test_reused_minigame_id_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][1]["minigame"]["id"] = manifest["challenges"][0]["minigame"]["id"]
    assert any("duplicate minigame id" in e for e in validate_event(manifest))


# --- §3.4 check 5 (full reading): no challenge may consume what it itself
# produces. The brief's snippet only checked "not produced by any challenge"
# (zero producers); this covers the self-consumption case (see
# task-10-report.md).
def test_self_consumption_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["rewards"]["consumes"] = [{"name": "player_ssh_keypair"}]
    assert any("produced by itself" in e for e in validate_event(manifest))
