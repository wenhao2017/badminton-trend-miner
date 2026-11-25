from types import SimpleNamespace

import numpy as np

from app.rules.signal_levels import determine_match_signal_level
from app.services.official_s import advance_confirmation, can_publish
from scripts.bwf_offline_replay import BASELINE, Poll, poll_states, publishable


def test_reference_replay_matches_formal_s_state_machine():
    rallies = np.arange(12, 24)
    # Include confirmation, a dip to A, an S rebound, an opponent A reset,
    # and a later run blocked at first by the separate trend gate.
    probabilities = np.array([[.90, .08], [.90, .08], [.90, .08], [.76, .24],
                              [.90, .08], [.85, .70], [.90, .08], [.85, .15],
                              [.85, .15], [.90, .08], [.90, .08], [.90, .08]])
    recent = np.broadcast_to([0.0, 1.0], probabilities.shape)
    trend = .8 * probabilities + .2 * recent
    expected = []
    state = None
    for index, p, t in zip(rallies, probabilities, trend):
        rows = {side: {"level": determine_match_signal_level(p[side]), "trend": t[side]}
                for side in (0, 1)}
        event = SimpleNamespace(point_index=int(index), raw_event_json={"type": "rally", "time": None})
        state = advance_confirmation(state, event, rows)
        expected.append([can_publish(state, side) for side in (0, 1)])
    assert np.array_equal(publishable(rallies, probabilities, recent, BASELINE), expected)
    assert [int(rallies[i]) for i, row in enumerate(expected) if row[0]] == [14, 16, 21, 22, 23]


def test_poll_replay_skips_intermediate_and_terminal_states():
    points = np.asarray([12, 20, 21, 145, 150])
    assert np.array_equal(poll_states(points, Poll(30, 5, 10, 35)),
                          np.asarray([np.nan, np.nan, 35, np.nan]), equal_nan=True)
