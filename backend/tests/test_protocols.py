"""T4 acceptance: loader rejects broken protocols; /api/protocols lists 3."""
import copy
import pytest

from app.protocol_loader import (
    ProtocolError, _validate_protocol, get_protocol, protocol_meta_list, get_deck,
)

ADMIN = {"username": "admin", "password": "changeme123"}


def test_deck_is_complete():
    deck = get_deck()
    for k in ["greet", "confirm_family", "wrong_person", "q_wound",
              "red_response", "closing", "timeout_reprompt"]:
        assert k in deck, f"missing deck clip {k}"


def test_protocols_load_and_count():
    metas = protocol_meta_list()
    ids = {m["id"] for m in metas}
    assert {"wound_care", "antibiotic_course", "fever_viral"}.issubset(ids)


def test_node_targets_and_clips_validated():
    # directly exercise the validator on a real protocol (should pass)
    p = get_protocol("wound_care")
    _validate_protocol(p)  # no raise


def test_validator_rejects_bad_next():
    p = copy.deepcopy(get_protocol("wound_care"))
    p["nodes"]["q_wound"]["options"]["1"]["next"] = "no_such_node"
    with pytest.raises(ProtocolError, match="unresolved"):
        _validate_protocol(p)


def test_validator_rejects_missing_clip():
    p = copy.deepcopy(get_protocol("wound_care"))
    p["nodes"]["greet"]["clip"] = "ghost_clip"
    with pytest.raises(ProtocolError, match="missing from deck"):
        _validate_protocol(p)


def test_api_protocols_requires_session(client):
    assert client.get("/api/protocols").status_code == 401


def test_api_protocols_lists(client):
    client.post("/api/auth/login", json=ADMIN)
    r = client.get("/api/protocols")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()}
    assert "wound_care" in ids