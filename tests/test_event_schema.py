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


# --- §3.4 check 10: cycle detection over the combined explicit and
# reward-derived dependency edges. The derived edges are fully determined by
# this document alone — a consumed name maps to its producing challenge via
# check 5 — so the SDK builds both edge sets and rejects cycles here.


def test_explicit_dependency_cycle_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["depends_on"] = ["escalate"]
    manifest["challenges"][1]["depends_on"] = ["entry"]
    assert any("dependency cycle" in e for e in validate_event(manifest))


def test_reward_derived_cycle_fails() -> None:
    # No challenge declares depends_on, so the cycle exists only in the
    # reward wiring: entry consumes what escalate produces and vice versa.
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["rewards"]["consumes"] = [{"name": "cron_flag_token"}]
    assert validate_event(manifest) == ["challenges: dependency cycle entry -> escalate -> entry"]


def test_mixed_explicit_and_derived_cycle_fails() -> None:
    # entry --(reward)--> escalate --(depends_on)--> entry
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["depends_on"] = ["escalate"]
    assert validate_event(manifest) == ["challenges: dependency cycle entry -> escalate -> entry"]


def test_acyclic_diamond_passes() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][1]["depends_on"] = ["entry"]
    assert validate_event(manifest) == []


def test_self_dependency_is_not_also_reported_as_a_cycle() -> None:
    # Check 6 already names it; the graph is built from valid edges only, so
    # the author gets one error rather than two for the same mistake.
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["depends_on"] = ["entry"]
    assert validate_event(manifest) == ["challenges/entry: depends on itself"]


# --- §3.4 checks 1-4: resolved against the *referenced* minigame manifest
# through the resolver interface. Without a resolver the four checks are
# skipped — each caller supplies its own source (registry catalog, local
# artifact store, working directory).

SLIDE_PUZZLE = {
    "id": "slide-puzzle",
    "version": "1.2.0",
    # §8.1: solved by *finishing* it — nothing to paste. Also what makes
    # leaving its token slot unwired legal: the "wire the flag token" half of
    # §3.4 check 12 applies to `flag` games only.
    "solve_mode": "callback",
    "rewards": {
        "consumes": [],
        "produces": [
            {"name": "player_ssh_keypair", "type": "ssh_keypair"},
            {"name": "slide_puzzle_solution_flag", "type": "token"},
        ],
    },
}

WRITABLE_CRON_JOB = {
    "id": "writable-cron-job",
    "version": "1.0.0",
    "rewards": {
        "consumes": [{"name": "player_ssh_keypair", "type": "ssh_keypair"}],
        "produces": [{"name": "cron_flag_token", "type": "token"}],
    },
}


class DictResolver:
    """A resolver over an in-memory catalog, ignoring the version range."""

    def __init__(self, *manifests: dict) -> None:
        self._by_id = {m["id"]: m for m in manifests}

    def resolve(self, minigame_id: str, version_range: str) -> dict | None:
        return self._by_id.get(minigame_id)


def resolver(*manifests: dict) -> DictResolver:
    return DictResolver(*(manifests or (SLIDE_PUZZLE, WRITABLE_CRON_JOB)))


def test_resolved_event_passes() -> None:
    assert validate_event(load("event_valid.yaml"), resolver=resolver()) == []


def test_checks_1_to_4_are_skipped_without_a_resolver() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["minigame"]["id"] = "no-such-game"
    assert validate_event(manifest) == []


def test_unresolvable_minigame_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["minigame"]["id"] = "no-such-game"
    assert validate_event(manifest, resolver=resolver()) == [
        "challenges/entry: minigame 'no-such-game' '>=1.0,<2.0' does not resolve to a manifest"
    ]


def test_unsatisfied_version_range_fails() -> None:
    # The resolver owns version-range matching (the schema enforces only the
    # §2.1 range grammar); "no manifest satisfies this range" reaches the SDK
    # as an unresolved reference.
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["minigame"]["version"] = ">=9.0,<10.0"
    assert validate_event(manifest, resolver=resolver(WRITABLE_CRON_JOB)) == [
        "challenges/entry: minigame 'slide-puzzle' '>=9.0,<10.0' does not resolve to a manifest"
    ]


def test_consumed_name_not_a_slot_of_the_referenced_manifest_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["rewards"]["produces"] = [
        {"name": "slide_puzzle_solution_flag", "type": "token"}
    ]
    manifest["challenges"][1]["rewards"]["consumes"] = [{"name": "slide_puzzle_solution_flag"}]
    assert validate_event(manifest, resolver=resolver()) == [
        "challenges/escalate: consumed reward 'slide_puzzle_solution_flag' "
        "is not a consumes slot of minigame 'writable-cron-job'",
        "challenges/escalate: minigame 'writable-cron-job' consumes slot "
        "'player_ssh_keypair' is not wired",
    ]


def test_unwired_consumes_slot_fails() -> None:
    # Every consumes slot the referenced manifest declares must be wired (§3.2).
    manifest = load("event_valid.yaml")
    manifest["challenges"][1]["rewards"]["consumes"] = []
    assert validate_event(manifest, resolver=resolver()) == [
        "challenges/escalate: minigame 'writable-cron-job' consumes slot "
        "'player_ssh_keypair' is not wired"
    ]


def test_produced_name_not_a_slot_of_the_referenced_manifest_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][1]["rewards"]["produces"] = [{"name": "ghost_flag", "type": "token"}]
    assert any(
        "produced reward 'ghost_flag' is not a produces slot of minigame 'writable-cron-job'" in e
        for e in validate_event(manifest, resolver=resolver())
    )


def test_unwired_produces_slot_is_legal() -> None:
    # The converse of check 3 is deliberately not checked: slide-puzzle's
    # slide_puzzle_solution_flag slot is left unwired by the fixture (§3.2).
    assert validate_event(load("event_valid.yaml"), resolver=resolver()) == []


def test_produced_reward_type_mismatch_fails() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][1]["rewards"]["produces"] = [
        {"name": "cron_flag_token", "type": "password"}
    ]
    assert validate_event(manifest, resolver=resolver()) == [
        "challenges/escalate: produced reward 'cron_flag_token' has type 'password', "
        "minigame 'writable-cron-job' declares 'token'"
    ]


def test_yaml_norwegian_language_key_is_rejected() -> None:
    # YAML 1.1 resolves a bare `no` key to the boolean False, so Norwegian's
    # language code silently became a non-string key that `propertyNames`
    # patterns do not constrain. This must go through yaml.safe_load - a dict
    # literal cannot reproduce it.
    manifest = load("event_valid.yaml")
    manifest["name"] = yaml.safe_load("name: {no: Nei}")["name"]
    assert list(manifest["name"]) == [False]  # the bug this guards against
    assert validate_event(manifest) != []


def test_contract_range_grammar_is_enforced() -> None:
    # §2.1 fixed grammar (decided 2026-08-07): exactly one comparator pair
    # `>=A.B,<C.D` — lower bound, comma, upper bound, no whitespace, bounds
    # MAJOR.MINOR. The schema is what makes the contract's "enforced in CI"
    # claim true.
    manifest = load("event_valid.yaml")
    for bad in (">=0.1", ">= 0.1,<1.0", "^1.0", ">=0.1.0,<1.0.0", "*"):
        manifest["contract"] = bad
        assert validate_event(manifest) != [], bad


def test_minigame_version_range_uses_the_same_grammar() -> None:
    # §3.2: challenges[].minigame.version shares the §2.1 range grammar —
    # an exact version is not a range.
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["minigame"]["version"] = "1.0.0"
    assert validate_event(manifest) != []


def test_participation_mode_is_an_optional_enum() -> None:
    # §3.1: optional, default `teams`; `solo` models teams of one.
    manifest = load("event_valid.yaml")
    manifest["participation_mode"] = "solo"
    assert validate_event(manifest) == []
    manifest["participation_mode"] = "duo"
    assert validate_event(manifest) != []


# --- §3.2 / §3.4 check 14: `challenges[].minigame.host_label` ----------------
#
# The DNS label a game's HTTP tier is published under — an event-authoring
# field, deliberately not a manifest field. Optional, defaulting to the
# minigame id; check 14 runs over the *effective* labels (after defaulting):
# unique within the event, and none of the installation's reserved labels.
# The reserved list is caller-supplied installation configuration; `callback`
# (the callback ingress of §4.3) is the contract's floor and always refused.


def test_host_label_is_accepted() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["minigame"]["host_label"] = "ahab"
    assert validate_event(manifest) == []


def test_host_label_grammar_is_enforced() -> None:
    # Same slug grammar as an id (§3.2).
    manifest = load("event_valid.yaml")
    for bad in ("Ahab", "-ahab", "a", "ahab.cabin"):
        manifest["challenges"][0]["minigame"]["host_label"] = bad
        assert validate_event(manifest) != [], bad


def test_duplicate_effective_host_labels_fail() -> None:
    # An explicit label may not collide with another challenge's defaulted
    # minigame id either — uniqueness is checked after defaulting.
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["minigame"]["host_label"] = "writable-cron-job"
    assert validate_event(manifest) == [
        "challenges: duplicate host label 'writable-cron-job'"
    ]


def test_duplicate_minigame_id_is_not_also_a_duplicate_label() -> None:
    # Two challenges sharing a minigame id share its defaulted label too;
    # check 9 already names that mistake, so check 14 stays quiet about it.
    manifest = load("event_valid.yaml")
    manifest["challenges"][1]["minigame"]["id"] = "slide-puzzle"
    assert validate_event(manifest) == ["challenges: duplicate minigame id 'slide-puzzle'"]


def test_callback_host_label_is_always_reserved() -> None:
    # The callback ingress lives at callback.<event_domain> (§4.3); a game
    # shadowing it would swallow every solve report. Refused with no
    # installation list supplied at all.
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["minigame"]["host_label"] = "callback"
    assert validate_event(manifest) == [
        "challenges/entry: host label 'callback' is reserved by the installation"
    ]


def test_installation_reserved_host_labels_are_refused() -> None:
    manifest = load("event_valid.yaml")
    manifest["challenges"][0]["minigame"]["host_label"] = "play"
    assert validate_event(manifest, reserved_host_labels={"play"}) == [
        "challenges/entry: host label 'play' is reserved by the installation"
    ]


def test_reserved_list_catches_defaulted_labels_too() -> None:
    # The defaulted label (the minigame id) goes through the same check.
    manifest = load("event_valid.yaml")
    assert validate_event(manifest, reserved_host_labels={"writable-cron-job"}) == [
        "challenges/escalate: host label 'writable-cron-job' is reserved by the installation"
    ]


# --- §3.4 check 12, the event half: a `flag` game's token slot must be wired.


def test_flag_token_slot_must_be_wired() -> None:
    # writable-cron-job declares no solve_mode, so the default `flag` applies:
    # its one token produces slot is the flag the game shows, and an event
    # that leaves it unwired ships a game whose solve nobody can submit.
    manifest = load("event_valid.yaml")
    manifest["challenges"][1]["rewards"]["produces"] = []
    assert validate_event(manifest, resolver=resolver()) == [
        "challenges/escalate: minigame 'writable-cron-job' declares solve_mode "
        "'flag' but its token slot 'cron_flag_token' is not wired"
    ]


def test_callback_game_token_slot_may_stay_unwired() -> None:
    # slide-puzzle is a callback game (§8.1): its token slot exists for other
    # events to use, and leaving it unwired is the fixture's documented state.
    assert validate_event(load("event_valid.yaml"), resolver=resolver()) == []
