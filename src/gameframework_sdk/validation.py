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


def _duplicates(items) -> list:
    seen: set = set()
    dupes: set = set()
    for item in items:
        (dupes if item in seen else seen).add(item)
    return sorted(dupes, key=str)


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
    return errors


def validate_event(manifest: dict) -> list[str]:
    """Validate a parsed event.yaml: JSON Schema + referential checks.

    Referential checks 1-4 of sdk-contract-v1.md §3.4 (resolving
    `challenges[].minigame.id`/`.version` and reward slot names/types
    against the *referenced minigame manifest*) are out of scope here: they
    compare this document against another one, and M1 has no minigame
    registry to resolve against. They require a second input this
    function's signature does not take.

    DAG cycle detection over the combined explicit and reward-derived
    dependency edges (§3.4 check 10) is intentionally not done here either
    — it lands with the core's import pipeline in M2, where both edge sets
    exist as rows; the SDK validators see one document at a time and cannot
    build the derived edges.
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
    errors += [
        f"challenges: duplicate minigame id '{m}'"
        for m in _duplicates(c["minigame"]["id"] for c in challenges)
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
    return errors
