from core.sim_clock import SeededRandom, SimClock


def test_clock_advances_deterministically():
    clock = SimClock(tick_hz=10)
    assert clock.now_s == 0.0
    clock.advance(10)
    assert clock.now_s == 1.0
    clock.advance(5)
    assert clock.now_s == 1.5


def test_clock_reset():
    clock = SimClock(tick_hz=10)
    clock.advance(20)
    clock.reset()
    assert clock.now_s == 0.0


def test_seeded_random_reproducible():
    r1 = SeededRandom(4417)
    r2 = SeededRandom(4417)
    s1 = r1.stream("traffic")
    s2 = r2.stream("traffic")
    assert [s1.random() for _ in range(5)] == [s2.random() for _ in range(5)]


def test_seeded_random_streams_independent():
    r = SeededRandom(4417)
    traffic = r.stream("traffic").random()
    sensors = r.stream("sensors").random()
    assert traffic != sensors
