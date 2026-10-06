from datetime import datetime, timedelta

import batch_pipeline as bp


def _tomorrow(hour):
    t = datetime.now() + timedelta(days=1)
    return datetime(t.year, t.month, t.day, hour)


def test_free_slot_skips_taken_hours_and_stays_inside_9_to_21():
    desired = _tomorrow(12)
    assert bp._free_slot(desired, []) == desired
    taken = [desired]
    slot = bp._free_slot(desired, taken)
    assert slot != desired and abs((slot - desired).total_seconds()) >= bp.BATCH_MIN_GAP_SECONDS
    assert 9 <= slot.hour <= 21 and slot.date() == desired.date()


def test_free_slot_moves_to_next_day_when_the_whole_day_is_taken():
    day = _tomorrow(9)
    taken = [day.replace(hour=h) for h in range(9, 22, 2)]  # cada 2 h: ninguna hora a >= 1 h libre... salvo las impares
    taken = [day.replace(hour=h) for h in range(9, 22)]     # todas las horas del rango
    slot = bp._free_slot(_tomorrow(12), taken)
    assert slot.date() == day.date() + timedelta(days=1) and slot.hour == 12


def test_free_slot_never_returns_the_past():
    past = datetime.now() - timedelta(days=1)
    assert bp._free_slot(past, []) >= datetime.now()


def test_schedule_respects_taken_slots_window_and_spreads_the_batch():
    taken = [_tomorrow(13), _tomorrow(20)]  # lo ya programado en la pagina
    schedule = bp._compute_schedule(6, 2, None, fixed_hours=[13, 20], taken=taken)
    slots = [datetime.fromisoformat(s) for s in schedule]
    assert len(set(slots)) == 6
    assert all(9 <= s.hour <= 21 for s in slots)
    existing = taken[:2]
    assert all(abs((s - t).total_seconds()) >= bp.BATCH_MIN_GAP_SECONDS for s in slots for t in existing)
    ordered = sorted(slots)
    assert all((b - a).total_seconds() >= bp.BATCH_MIN_GAP_SECONDS for a, b in zip(ordered, ordered[1:]))


def test_window_now_closes_at_9pm_sharp():
    assert bp.BATCH_HOUR_END == 21
    assert bp._into_publish_window(datetime(2026, 10, 6, 21, 0)) == datetime(2026, 10, 6, 21, 0)
    assert bp._into_publish_window(datetime(2026, 10, 6, 21, 30)) == datetime(2026, 10, 7, 9, 0)


def test_existing_slots_collects_other_batches_of_the_same_page(monkeypatch):
    monkeypatch.setattr(bp, "_load", lambda: {
        "a": {"page_name": "Bebé Héroe", "videos": [{"scheduled_at": "2026-10-07T20:00:00", "status": "ready"},
                                                    {"scheduled_at": "2026-10-08T20:00:00", "status": "error"}]},
        "b": {"page_name": "HISTORIAS", "videos": [{"scheduled_at": "2026-10-07T12:00:00", "status": "ready"}]},
    })
    monkeypatch.setattr(bp.facebook_publisher, "list_post_times", lambda page_id=None, **k: [datetime(2026, 10, 9, 13)])
    slots = bp.existing_slots("Bebé Héroe", {"facebook": {"page_id": "1"}})
    assert sorted(slots) == [datetime(2026, 10, 7, 20), datetime(2026, 10, 9, 13)]


def test_existing_slots_survives_a_network_failure(monkeypatch):
    monkeypatch.setattr(bp, "_load", lambda: {})

    def boom(*a, **k):
        raise RuntimeError("sin red")

    monkeypatch.setattr(bp.facebook_publisher, "list_post_times", boom)
    assert bp.existing_slots("X", {"facebook": {"page_id": "1"}}) == []
