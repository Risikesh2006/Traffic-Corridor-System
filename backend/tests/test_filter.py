import random

import pytest

from tracking.filter import AlphaBetaFilter, EwmaSpeed


def test_converges_on_constant_speed_without_noise():
    f = AlphaBetaFilter(alpha=0.5, beta=0.1)
    speed = 10.0
    f.initialize(0.0, 0.0, 0)  # starts wrong: speed unknown
    t = 0
    for _ in range(200):
        t += 1000
        f.update(speed * t / 1000, t)
    assert f.state.v_mps == pytest.approx(speed, rel=0.02)
    assert f.state.s_m == pytest.approx(speed * t / 1000, rel=0.02)


def test_smooths_noise_better_than_raw_measurements():
    rng = random.Random(4417)
    f = AlphaBetaFilter(alpha=0.3, beta=0.05)
    speed = 12.0
    f.initialize(0.0, speed, 0)
    raw_err = []
    filt_err = []
    t = 0
    for _ in range(300):
        t += 1000
        true_s = speed * t / 1000
        noisy = true_s + rng.gauss(0, 5.0)
        state = f.update(noisy, t)
        raw_err.append(abs(noisy - true_s))
        filt_err.append(abs(state.s_m - true_s))
    # Compare after the filter has settled.
    assert sum(filt_err[50:]) / 250 < sum(raw_err[50:]) / 250


def test_handles_stop_speed_goes_to_zero():
    f = AlphaBetaFilter(alpha=0.5, beta=0.2)
    f.initialize(0.0, 10.0, 0)
    t = 0
    for _ in range(50):
        t += 1000
        f.update(f.state.s_m, t)  # vehicle stays put
    assert f.state.v_mps < 1.0


def test_speed_never_negative():
    f = AlphaBetaFilter(alpha=0.9, beta=0.9)
    f.initialize(100.0, 5.0, 0)
    f.update(0.0, 1000)  # huge backwards measurement
    assert f.state.v_mps >= 0.0


def test_predict_advances_at_current_speed():
    f = AlphaBetaFilter(alpha=0.5, beta=0.2)
    f.initialize(100.0, 10.0, 0)
    assert f.predict(2000) == pytest.approx(120.0)


def test_coast_keeps_speed_and_moves_position():
    f = AlphaBetaFilter(alpha=0.5, beta=0.2)
    f.initialize(0.0, 8.0, 0)
    state = f.coast(5000)
    assert state.s_m == pytest.approx(40.0)
    assert state.v_mps == pytest.approx(8.0)


def test_rejects_invalid_gains():
    with pytest.raises(ValueError):
        AlphaBetaFilter(alpha=0.0, beta=0.5)
    with pytest.raises(ValueError):
        AlphaBetaFilter(alpha=0.5, beta=1.0)


def test_ewma_speed_tracks_observations():
    ewma = EwmaSpeed(alpha=0.5)
    assert ewma.observe(10.0) == 10.0
    assert ewma.observe(20.0) == pytest.approx(15.0)
    assert ewma.observe(20.0) == pytest.approx(17.5)
