"""Characterization tests for the shape classifier.

Every expected value here was verified against the live classifier before being
written down. These lock current behaviour so a refactor cannot quietly change
what 'cascade' means.

No test in this file touches Slack, Cognee, Qdrant, or the network.
"""

from conftest import ANCHOR, rx
from shapes import MIN_REACTIONS, burstiness, classify


def test_refuses_below_minimum_reactions():
    """The single most important behaviour: too little evidence -> no answer.

    Three timed reactions cannot support a shape, so classify returns None
    rather than guessing. A tool that invents a shape from n=3 is worse than
    one that stays quiet.
    """
    assert MIN_REACTIONS == 4
    assert classify(rx([0, 600, 1200]), ANCHOR) is None


def test_uniform_arrivals_classify_as_trickle():
    """Evenly spaced arrivals are indistinguishable from independent decisions.
    KS statistic is 0 here, well under the 1.36/sqrt(n) critical value."""
    c = classify(rx([0, 600, 1200, 1800, 2400, 3000]), ANCHOR)
    assert c["shape"] == "trickle"


def test_front_loaded_arrivals_classify_as_cascade():
    """Five reactions in five seconds, then a straggler two hours later.
    mean_u sits far below 0.25, which is the cascade signature."""
    c = classify(rx([0, 1, 2, 3, 4, 7200]), ANCHOR)
    assert c["shape"] == "cascade"


def test_simultaneous_arrivals_classify_as_cascade():
    """Zero span is the degenerate cascade: everyone at the same instant."""
    c = classify(rx([0, 0, 0, 0]), ANCHOR)
    assert c["shape"] == "cascade"


def test_late_cluster_classifies_as_stall_burst():
    """One early reaction, a long silence, then everyone at once.
    mean_u = 0.79, above the 0.75 stall-burst threshold."""
    c = classify(rx([0, 7000, 7100, 7150, 7199]), ANCHOR)
    assert c["shape"] == "stall-burst"
    assert c["mean_u"] > 0.75


def test_opposed_emoji_classify_as_split_regardless_of_timing():
    """Disagreement is a content property, orthogonal to arrival timing.
    Four +1 against two -1: the minority is 33%, over the 20% threshold."""
    c = classify(rx([0, 600, 1200, 1800]) + rx([300, 900], emoji="-1"), ANCHOR)
    assert c["shape"] == "split"
    assert c["split"] == {"for": 4, "against": 2}


def test_split_preserves_the_underlying_timing_shape():
    """`split` overrides `shape`, but the timing read must remain inspectable."""
    c = classify(rx([0, 600, 1200, 1800]) + rx([300, 900], emoji="-1"), ANCHOR)
    assert c["timing_shape"] in {"cascade", "trickle", "stall-burst", "mixed"}
    assert c["shape"] != c["timing_shape"]


def test_burstiness_of_perfectly_regular_gaps_is_minus_one():
    """Zero variance -> sd is 0 -> B = -1. The published lower bound."""
    assert burstiness([10, 10, 10, 10]) == -1.0


def test_burstiness_rises_with_irregularity():
    """A long gap among short ones is more bursty than uniform spacing."""
    assert burstiness([1, 1, 1, 100]) > burstiness([10, 10, 10, 10])


def test_burstiness_is_bounded():
    """B must stay inside [-1, 1] for any gap sequence."""
    for gaps in ([1, 1000000], [5, 5, 5], [1, 2, 3, 4, 5]):
        assert -1.0 <= burstiness(gaps) <= 1.0


def test_burstiness_needs_two_gaps():
    """Fewer than two gaps carries no spacing information; returns 0.0."""
    assert burstiness([42]) == 0.0
    assert burstiness([]) == 0.0
