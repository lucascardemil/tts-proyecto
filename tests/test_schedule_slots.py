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
        "a": {"page_name": "CUENTOS", "videos": [{"scheduled_at": "2026-10-07T20:00:00", "status": "ready"},
                                                 {"scheduled_at": "2026-10-08T20:00:00", "status": "error"}]},
        "b": {"page_name": "HISTORIAS", "videos": [{"scheduled_at": "2026-10-07T12:00:00", "status": "ready"}]},
    })
    monkeypatch.setattr(bp.facebook_publisher, "list_post_times", lambda page_id=None, **k: [datetime(2026, 10, 9, 13)])
    slots = bp.existing_slots("CUENTOS", {"facebook": {"page_id": "1"}})
    assert sorted(slots) == [datetime(2026, 10, 7, 20), datetime(2026, 10, 9, 13)]


def test_existing_slots_survives_a_network_failure(monkeypatch):
    monkeypatch.setattr(bp, "_load", lambda: {})

    def boom(*a, **k):
        raise RuntimeError("sin red")

    monkeypatch.setattr(bp.facebook_publisher, "list_post_times", boom)
    assert bp.existing_slots("X", {"facebook": {"page_id": "1"}}) == []


def _et_hour(local_dt):
    from zoneinfo import ZoneInfo
    return local_dt.astimezone(ZoneInfo(bp.US_AUDIENCE_TZ)).hour


def test_us_audience_uses_two_eastern_time_slots_every_day():
    schedule = [datetime.fromisoformat(s) for s in bp._compute_schedule(6, 2, None, taken=[], us_audience=True)]
    assert len(schedule) == 6
    assert sorted({_et_hour(s) for s in schedule}) == [12, 19]
    assert all(bp.BATCH_HOUR_START <= s.hour <= bp.BATCH_HOUR_END for s in schedule)


def test_us_audience_adds_extra_eastern_slots_after_the_two_main_ones():
    first_day = [datetime.fromisoformat(s) for s in bp._compute_schedule(3, 3, None, taken=[], us_audience=True)]
    assert [_et_hour(s) for s in first_day][:2] == [12, 19]
    assert _et_hour(first_day[2]) in bp.US_SLOT_HOURS_ET


def test_us_hours_follow_each_countrys_daylight_saving():
    from datetime import date
    for day in (date(2026, 10, 20), date(2026, 11, 10), date(2027, 5, 10)):
        hours = bp.us_hours_for_date(day)
        assert hours and all(bp.BATCH_HOUR_START <= h <= bp.BATCH_HOUR_END for h in hours)
        local = datetime(day.year, day.month, day.day, hours[0])
        assert _et_hour(local) == 12


def test_default_schedule_without_us_audience_is_unchanged():
    schedule = bp._compute_schedule(3, 3, None, taken=[])
    assert sorted(datetime.fromisoformat(s).hour for s in schedule[:3]) == [9, 15, 20]


def test_every_fixed_profile_publishes_at_its_eastern_time_slots():
    from datetime import date
    profiles = {"gaming": bp.GAMING_SLOT_HOURS, "ninio": bp.NINIO_SLOT_HOURS,
                "historias": bp.HISTORIAS_SLOT_HOURS, "macrame": bp.MACRAME_SLOT_HOURS}
    for name, et_slots in profiles.items():
        schedule = [datetime.fromisoformat(s) for s in
                    bp._compute_schedule(4, 2, None, taken=[], us_audience=True, et_hours=et_slots)]
        assert {_et_hour(s) for s in schedule} == set(et_slots), name


def test_howbig_hours_are_eastern_time_converted_to_local_and_stay_inside_its_cap():
    from datetime import date
    launch = date(2026, 10, 20)
    for day in (launch, date(2026, 11, 10), date(2027, 5, 10)):
        hours = bp.howbig_hours_for_date(day, day)  # primer dia de la fase 1 en cada fecha
        assert len(hours) == 3 and all(bp.BATCH_HOUR_START <= h <= bp.HOWBIG_HOUR_END for h in hours)
        assert [_et_hour(datetime(day.year, day.month, day.day, h)) for h in hours] == bp.HOWBIG_PHASE1_HOURS
    later = bp.howbig_hours_for_date(launch + timedelta(days=30), launch)
    assert len(later) == 2
