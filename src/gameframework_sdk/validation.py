import json
from collections.abc import Collection
from importlib.resources import files
from typing import Protocol

from jsonschema import Draft202012Validator

# §3.4 check 14: the floor of the installation's reserved host labels, fixed
# by the contract itself — the callback ingress lives at
# `callback.<event_domain>` (§4.3), so a minigame publishing under that label
# would shadow the solve channel on any installation. The rest of the list is
# installation configuration and reaches `validate_event` through its caller,
# the same way referenced manifests reach it through the resolver.
RESERVED_HOST_LABELS = frozenset({"callback"})


class MinigameResolver(Protocol):
    """Supplies the minigame manifests referenced by an `event.yaml`.

    §3.4 makes the SDK the single canonical validation pipeline, including
    the checks that compare an event against a *referenced* manifest. Those
    checks need a second input, and where it comes from differs per call
    site — the registry catalog in registry CI, the local artifact store in
    the core's import/dry-run, the working directory in an author's editor.
    Each caller implements this interface against its own source; the
    algorithm above it is the same one everywhere.
    """

    def resolve(self, minigame_id: str, version_range: str) -> dict | None:
        """Return the manifest for `minigame_id` satisfying `version_range`.

        Returns `None` when the source holds no such manifest — either the
        id is unknown or no version of it satisfies the range. **The
        resolver owns version-range matching**: the schemas enforce the
        fixed range grammar `>=A.B,<C.D` (§2.1, decided 2026-08-07), but
        whether any existing version satisfies the range is a question only
        the source that knows which versions exist can answer.
        """


def _schema(name: str) -> dict:
    return json.loads((files("gameframework_sdk") / "schemas" / name).read_text())


def _schema_errors(schema_file: str, manifest: dict) -> list[str]:
    validator = Draft202012Validator(_schema(schema_file))
    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path))
    ]


def _duplicates(items) -> list:
    seen: set = set()
    dupes: set = set()
    for item in items:
        (dupes if item in seen else seen).add(item)
    return sorted(dupes, key=str)


def _slot_errors(challenge: dict, minigame: dict) -> list[str]:
    """§3.4 checks 2-4, plus the event half of check 12.

    The manifest owns the reward names in both directions (§2.1), so this
    reads as "does the event speak the minigame's vocabulary": every wired
    name must be a slot the manifest declares, every `consumes` slot must be
    wired (§3.2), and a wired `produces` type must equal the declared one.
    The converse of check 3 is deliberately absent — a `produces` slot the
    event leaves unwired is legal and simply unused — with one exception:
    a `flag` game's token slot (check 12). Under `solve_mode: flag` (the
    default) that slot is the flag the game shows, and the framework, not
    another challenge, is what consumes it — so check 5 never notices it
    missing, and an event that leaves it unwired ships a game whose solve
    nobody can submit.
    """
    cid, mid = challenge["id"], minigame["id"]
    wiring = challenge.get("rewards", {})
    slots = minigame.get("rewards", {})
    errors = []

    declared = {r["name"] for r in slots.get("consumes", [])}
    wired = {r["name"] for r in wiring.get("consumes", [])}
    errors += [
        f"challenges/{cid}: consumed reward '{n}' is not a consumes slot of minigame '{mid}'"
        for n in sorted(wired - declared)
    ]
    errors += [
        f"challenges/{cid}: minigame '{mid}' consumes slot '{n}' is not wired"
        for n in sorted(declared - wired)
    ]

    produces = {r["name"]: r["type"] for r in slots.get("produces", [])}
    for r in wiring.get("produces", []):
        if r["name"] not in produces:
            errors.append(
                f"challenges/{cid}: produced reward '{r['name']}' "
                f"is not a produces slot of minigame '{mid}'"
            )
        elif r["type"] != produces[r["name"]]:
            errors.append(
                f"challenges/{cid}: produced reward '{r['name']}' has type '{r['type']}', "
                f"minigame '{mid}' declares '{produces[r['name']]}'"
            )

    if minigame.get("solve_mode", "flag") == "flag":
        token_slot = next((n for n, t in produces.items() if t == "token"), None)
        wired_produces = {r["name"] for r in wiring.get("produces", [])}
        if token_slot is not None and token_slot not in wired_produces:
            errors.append(
                f"challenges/{cid}: minigame '{mid}' declares solve_mode 'flag' "
                f"but its token slot '{token_slot}' is not wired"
            )
    return errors


def _dependency_cycle(challenges: list[dict], produced: dict[str, str]) -> str | None:
    """§3.4 check 10: the first cycle in the combined dependency graph.

    Edges point predecessor → dependent, from both sources: an explicit
    `depends_on` entry, and a consumed reward name resolved through
    `produced` to the challenge that produces it. Only edges that are
    themselves valid are added — an unknown or self-referential dependency
    is already reported by checks 5-6, and feeding it in here would report
    the same mistake twice under a second name.
    """
    ids = {c["id"] for c in challenges}
    adjacency: dict[str, list[str]] = {c["id"]: [] for c in challenges}
    for c in challenges:
        cid = c["id"]
        sources = [d for d in c.get("depends_on", []) if d in ids and d != cid]
        sources += [
            produced[r["name"]]
            for r in c.get("rewards", {}).get("consumes", [])
            if produced.get(r["name"], cid) != cid
        ]
        for source in sources:
            if cid not in adjacency[source]:
                adjacency[source].append(cid)

    visited: dict[str, bool] = {}  # id -> still on the current path
    path: list[str] = []

    def walk(node: str) -> list[str] | None:
        visited[node] = True
        path.append(node)
        for nxt in adjacency[node]:
            if visited.get(nxt):
                return path[path.index(nxt) :] + [nxt]
            if nxt not in visited:
                found = walk(nxt)
                if found:
                    return found
        path.pop()
        visited[node] = False
        return None

    for c in challenges:
        if c["id"] not in visited:
            found = walk(c["id"])
            if found:
                return " -> ".join(found)
    return None


def validate_minigame(manifest: dict) -> list[str]:
    """Validate a parsed minigame.yaml: JSON Schema + intra-document checks.

    The §2.1 rules layered on top of the schema pass are the ones JSON
    Schema cannot express: they compare sibling values against each other
    rather than constraining a single instance location.

    - `http.port` must differ from every `tcp_ports[].port`, and
      `tcp_ports[].port` is unique within `tcp_ports` — "one port serves
      exactly one tier".
    - `rewards.produces[].name` and `rewards.consumes[].name` are each
      unique within their own array. §6 makes the reward name the join key
      between an event and this manifest, so one name bound to two types
      would make §3.4's reward-type check nondeterministic.

    §3.4's checks 11-12 are here for the same reason — they read two fields
    of this one document against each other:

    - check 11: `solve_mode: callback` is rejected beside any `tcp_ports`.
      A team shares one injected credential by design, so a callback from
      an SSH box cannot say which person earned it.
    - check 12: `solve_mode: flag` (the default) requires at least one
      `rewards.produces[]` slot of type `token` — the flag the game shows
      for the player to submit. Nothing else in the pipeline covers it: the
      flag is consumed by the *framework*, not by another challenge.
    """
    errors = _schema_errors("minigame.schema.json", manifest)
    if errors:
        return errors

    tcp_ports = [p["port"] for p in manifest.get("tcp_ports", [])]
    errors += [f"tcp_ports: duplicate port {p}" for p in _duplicates(tcp_ports)]
    http_port = manifest["http"]["port"]
    if http_port in tcp_ports:
        errors.append(f"http/port: port {http_port} is also declared in tcp_ports")

    rewards = manifest.get("rewards", {})
    for slot in ("consumes", "produces"):
        names = [r["name"] for r in rewards.get(slot, [])]
        errors += [f"rewards/{slot}: duplicate reward name '{n}'" for n in _duplicates(names)]

    solve_mode = manifest.get("solve_mode", "flag")
    if tcp_ports and solve_mode == "callback":
        errors.append("solve_mode: 'callback' is not allowed for a minigame declaring tcp_ports")
    if solve_mode == "flag" and not any(r["type"] == "token" for r in rewards.get("produces", [])):
        errors.append(
            "rewards/produces: solve_mode 'flag' requires at least one "
            "produces slot of type 'token'"
        )
    return errors


def validate_event(
    manifest: dict,
    resolver: MinigameResolver | None = None,
    reserved_host_labels: Collection[str] = (),
) -> list[str]:
    """Validate a parsed event.yaml: JSON Schema + referential checks.

    Cycle detection over the combined explicit and reward-derived
    dependency edges (sdk-contract-v1.md §3.4 check 10) happens here. Both
    edge sets are fully determined by this one document: `depends_on` gives
    the explicit edges, and a consumed reward name maps to its producing
    challenge (check 5) to give the derived ones. Only the first cycle
    found is reported — a cycle makes the rest of the graph's ordering
    meaningless, so listing more of them helps nobody.

    Referential checks 1-4 (resolving `challenges[].minigame.id`/`.version`
    and reward slot names/types against the *referenced minigame manifest*)
    need a second document, which arrives through `resolver` — see
    `MinigameResolver`. Without one they are skipped and every other check
    still runs, because an author editing an `event.yaml` with no catalog
    at hand should still get the rest of the pipeline.

    Check 14 (host labels) runs over the *effective* labels — an explicit
    `host_label` or the `minigame.id` default (§3.2): unique within the
    event, and none of the installation's reserved labels. The reserved
    list is installation configuration handed in by the caller like the
    resolver is; `RESERVED_HOST_LABELS` is the contract's floor and is
    refused with or without a caller-supplied list. Two challenges sharing
    a minigame id share its defaulted label too — check 9 already names
    that mistake, so it is not reported again as a duplicate label.
    """
    errors = _schema_errors("event.schema.json", manifest)
    if errors:
        return errors

    challenges = manifest["challenges"]
    ids = [c["id"] for c in challenges]
    errors += [f"challenges: duplicate challenge id '{i}'" for i in _duplicates(ids)]
    errors += [
        f"challenges: duplicate order {o}" for o in _duplicates(c["order"] for c in challenges)
    ]
    duplicate_minigame_ids = _duplicates(c["minigame"]["id"] for c in challenges)
    errors += [f"challenges: duplicate minigame id '{m}'" for m in duplicate_minigame_ids]

    labels = {c["id"]: c["minigame"].get("host_label", c["minigame"]["id"]) for c in challenges}
    errors += [
        f"challenges: duplicate host label '{label}'"
        for label in _duplicates(labels.values())
        if label not in duplicate_minigame_ids
    ]
    reserved = RESERVED_HOST_LABELS.union(reserved_host_labels)
    errors += [
        f"challenges/{cid}: host label '{label}' is reserved by the installation"
        for cid, label in labels.items()
        if label in reserved
    ]

    produced: dict[str, str] = {}
    for c in challenges:
        for dep in c.get("depends_on", []):
            if dep == c["id"]:
                errors.append(f"challenges/{c['id']}: depends on itself")
            elif dep not in ids:
                errors.append(f"challenges/{c['id']}: depends_on unknown challenge '{dep}'")
        for r in c.get("rewards", {}).get("produces", []):
            if r["name"] in produced:
                errors.append(f"challenges/{c['id']}: reward '{r['name']}' produced more than once")
            produced[r["name"]] = c["id"]

    for c in challenges:
        for r in c.get("rewards", {}).get("consumes", []):
            name = r["name"]
            if name not in produced:
                errors.append(
                    f"challenges/{c['id']}: consumed reward '{name}' not produced by any challenge"
                )
            elif produced[name] == c["id"]:
                errors.append(f"challenges/{c['id']}: consumed reward '{name}' produced by itself")

    if resolver is not None:
        for c in challenges:
            ref = c["minigame"]
            referenced = resolver.resolve(ref["id"], ref["version"])
            if referenced is None:
                errors.append(
                    f"challenges/{c['id']}: minigame '{ref['id']}' '{ref['version']}' "
                    "does not resolve to a manifest"
                )
            else:
                errors += _slot_errors(c, referenced)

    cycle = _dependency_cycle(challenges, produced)
    if cycle:
        errors.append(f"challenges: dependency cycle {cycle}")
    return errors
