from core.rng import RngFactory


def test_same_seed_and_name_identical_sequence() -> None:
    f1 = RngFactory(4417)
    f2 = RngFactory(4417)
    seq1 = [f1.stream("traffic").random() for _ in range(10)]
    seq2 = [f2.stream("traffic").random() for _ in range(10)]
    assert seq1 == seq2


def test_different_names_different_sequences() -> None:
    f = RngFactory(4417)
    traffic = [f.stream("traffic").random() for _ in range(5)]
    sensors = [f.stream("sensors").random() for _ in range(5)]
    assert traffic != sensors


def test_different_seed_different_sequence() -> None:
    a = RngFactory(1).stream("traffic")
    b = RngFactory(2).stream("traffic")
    assert [a.random() for _ in range(5)] != [b.random() for _ in range(5)]


def test_new_stream_does_not_perturb_existing_stream() -> None:
    f1 = RngFactory(4417)
    before = [f1.stream("traffic").random() for _ in range(5)]

    f2 = RngFactory(4417)
    f2.stream("brand_new_consumer")  # draw from an extra stream first
    after = [f2.stream("traffic").random() for _ in range(5)]

    assert before == after


def test_stream_is_cached_not_recreated() -> None:
    f = RngFactory(4417)
    s1 = f.stream("traffic")
    s2 = f.stream("traffic")
    assert s1 is s2
