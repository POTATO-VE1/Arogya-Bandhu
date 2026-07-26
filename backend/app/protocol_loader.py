import json
from functools import lru_cache
from pathlib import Path

BASE = Path(__file__).parent
PROTOCOLS_DIR = BASE / "protocols"
DECK_FILE = BASE / "audio" / "scripts_kn.json"

TERMINALS = {"@end_ok", "@end_red", "@end_noanswer"}


class ProtocolError(Exception):
    pass


@lru_cache(maxsize=1)
def get_deck() -> dict[str, dict]:
    return json.loads(DECK_FILE.read_text(encoding="utf-8"))


def _validate_protocol(proto: dict) -> None:
    pid = proto["id"]
    nodes = proto["nodes"]
    deck = get_deck()

    if proto["start_node"] not in nodes:
        raise ProtocolError(f"{pid}: start_node '{proto['start_node']}' not a node")

    for node_id, node in nodes.items():
        if node["type"] not in ("play", "question"):
            raise ProtocolError(f"{pid}.{node_id}: bad type {node['type']!r}")
        if node["clip"] not in deck:
            raise ProtocolError(f"{pid}.{node_id}: clip '{node['clip']}' missing from deck")

        if node["type"] == "question":
            for digit, opt in node["options"].items():
                if not digit.isdigit() or not 1 <= int(digit) <= 9:
                    raise ProtocolError(f"{pid}.{node_id}: bad option digit {digit!r}")
                nxt = opt["next"]
                if nxt not in nodes and nxt not in TERMINALS:
                    raise ProtocolError(f"{pid}.{node_id}.{digit}: next '{nxt}' unresolved")
                if "clip" in opt and opt["clip"] not in deck:
                    raise ProtocolError(f"{pid}.{node_id}.{digit}: clip '{opt['clip']}' missing from deck")
        else:
            nxt = node["next"]
            if nxt not in nodes and nxt not in TERMINALS:
                raise ProtocolError(f"{pid}.{node_id}: next '{nxt}' unresolved")

    if "sheet" not in proto or "bullets_kn" not in proto["sheet"]:
        raise ProtocolError(f"{pid}: missing sheet.bullets_kn")


def _load_raw() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted(PROTOCOLS_DIR.glob("*.json")):
        proto = json.loads(f.read_text(encoding="utf-8"))
        if proto["id"] != f.stem:
            raise ProtocolError(f"{f.name}: id '{proto['id']}' != filename")
        _validate_protocol(proto)
        if proto["id"] in out:
            raise ProtocolError(f"{f.name}: duplicate protocol id")
        out[proto["id"]] = proto
    return out


@lru_cache(maxsize=1)
def get_protocols() -> dict[str, dict]:
    return _load_raw()


def get_protocol(pid: str) -> dict:
    try:
        return get_protocols()[pid]
    except KeyError:
        raise ProtocolError(f"unknown protocol: {pid}")


def protocol_meta_list() -> list[dict]:
    return [
        {
            "id": p["id"],
            "name_en": p["name_en"],
            "name_kn": p["name_kn"],
            "condition": p["condition"],
            "schedule_days": p["schedule_days"],
        }
        for p in get_protocols().values()
    ]