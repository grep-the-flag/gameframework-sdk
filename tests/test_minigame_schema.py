import json
from pathlib import Path

import pytest
import yaml

import gameframework_sdk.validation as validation
from gameframework_sdk.validation import validate_minigame

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return yaml.safe_load((FIXTURES / name).read_text())


def _minigame_schema() -> dict:
    schemas = Path(validation.__file__).parent / "schemas"
    return json.loads((schemas / "minigame.schema.json").read_text())


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


# --- `image`: OCI image reference (§2.1) ------------------------------------
#
# §1.2 makes the field tables normative and the examples illustrative. §2.1
# constrains `image` to an "OCI image reference" and explicitly declines to
# require a digest, noting digest pinning is planned for M6. The registry
# host, namespace depth, tag and digest are therefore all author's choice.

ACCEPTED_IMAGES = [
    "nginx",  # bare name, no registry
    "docker.io/library/nginx",  # registry + namespace + name
    "ghcr.io/grep-the-flag/minigame-writable-cron-job",  # the fixture value
    "quay.io/org/img",  # non-ghcr registry
    "harbor.example.com:5000/team/sub/img",  # registry with port + nested namespaces
    "ghcr.io/a/b/c/d/e",  # deeply nested namespaces
    "ghcr.io/org/my_img",  # underscore separator
    "registry.io/org/img__x.y-z",  # every OCI separator form
    "ghcr.io/org/img:1.0.0",  # tag
    "ghcr.io/org/img@sha256:" + "a" * 64,  # digest
    "ghcr.io/org/img:1.0.0@sha256:" + "a" * 64,  # tag + digest
    "localhost:5000/img",  # localhost registry
]

REJECTED_IMAGES = [
    "",  # empty string
    "   ",  # whitespace only
    "ghcr.io/org/img ",  # trailing whitespace
    "ghcr.io/Org/img",  # uppercase in a path component - forbidden by OCI
    "GHCR.IO/org/IMG",  # uppercase in the name
    "ghcr.io//img",  # empty path component
    "/org/img",  # leading slash
    "ghcr.io/org/-img",  # path component may not start with a separator
    "ghcr.io/org/img:",  # empty tag
    "ghcr.io/org/img:tag with space",  # whitespace in tag
    "ghcr.io/org/img@sha256:zz",  # malformed digest
    "ghcr.io/org/img@sha256:" + "a" * 63,  # digest of the wrong length
]


@pytest.mark.parametrize("image", ACCEPTED_IMAGES)
def test_valid_oci_image_reference_accepted(image: str) -> None:
    manifest = load("minigame_valid.yaml")
    manifest["image"] = image
    assert validate_minigame(manifest) == []


@pytest.mark.parametrize("image", REJECTED_IMAGES)
def test_invalid_oci_image_reference_rejected(image: str) -> None:
    manifest = load("minigame_valid.yaml")
    manifest["image"] = image
    assert any("image" in e for e in validate_minigame(manifest))


# --- §2.1 intra-document rules ----------------------------------------------


def test_http_port_colliding_with_tcp_port_fails() -> None:
    manifest = load("minigame_valid.yaml")
    manifest["http"]["port"] = manifest["tcp_ports"][0]["port"]
    assert validate_minigame(manifest) == ["http/port: port 22 is also declared in tcp_ports"]


def test_duplicate_tcp_port_fails() -> None:
    manifest = load("minigame_valid.yaml")
    manifest["tcp_ports"] = [
        {"port": 2222, "protocol": "tcp"},
        {"port": 2222, "protocol": "tcp"},
    ]
    assert validate_minigame(manifest) == ["tcp_ports: duplicate port 2222"]


def test_duplicate_produced_reward_name_fails() -> None:
    manifest = load("minigame_valid.yaml")
    manifest["rewards"]["produces"] = [
        {"name": "cron_flag_token", "type": "token"},
        {"name": "cron_flag_token", "type": "password"},
    ]
    assert validate_minigame(manifest) == [
        "rewards/produces: duplicate reward name 'cron_flag_token'"
    ]


def test_duplicate_consumed_reward_name_fails() -> None:
    manifest = load("minigame_valid.yaml")
    manifest["rewards"]["consumes"] = [
        {"name": "player_ssh_keypair", "type": "ssh_keypair"},
        {"name": "player_ssh_keypair", "type": "password"},
    ]
    assert validate_minigame(manifest) == [
        "rewards/consumes: duplicate reward name 'player_ssh_keypair'"
    ]


def test_manifest_without_optional_blocks_still_validates() -> None:
    # The intra-document checks must tolerate absent `tcp_ports`/`rewards`,
    # both optional per §2.1. `solve_mode: callback` is what makes dropping
    # `rewards` legal: under the default `flag` the manifest must declare the
    # token slot it shows (§3.4 check 12).
    manifest = load("minigame_valid.yaml")
    del manifest["tcp_ports"]
    del manifest["rewards"]
    manifest["solve_mode"] = "callback"
    assert validate_minigame(manifest) == []


# --- §2.1 `isolation_mode` / `provision_identity` / `solve_mode` -------------
#
# All three are optional with a default, and the manifest is closed
# (`additionalProperties: false`), so a manifest that states any of them is
# rejected outright until the schema knows the field.


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("isolation_mode", ["per_team", "shared"]),
        ("provision_identity", ["handles", "names"]),
        ("solve_mode", ["flag", "callback"]),
    ],
)
def test_declared_enum_values_are_accepted(field: str, values: list[str]) -> None:
    for value in values:
        manifest = load("minigame_valid.yaml")
        if field == "solve_mode":
            # `callback` is illegal beside tcp_ports (§3.4 check 11), which is
            # a separate rule from "the schema knows this field". Nothing
            # constrains `isolation_mode` that way any more: `shared` is the
            # default and is valid with or without a TCP tier.
            del manifest["tcp_ports"]
        manifest[field] = value
        assert validate_minigame(manifest) == []


@pytest.mark.parametrize("field", ["isolation_mode", "provision_identity", "solve_mode"])
def test_unknown_enum_value_is_rejected(field: str) -> None:
    manifest = load("minigame_valid.yaml")
    manifest[field] = "whatever"
    assert any(field in e for e in validate_minigame(manifest))


@pytest.mark.parametrize(
    ("field", "default"),
    [
        ("isolation_mode", "shared"),
        ("provision_identity", "handles"),
        ("solve_mode", "flag"),
    ],
)
def test_schema_declares_the_contract_default(field: str, default: str) -> None:
    # The default is normative in §2.1; the schema is where a consumer of the
    # SDK reads it, so it must be stated rather than only implied by omission.
    assert _minigame_schema()["properties"][field]["default"] == default


# --- §3.4 checks 11-12 ------------------------------------------------------


def test_shared_isolation_with_tcp_ports_passes() -> None:
    # ADR-0018: `shared` is the default and carries every game class, TCP tier
    # included. What keeps teams apart after a solve is the contract rule that
    # a minigame never grants players root — the escalation target is a
    # per-team service account — and no manifest field can attest to that, so
    # nothing here may reject the combination.
    manifest = load("minigame_valid.yaml")
    manifest["isolation_mode"] = "shared"
    assert validate_minigame(manifest) == []


def test_shared_isolation_without_tcp_ports_passes() -> None:
    manifest = load("minigame_valid.yaml")
    del manifest["tcp_ports"]
    manifest["isolation_mode"] = "shared"
    assert validate_minigame(manifest) == []


def test_callback_solve_mode_with_tcp_ports_fails() -> None:
    manifest = load("minigame_valid.yaml")
    manifest["solve_mode"] = "callback"
    assert validate_minigame(manifest) == [
        "solve_mode: 'callback' is not allowed for a minigame declaring tcp_ports"
    ]


def test_flag_solve_mode_without_a_token_produces_slot_fails() -> None:
    manifest = load("minigame_valid.yaml")
    manifest["solve_mode"] = "flag"
    manifest["rewards"]["produces"] = [{"name": "cron_flag_token", "type": "password"}]
    assert validate_minigame(manifest) == [
        "rewards/produces: solve_mode 'flag' requires at least one produces slot of type 'token'"
    ]


def test_default_solve_mode_without_a_token_produces_slot_fails() -> None:
    # `flag` is the default, so the rule bites on manifests that never mention
    # solve_mode at all.
    manifest = load("minigame_valid.yaml")
    del manifest["rewards"]["produces"]
    assert validate_minigame(manifest) == [
        "rewards/produces: solve_mode 'flag' requires at least one produces slot of type 'token'"
    ]


def test_callback_solve_mode_needs_no_token_produces_slot() -> None:
    manifest = load("minigame_valid.yaml")
    del manifest["tcp_ports"]
    manifest["solve_mode"] = "callback"
    manifest["rewards"]["produces"] = [{"name": "cron_flag_token", "type": "password"}]
    assert validate_minigame(manifest) == []


def test_yaml_norwegian_language_key_is_rejected() -> None:
    # YAML 1.1 resolves a bare `no` key to the boolean False, so Norwegian's
    # language code silently became a non-string key that `propertyNames`
    # patterns do not constrain. This must go through yaml.safe_load - a dict
    # literal cannot reproduce it.
    manifest = yaml.safe_load((FIXTURES / "minigame_valid.yaml").read_text())
    manifest["name"] = yaml.safe_load("name: {no: Nei}")["name"]
    assert list(manifest["name"]) == [False]  # the bug this guards against
    assert validate_minigame(manifest) != []
