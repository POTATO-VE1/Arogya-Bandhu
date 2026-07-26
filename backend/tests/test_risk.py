"""T6 acceptance: the 8 enumerated risk-engine cases (docs/03 §6.4)."""
from app.risk import evaluate


def out(score=0, reason=None, forced_red=False, node_id="n", digit="1"):
    return {"node_id": node_id, "digit": digit, "score": score,
            "reason": reason, "forced_red": forced_red}


def test_1_all_zeros_is_green():
    r = evaluate([out(0), out(0), out(0)])
    assert (r.level, r.score) == ("green", 0)


def test_2_single_yellow():
    r = evaluate([out(2, reason="wound: pain")])
    assert r.level == "yellow"
    assert r.reasons == ["wound: pain"]


def test_3_forced_red_overrides_low_score():
    r = evaluate([out(0, forced_red=True, reason="SSI red flag")])
    assert r.level == "red"
    assert r.score == 0


def test_4_scores_sum_to_red():
    r = evaluate([out(2), out(2), out(2)])
    assert r.level == "red" and r.score == 6


def test_5_unreachable_two_missed_promotes_to_yellow():
    r = evaluate([out(0)], missed_calls_before=2)
    assert r.level == "yellow"
    assert "family unreachable on 2 scheduled calls" in r.reasons


def test_6_reasons_preserve_order_and_content():
    r = evaluate([out(0, reason="a"), out(2, reason="b"), out(10, reason="c")])
    assert r.reasons == ["a", "b", "c"]


def test_7_boundaries_5_yellow_6_red():
    assert evaluate([out(5)]).level == "yellow"
    assert evaluate([out(6)]).level == "red"


def test_8_empty_outcomes_green():
    r = evaluate([])
    assert (r.level, r.score, r.reasons) == ("green", 0, [])