# -*- coding: utf-8 -*-
"""How Big Really: banco de temas y seleccion de plantilla/tema."""
import random
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import howbig


def test_bank_schema_and_distribution():
    topics = howbig.load_topics()
    assert len(topics) >= 100
    ids = [t["id"] for t in topics]
    assert len(ids) == len(set(ids))
    counts = Counter(t["template"] for t in topics)
    assert set(counts) == {"A", "B", "C"}
    assert counts["A"] >= 60 and counts["B"] >= 15 and counts["C"] >= 5
    for t in topics:
        assert set(t) == {"id", "template", "big", "reference", "fact", "source", "question"}, t["id"]
        for key in ("big", "reference", "fact", "question"):
            assert isinstance(t[key], str) and t[key].strip(), (t["id"], key)
        assert t["source"] is None or t["source"] in howbig.SOURCES, (t["id"], t["source"])
        assert 25 <= len(t["fact"]) <= 400, t["id"]
        assert t["question"].strip().endswith("?"), t["id"]
        assert "http" not in t["fact"].lower() and "#" not in t["fact"], t["id"]


def test_template_pick_is_weighted_and_never_repeats_the_last():
    rng = random.Random(7)
    picks, last = [], "A"
    for _ in range(3000):
        last = howbig.pick_template([last], rng)
        picks.append(last)
    assert all(a != b for a, b in zip(picks, picks[1:]))
    counts = Counter(picks)
    assert counts["A"] > counts["B"] > counts["C"] and counts["C"] > 0
    assert howbig.pick_template([], random.Random(1)) in howbig.TEMPLATE_WEIGHTS


def test_pick_topic_skips_used_and_falls_back_to_other_templates():
    topics = howbig.load_topics()
    a_ids = {t["id"] for t in topics if t["template"] == "A"}
    t = howbig.pick_topic("A", set(), random.Random(3))
    assert t["template"] == "A" and t["id"] not in set()
    t2 = howbig.pick_topic("A", a_ids, random.Random(3))  # todas las A usadas
    assert t2["template"] in ("B", "C") and t2["id"] not in a_ids


def test_bank_exhausted():
    all_ids = {t["id"] for t in howbig.load_topics()}
    with pytest.raises(howbig.BankExhaustedError, match="exhausted"):
        howbig.pick_topic("A", all_ids, random.Random(1))


def test_topic_block_has_the_fact_and_only_listed_sources():
    topic = {"id": "x1", "template": "A", "big": "blue whale heart", "reference": "golf cart",
             "fact": "A blue whale's heart is roughly the size of a golf cart.", "source": "NOAA",
             "question": "What animal should we scale next?"}
    block = howbig.topic_block(topic)
    assert "blue whale heart" in block and "golf cart" in block and "Source: NOAA" in block
    no_source = howbig.topic_block({**topic, "source": None})
    assert "Source:" not in no_source and "no source" in no_source.lower()


# ── Perfil howbig_image: prompt, plantilla y generacion del post ───────────────

import batch_pipeline as bp
import meme_maker
from PIL import Image

TOPIC_A1 = {"id": "a1", "template": "A", "big": "blue whale heart", "reference": "golf cart",
            "fact": "A blue whale's heart is roughly the size of a golf cart.", "source": "NOAA",
            "question": "What animal should we scale next?"}
TOPIC_A2 = {**TOPIC_A1, "id": "a2", "big": "giraffe", "reference": "school bus",
            "fact": "A giraffe is about as tall as a three-story building would be wide."}
TOPIC_B1 = {"id": "b1", "template": "B", "big": "Alaska", "reference": "Texas",
            "fact": "Alaska is about 665,000 square miles, nearly two and a half times Texas.", "source": None,
            "question": "Which map surprised you most?"}

HOWBIG_REPLY = """IDEA: A blue whale heart is as big as a golf cart.
HOOK: Your heart versus a whale heart
IMAGE_PROMPT: Facebook post image 4:5 portrait, matte black background #0A0A0A, minimal flat vector style, blue whale heart huge vs golf cart small for scale comparison, yellow accent #FFD60A
TOP: WHALE HEART SIZE
BOTTOM:
CAPTION: You think big hearts are a metaphor.

A blue whale's heart is roughly the size of a golf cart.
What should we scale next?
Source: NOAA"""


def test_howbig_profile_and_prompt():
    p = bp.IMAGE_POST_PROFILES["howbig_image"]
    assert p["kind"] == "howbig_post" and p["templates"] is howbig.TEMPLATE_LABELS and p["week"] is None
    assert p["slot_hours"] == [11, 15, 22] and p["network_offsets"] == {}
    assert p["history_store"] == "howbig_post_history" and p["composition"] == "" and p["text_fill"] == (255, 255, 255)
    assert p["ai_design"] == "scale" and p["topics"] is True and p["max_hashtags"] == {"instagram": 0, "facebook": 1}
    assert p["default_trigger"] == "give me the next How Big Really post" and p["label"] == "Post How Big Really"
    text = bp._text_system_prompt("howbig_post")
    for needle in ("matte black background #0A0A0A", "#FFD60A", "white human silhouette 1.8m", "A - SCALE COMPARISON",
                   "B - MAP COMPARISON", "C - QUANTITY / STACK", "CAPTION FORMULA", "share this", "Source:", "IDEA:",
                   "BOTTOM:", "scale bar 1000km"):
        assert needle in text, needle
    assert "km2" not in text and "ONLY the numbers and units" in text  # el banco usa millas cuadradas: sin conversiones
    assert not any(ord(c) < 9 or ord(c) in (11, 12) for c in text)  # sin bytes de control (escritura de archivos)


def test_howbig_template_is_abc_and_differs_from_the_last(monkeypatch):
    hist = [{"template": "A", "hook": "h"}]
    monkeypatch.setattr(bp, "_load_gaming_history", lambda store: hist)
    for _ in range(40):
        t = bp._image_post_template("howbig_image", None)
        assert t in ("A", "B", "C") and t != "A"
    hist[:] = [{"template": "B", "hook": "h"}]
    assert all(bp._image_post_template("howbig_image", None) != "B" for _ in range(40))


def test_ai_scale_layout_pins_the_exact_title():
    out = bp._ai_scale_layout({"top": 'Blue "Whale" Heart'})
    assert out == (" Keep every element inside the central 90% of the frame, nothing near the edges. "
                   "The title text must be spelled exactly as written: \"Blue 'Whale' Heart\". "
                   "No other text, no logos, no watermark.")


def test_parse_howbig_reply_with_empty_bottom_and_multiline_caption():
    post = bp._parse_gaming_post(HOWBIG_REPLY)
    assert post["bottom"] == "" and post["top"] == "WHALE HEART SIZE"
    assert post["caption"].startswith("You think big hearts") and "\n\n" in post["caption"]
    assert post["caption"].endswith("Source: NOAA")


class _Calls:
    def __init__(self):
        self.messages, self.prompts, self.aspects, self.fits, self.history = [], [], [], [], []


@pytest.fixture
def howbig_env(monkeypatch):
    calls = _Calls()
    monkeypatch.setattr(howbig, "load_topics", lambda: [dict(TOPIC_A1), dict(TOPIC_A2), dict(TOPIC_B1)])
    monkeypatch.setattr(bp, "_load_gaming_history", lambda store: calls.history)

    def fake_text(kind, page, message, story_id, **kw):
        calls.messages.append((kind, message))
        return HOWBIG_REPLY
    monkeypatch.setattr(bp, "_generate_text_with_chain", fake_text)

    def fake_clips(story, dest, unattended, start_index=0, on_progress=None, provider="whatsapp",
                   generate_video=True, aspect="9:16"):
        calls.prompts.append(story["prompts"][0]["prompt"])
        calls.aspects.append(aspect)
        out = Path(dest) / "scene_000.jpg"
        Image.new("RGB", (8, 8)).save(out)
        return [str(out)]
    monkeypatch.setattr(bp.auto_pipeline, "generate_clips", fake_clips)

    def fake_fit(base, out, size, trim=True):
        calls.fits.append((size, trim))
        Image.new("RGB", (8, 8)).save(out)
        return out
    monkeypatch.setattr(bp.meme_maker, "fit_post", fake_fit)
    return calls


def test_generate_howbig_post_sends_topic_and_scale_layout(howbig_env, tmp_path):
    post = bp.generate_gaming_post("How Big Really", "give me the next How Big Really post", "flow",
                                   tmp_path / "p", "B", profile_key="howbig_image")
    kind, message = howbig_env.messages[0]
    assert kind == "howbig_post" and "TOPIC FOR THIS POST" in message
    assert post["topic_id"] == "b1" and post["template"] == "B"  # la plantilla pedida (B) tenia tema: se respeta
    assert "Alaska is about 665,000 square miles" in message and "Template for this post: B (Map comparison)" in message
    sent = howbig_env.prompts[0]
    assert sent.endswith(bp._ai_scale_layout(post))
    assert bp.GAMING_IMAGE_COMPOSITION not in sent and "square 1:1" not in sent
    assert howbig_env.aspects == ["3:4"]
    assert howbig_env.fits == [(meme_maker.SIZE_IG, False)]
    assert not (tmp_path / "p" / "base.jpg").exists()


def test_howbig_template_follows_the_topic_when_the_requested_one_has_none(howbig_env, tmp_path):
    howbig_env.history[:] = [{"hook": "x", "topic_id": "a1"}, {"hook": "y", "topic_id": "a2"}]
    post = bp.generate_gaming_post("HB", "t", "flow", tmp_path / "p", "A", profile_key="howbig_image")
    assert post["topic_id"] == "b1" and post["template"] == "B"
    assert "Template for this post: B (Map comparison)" in howbig_env.messages[0][1]


def test_howbig_topics_are_not_repeated_and_exhaustion_fails_before_flow(howbig_env, tmp_path):
    seen = []
    for i in range(3):
        post = bp.generate_gaming_post("HB", "t", "flow", tmp_path / f"p{i}", "A", profile_key="howbig_image")
        seen.append(post["topic_id"])
        howbig_env.history.append({"hook": f"h{i}", "topic_id": post["topic_id"]})
    assert sorted(seen) == ["a1", "a2", "b1"]
    texts, flows = len(howbig_env.messages), len(howbig_env.prompts)
    with pytest.raises(howbig.BankExhaustedError, match="exhausted"):
        bp.generate_gaming_post("HB", "t", "flow", tmp_path / "p9", "A", profile_key="howbig_image")
    assert (len(howbig_env.messages), len(howbig_env.prompts)) == (texts, flows)  # ni texto ni Flow


def test_howbig_batch_item_records_topic_id_and_real_template(monkeypatch):
    project = {"id": "p1", "type": "howbig_image", "page_name": "HOW BIG REALLY", "video_settings": {}, "networks": {},
               "videos": [{"index": 0, "status": "pending", "scheduled_at": "2026-10-14T11:00:00"}]}
    store = {"p1": project}
    monkeypatch.setattr(bp, "_load", lambda: store)
    monkeypatch.setattr(bp, "_save", lambda projects: None)
    monkeypatch.setattr(bp, "_load_gaming_history", lambda s: [])
    monkeypatch.setattr(bp, "generate_gaming_post", lambda *a, **k: {
        "hook": "h", "idea": "i", "caption": "c", "topic_id": "b1", "template": "B"})
    saved = []
    monkeypatch.setattr(bp, "_append_gaming_history", lambda entry, store_name="x": saved.append((entry, store_name)))
    bp._generate_batch_image_post("p1", 0)
    entry, store_name = saved[0]
    assert store_name == "howbig_post_history" and entry["topic_id"] == "b1" and entry["template"] == "B"
    assert project["videos"][0]["template"] == "B" and project["videos"][0]["status"] == "ready"


def test_fit_post_without_trim_keeps_the_dark_frame(tmp_path):
    base = tmp_path / "scale.png"
    img = Image.new("RGB", (810, 1080), (10, 10, 10))
    img.paste((255, 214, 10), (100, 100, 710, 980))  # figura amarilla con margen negro alrededor
    img.save(base)
    kept = meme_maker.fit_post(base, tmp_path / "keep.jpg", meme_maker.SIZE_IG, trim=False)
    trimmed = meme_maker.fit_post(base, tmp_path / "trim.jpg", meme_maker.SIZE_IG)
    with Image.open(kept) as a, Image.open(trimmed) as b:
        assert a.size == meme_maker.SIZE_IG and b.size == meme_maker.SIZE_IG
        assert a.getpixel((540, 675)) == pytest.approx((255, 214, 10), abs=14)
        assert a.getpixel((60, 675)) == pytest.approx((10, 10, 10), abs=14)  # el margen negro sigue ahi
        assert a.getpixel((1020, 675)) == pytest.approx((10, 10, 10), abs=14)
        assert a.getpixel((200, 675)) == pytest.approx((255, 214, 10), abs=14)  # figura en su posicion (133..947)
        assert b.getpixel((60, 675)) == pytest.approx((255, 214, 10), abs=14)  # con trim el margen se come y la figura se estira


def test_manual_howbig_job_records_topic_id_and_real_template(monkeypatch):
    import app
    monkeypatch.setattr(bp, "_image_post_template", lambda key, at: "A")
    monkeypatch.setattr(bp, "generate_gaming_post", lambda *a, **k: {
        "hook": "h", "idea": "i", "top": "T", "bottom": "", "caption": "c", "topic_id": "b1", "template": "B"})
    saved, updates = [], []
    monkeypatch.setattr(bp, "_append_gaming_history", lambda entry, store_name="x": saved.append((entry, store_name)))
    monkeypatch.setattr(app, "_gaming_update", lambda job_id, **fields: updates.append(fields))
    app._run_gaming_generate("j1", "HB", "t", "flow", profile_key="howbig_image")
    entry, store_name = saved[0]
    assert store_name == "howbig_post_history" and entry["topic_id"] == "b1" and entry["template"] == "B"
    assert updates[-1]["template"] == "B" and updates[-1]["status"] == "ready"


# ── Memoria de temas usados: no se poda (regresion: se repetian tras ~60 posts) ─

def _fake_history_store(monkeypatch):
    import job_store
    store = {}
    monkeypatch.setattr(job_store, "load", lambda name: {k: (list(v) if isinstance(v, list) else v) for k, v in store.get(name, {}).items()})
    monkeypatch.setattr(job_store, "save", lambda name, data: store.__setitem__(name, dict(data)))
    return store


def test_howbig_history_never_loses_topic_ids_but_other_profiles_are_pruned(monkeypatch):
    store = _fake_history_store(monkeypatch)
    ids = [t["id"] for t in howbig.load_topics()][:100]
    for i, tid in enumerate(ids):
        bp._append_gaming_history({"hook": f"h{i}", "topic_id": tid}, "howbig_post_history")
        bp._append_gaming_history({"hook": f"h{i}"}, "ninio_post_history")
    kept = [e["topic_id"] for e in store["howbig_post_history"]["entries"]]
    assert kept == ids  # los 100, sin podar
    assert len(store["ninio_post_history"]["entries"]) == bp.GAMING_HISTORY_MAX  # los demas perfiles siguen podando


def test_real_bank_fully_used_fails_before_text_or_flow(howbig_env, monkeypatch, tmp_path):
    monkeypatch.undo()  # howbig_env parchea un banco de 3 temas: aqui va el banco real
    calls = _Calls()
    all_ids = [t["id"] for t in howbig.load_topics()]
    assert len(all_ids) >= 100
    monkeypatch.setattr(bp, "_load_gaming_history", lambda store: [{"hook": "h", "topic_id": i} for i in all_ids])
    monkeypatch.setattr(bp, "_generate_text_with_chain", lambda *a, **k: calls.messages.append(a) or HOWBIG_REPLY)
    monkeypatch.setattr(bp.auto_pipeline, "generate_clips", lambda *a, **k: calls.prompts.append(a))
    with pytest.raises(howbig.BankExhaustedError, match="exhausted"):
        bp.generate_gaming_post("HB", "t", "flow", tmp_path / "p", "A", profile_key="howbig_image")
    assert calls.messages == [] and calls.prompts == []


def test_real_bank_with_70_used_never_picks_a_used_topic(howbig_env, monkeypatch, tmp_path):
    monkeypatch.undo()
    calls = _Calls()
    all_ids = [t["id"] for t in howbig.load_topics()]
    used = all_ids[:70]
    monkeypatch.setattr(bp, "_load_gaming_history", lambda store: [{"hook": "h", "topic_id": i} for i in used])
    monkeypatch.setattr(bp, "_generate_text_with_chain", lambda *a, **k: HOWBIG_REPLY)

    def fake_clips(story, dest, *a, **k):
        out = Path(dest) / "scene_000.jpg"
        Image.new("RGB", (8, 8)).save(out)
        return [str(out)]
    monkeypatch.setattr(bp.auto_pipeline, "generate_clips", fake_clips)
    monkeypatch.setattr(bp.meme_maker, "fit_post", lambda base, out, size, trim=True: Image.new("RGB", (8, 8)).save(out))
    for i in range(15):
        post = bp.generate_gaming_post("HB", "t", "flow", tmp_path / f"p{i}", "A", profile_key="howbig_image")
        assert post["topic_id"] not in set(used)


# ── Horario automatico por fase ────────────────────────────────────────────────

from datetime import date, datetime, timedelta


def _freeze_now(monkeypatch, now):
    """bp.datetime.now() -> `now`; el resto de datetime (constructor, fromisoformat) sigue igual."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(bp, "datetime", Frozen)


def test_howbig_hours_for_date_phases():
    launch = date(2026, 10, 1)
    assert bp.HOWBIG_PHASE1_DAYS == 14
    assert bp.HOWBIG_PHASE1_HOURS == [11, 15, 22] and bp.HOWBIG_PHASE2_HOURS == [11, 22]
    assert bp.HOWBIG_HOUR_END == 22
    assert bp.howbig_hours_for_date(launch, launch) == [11, 15, 22]
    assert bp.howbig_hours_for_date(launch + timedelta(days=13), launch) == [11, 15, 22]
    assert bp.howbig_hours_for_date(launch + timedelta(days=14), launch) == [11, 22]
    assert bp.howbig_hours_for_date(launch + timedelta(days=60), launch) == [11, 22]


def _howbig_schedule(total, launch, taken):
    return bp._compute_schedule(
        total, 3, None, hours_for_date=lambda d: bp.howbig_hours_for_date(d, launch),
        hour_end=bp.HOWBIG_HOUR_END, taken=taken)


def test_howbig_schedule_runs_3_a_day_then_2_a_day(monkeypatch):
    now = datetime(2026, 10, 15, 10, 30)
    _freeze_now(monkeypatch, now)
    launch = now.date() - timedelta(days=10)  # hoy es el dia 10: quedan 4 dias de fase 1 (10..13)
    slots = [datetime.fromisoformat(s) for s in _howbig_schedule(30, launch, [])]
    assert len(slots) == 30 and slots == sorted(slots) and len(set(slots)) == 30
    earliest = now + bp.NATIVE_SCHEDULE_MIN_LEAD
    assert all(s >= earliest for s in slots)
    assert all(9 <= s.hour <= 22 and s.hour in (11, 15, 22) for s in slots)
    per_day = Counter(s.date() for s in slots)
    days = sorted(per_day)
    assert all(per_day[d] <= 3 for d in days)
    assert [per_day[d] for d in days[:4]] == [3, 3, 3, 3]
    assert all(per_day[d] == 2 for d in days[4:-1])
    for d in days[4:]:
        assert d - launch >= timedelta(days=14)
    assert days[0] == now.date()  # hoy mismo: las 11:00 todavia caben


def test_howbig_schedule_skips_past_slots_of_today_without_shifting_the_batch(monkeypatch):
    now = datetime(2026, 10, 15, 12, 0)  # las 11:00 de hoy ya pasaron
    _freeze_now(monkeypatch, now)
    launch = now.date() - timedelta(days=10)
    slots = [datetime.fromisoformat(s) for s in _howbig_schedule(6, launch, [])]
    assert slots[:3] == [datetime(2026, 10, 15, 15), datetime(2026, 10, 15, 22), datetime(2026, 10, 16, 11)]
    assert len(slots) == 6 and slots == sorted(slots)


def test_howbig_schedule_taken_22h_uses_up_a_daily_slot(monkeypatch):
    now = datetime(2026, 10, 15, 10, 30)
    _freeze_now(monkeypatch, now)
    launch = now.date() - timedelta(days=10)
    taken = [datetime(2026, 10, 15, 22)]
    slots = [datetime.fromisoformat(s) for s in _howbig_schedule(3, launch, taken)]
    # el 22:00 ocupado gasta uno de los 3 cupos del dia: el post no se reubica a otra hora, pasa al dia siguiente
    assert slots == [datetime(2026, 10, 15, 11), datetime(2026, 10, 15, 15), datetime(2026, 10, 16, 11)]
    assert all(abs((s - datetime(2026, 10, 15, 22)).total_seconds()) >= bp.BATCH_MIN_GAP_SECONDS for s in slots)


def test_free_slot_without_hour_end_keeps_the_21h_cap(monkeypatch):
    now = datetime(2026, 10, 15, 10, 30)
    _freeze_now(monkeypatch, now)
    desired = datetime(2026, 10, 16, 22)
    assert bp._free_slot(desired, []).hour <= 21
    assert bp._free_slot(desired, [], hour_end=22) == desired


class _FakeStore:
    def __init__(self):
        self.data, self.saves = {}, []

    def load(self, name):
        return dict(self.data.get(name, {}))

    def save(self, name, value):
        self.saves.append(name)
        self.data[name] = value


def test_howbig_launch_date_is_saved_once(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(bp, "job_store", store)
    _freeze_now(monkeypatch, datetime(2026, 10, 15, 10, 30))
    first = bp._howbig_launch_date()
    assert first == date(2026, 10, 15)
    _freeze_now(monkeypatch, datetime(2026, 11, 3, 9, 0))
    assert bp._howbig_launch_date() == first
    assert store.saves == ["howbig_launch"]


def test_create_project_howbig_uses_the_phase_schedule(monkeypatch):
    store = _FakeStore()
    saved = {}
    monkeypatch.setattr(bp, "job_store", store)
    monkeypatch.setattr(bp, "_load", lambda: dict(saved))
    monkeypatch.setattr(bp, "_save", lambda projects: saved.update(projects))
    monkeypatch.setattr(bp, "existing_slots", lambda *a, **k: [])
    monkeypatch.setattr(bp, "_best_hour_for_networks", lambda networks: None)
    _freeze_now(monkeypatch, datetime(2026, 10, 15, 10, 30))
    project = bp.create_project("How Big Really", 10, 3, {"facebook": {"page_id": "1"}}, {},
                                content_type="howbig_image")
    assert project["per_day"] == 3 and len(project["videos"]) == 10
    slots = [datetime.fromisoformat(v["scheduled_at"]) for v in project["videos"]]
    assert slots == sorted(slots) and all(s.hour in (11, 15, 22) for s in slots)
    assert Counter(s.date() for s in slots)[date(2026, 10, 15)] == 3
    assert store.saves == ["howbig_launch"]
    assert bp._howbig_launch_date() == date(2026, 10, 15) and store.saves == ["howbig_launch"]
    # per_day pedido distinto: howbig manda su propio reparto
    again = bp.create_project("How Big Really", 4, 1, {"facebook": {"page_id": "1"}}, {}, content_type="howbig_image")
    assert again["per_day"] == 3 and store.saves == ["howbig_launch"]


def test_publish_window_and_reschedule_accept_a_wider_hour_end():
    assert bp._into_publish_window(datetime(2026, 10, 6, 22, 0), hour_end=22) == datetime(2026, 10, 6, 22, 0)
    assert bp._into_publish_window(datetime(2026, 10, 6, 22, 30), hour_end=22) == datetime(2026, 10, 7, 9, 0)
    assert bp._into_publish_window(datetime(2026, 10, 6, 22, 0)) == datetime(2026, 10, 7, 9, 0)  # por defecto sigue siendo 21
    slot = bp._next_free_reschedule_slot({}, ("p", 0), datetime(2026, 10, 6, 22, 0), hour_end=22)
    assert slot == datetime(2026, 10, 6, 22, 0)


def test_recover_stuck_videos_keeps_the_22h_howbig_slot(monkeypatch):
    saved = {
        "h": {"type": "howbig_image", "page_name": "HB", "videos": [
            {"index": 0, "status": "pending", "scheduled_at": "2026-10-20T22:00:00"},
            {"index": 1, "status": "pending", "scheduled_at": "2026-10-20T23:00:00"}]},
        "g": {"type": "gaming_image", "page_name": "G", "videos": [
            {"index": 0, "status": "pending", "scheduled_at": "2026-10-20T22:00:00"}]},
    }
    monkeypatch.setattr(bp, "_load", lambda: saved)
    monkeypatch.setattr(bp, "_save", lambda projects: None)
    bp._recover_stuck_videos()
    assert saved["h"]["videos"][0]["scheduled_at"] == "2026-10-20T22:00:00"
    assert saved["h"]["videos"][1]["scheduled_at"] == "2026-10-20T22:00:00"  # 23 h -> tope 22
    assert saved["g"]["videos"][0]["scheduled_at"] == "2026-10-20T21:00:00"  # el resto de las paginas: tope 21


# ── Tope diario por fase con horarios ya ocupados (lotes seguidos, posts externos) ──

def _phase_hours(day, launch):
    return bp.howbig_hours_for_date(day, launch)


@pytest.mark.parametrize("launch_days_ago", [10, 3])
def test_two_consecutive_batches_never_exceed_the_daily_phase_count(monkeypatch, launch_days_ago):
    now = datetime(2026, 10, 15, 10, 30)
    _freeze_now(monkeypatch, now)
    launch = now.date() - timedelta(days=launch_days_ago)
    taken = []
    first = [datetime.fromisoformat(s) for s in _howbig_schedule(12, launch, taken)]
    second = [datetime.fromisoformat(s) for s in _howbig_schedule(12, launch, taken)]
    everything = first + second
    assert len(everything) == 24 and len(set(everything)) == 24
    per_day = Counter(s.date() for s in everything)
    for day, count in per_day.items():
        assert count <= len(_phase_hours(day, launch)), day
    assert all(s.hour in _phase_hours(s.date(), launch) for s in everything)
    ordered = sorted(everything)
    assert all((b - a).total_seconds() >= bp.BATCH_MIN_GAP_SECONDS for a, b in zip(ordered, ordered[1:]))
    assert min(second) > max(first)  # el segundo lote arranca despues del primero, no encima


def test_a_full_day_in_taken_is_skipped(monkeypatch):
    now = datetime(2026, 10, 15, 10, 30)
    _freeze_now(monkeypatch, now)
    launch = now.date() - timedelta(days=10)
    taken = [datetime(2026, 10, 15, h) for h in (11, 15, 22)]
    slots = [datetime.fromisoformat(s) for s in _howbig_schedule(3, launch, taken)]
    assert slots == [datetime(2026, 10, 16, 11), datetime(2026, 10, 16, 15), datetime(2026, 10, 16, 22)]


def test_an_external_post_removes_the_nearby_hour_and_uses_up_capacity(monkeypatch):
    now = datetime(2026, 10, 15, 10, 30)
    _freeze_now(monkeypatch, now)
    launch = now.date() - timedelta(days=10)
    external = datetime(2026, 10, 16, 11, 30)  # a 30 min de las 11:00: esa hora ya no sirve
    taken = [external]
    slots = [datetime.fromisoformat(s) for s in _howbig_schedule(6, launch, taken)]
    assert slots == [datetime(2026, 10, 15, 11), datetime(2026, 10, 15, 15), datetime(2026, 10, 15, 22),
                     datetime(2026, 10, 16, 15), datetime(2026, 10, 16, 22), datetime(2026, 10, 17, 11)]
    assert Counter(s.date() for s in slots + [external])[date(2026, 10, 16)] == 3


def test_day_14_boundary_with_prefilled_taken(monkeypatch):
    now = datetime(2026, 10, 15, 10, 30)
    _freeze_now(monkeypatch, now)
    launch = date(2026, 10, 2)  # 15/10 = dia 13 (fase 1), 16/10 = dia 14 (fase 2)
    taken = [datetime(2026, 10, 15, 11), datetime(2026, 10, 16, 22)]
    slots = [datetime.fromisoformat(s) for s in _howbig_schedule(4, launch, taken[:])]
    assert slots == [datetime(2026, 10, 15, 15), datetime(2026, 10, 15, 22),
                     datetime(2026, 10, 16, 11), datetime(2026, 10, 17, 11)]


def test_an_external_post_exactly_one_hour_away_only_uses_up_capacity(monkeypatch):
    now = datetime(2026, 10, 15, 10, 30)
    _freeze_now(monkeypatch, now)
    launch = now.date() - timedelta(days=10)
    taken = [datetime(2026, 10, 16, 12)]  # a exactamente 1 h de las 11:00 y las 22:00 queda libre: el hueco minimo se cumple
    slots = [datetime.fromisoformat(x) for x in _howbig_schedule(5, launch, taken)]
    on_16 = [x for x in slots if x.date() == date(2026, 10, 16)]
    assert on_16 == [datetime(2026, 10, 16, 11), datetime(2026, 10, 16, 15)]  # 2 cupos + el externo = 3


# ---------------------------------------------------------------- panel (app.py)

import base64


def _panel_client(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}
    return app_module, app_module.app.test_client(), auth


def test_main_page_offers_howbig_in_both_selectors_with_the_schedule_note(monkeypatch):
    app_module, client, auth = _panel_client(monkeypatch)
    resp = client.get("/", headers=auth)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert html.count('<option value="howbig_image">') == 2  # "Crear video" y lote
    assert "How Big Really (4:5, solo Facebook)" in html
    assert "Horario automático: 3 posts al día los primeros 14 días y 2 al día después (hora Chile). Solo Facebook." in html
    assert 'howbig_image: "give me the next How Big Really post"' in html
    assert 'howbig_image: "howbig"' in html
    assert 'type === "howbig_image"' in html  # isImagePostType


def test_batch_create_route_keeps_only_facebook_for_howbig(monkeypatch):
    app_module, client, auth = _panel_client(monkeypatch)
    store, saved, seen = _FakeStore(), {}, {}
    monkeypatch.setattr(bp, "job_store", store)
    monkeypatch.setattr(bp, "_load", lambda: dict(saved))
    monkeypatch.setattr(bp, "_save", lambda projects: saved.update(projects))
    monkeypatch.setattr(bp, "existing_slots", lambda page, networks, *a, **k: seen.update(slots_networks=dict(networks)) or [])
    monkeypatch.setattr(bp, "_best_hour_for_networks", lambda networks: seen.update(hour_networks=dict(networks)))
    _freeze_now(monkeypatch, datetime(2026, 10, 15, 10, 30))
    payload = {"page_name": "How Big Really", "type": "howbig_image", "total_videos": 4, "per_day": 3,
               "networks": {"facebook": {"page_id": "1"}, "instagram": {"page_id": "1"}, "youtube": True},
               "video_settings": {}}
    resp = client.post("/api/batch/create", json=payload, headers=auth)
    data = resp.get_json()
    assert data["ok"], data
    assert list(data["project"]["networks"]) == ["facebook"]
    assert data["project"]["networks"]["facebook"] == {"page_id": "1"}
    assert list(saved[data["project"]["id"]]["networks"]) == ["facebook"]
    assert list(seen["slots_networks"]) == ["facebook"] and list(seen["hour_networks"]) == ["facebook"]


def test_create_project_howbig_never_keeps_instagram_or_youtube(monkeypatch):
    store, saved = _FakeStore(), {}
    monkeypatch.setattr(bp, "job_store", store)
    monkeypatch.setattr(bp, "_load", lambda: dict(saved))
    monkeypatch.setattr(bp, "_save", lambda projects: saved.update(projects))
    monkeypatch.setattr(bp, "existing_slots", lambda *a, **k: [])
    monkeypatch.setattr(bp, "_best_hour_for_networks", lambda networks: None)
    _freeze_now(monkeypatch, datetime(2026, 10, 15, 10, 30))
    only_ig = bp.create_project("How Big Really", 2, 3, {"instagram": {"page_id": "1"}}, {}, content_type="howbig_image")
    assert only_ig["networks"] == {}  # el llamador valida que quede al menos una red
    nets = {"facebook": {"page_id": "1"}, "instagram": {"page_id": "1"}, "youtube": True}
    both = bp.create_project("How Big Really", 2, 3, nets, {}, content_type="howbig_image")
    assert both["networks"] == {"facebook": {"page_id": "1"}}
    assert set(nets) == {"facebook", "instagram", "youtube"}  # no muta el dict del llamador
    other = bp.create_project("Otra", 2, 3, dict(nets), {}, content_type="ninio_image")
    assert set(other["networks"]) == {"facebook", "instagram", "youtube"}  # el resto de tipos no cambia


def test_batch_create_route_rejects_a_howbig_batch_without_facebook(monkeypatch):
    app_module, client, auth = _panel_client(monkeypatch)
    called = []
    monkeypatch.setattr(bp, "create_project", lambda *a, **k: called.append(1))
    payload = {"page_name": "How Big Really", "type": "howbig_image", "total_videos": 2, "per_day": 3,
               "networks": {"instagram": {"page_id": "1"}}, "video_settings": {}}
    data = client.post("/api/batch/create", json=payload, headers=auth).get_json()
    assert data["ok"] is False and "Facebook" in data["error"] and not called


class _FakeThread:
    started = []

    def __init__(self, target=None, args=(), **kw):
        self.target, self.args = target, args

    def start(self):
        _FakeThread.started.append((self.target, self.args))


def test_single_post_route_maps_the_howbig_kind_to_its_profile(monkeypatch):
    app_module, client, auth = _panel_client(monkeypatch)
    _FakeThread.started = []
    monkeypatch.setattr(app_module, "_gaming_jobs", {})
    monkeypatch.setattr(app_module.job_store, "save", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "_prune_gaming_jobs", lambda: None)
    monkeypatch.setattr(app_module.threading, "Thread", _FakeThread)
    resp = client.post("/api/gaming/start", json={"kind": "howbig", "page_name": "How Big Really"}, headers=auth)
    assert resp.get_json()["ok"]
    (target, args), = _FakeThread.started
    assert target is app_module._run_gaming_generate
    assert args[1:] == ("How Big Really", "give me the next How Big Really post", "whatsapp", "", "howbig_image")
    (job,) = app_module._gaming_jobs.values()
    assert job["profile"] == "howbig_image"


def test_single_post_publish_never_sends_a_howbig_post_to_instagram(monkeypatch):
    app_module, client, auth = _panel_client(monkeypatch)
    _FakeThread.started = []
    jobs = {"j1": {"kind": "image", "profile": "howbig_image", "status": "ready", "networks": {}, "caption": "c"}}
    monkeypatch.setattr(app_module, "_gaming_jobs", jobs)
    monkeypatch.setattr(app_module.job_store, "save", lambda *a, **k: None)
    monkeypatch.setattr(app_module.meta_auth, "list_pages", lambda: [{"page_id": "1", "name": "HB"}])
    monkeypatch.setattr(app_module.threading, "Thread", _FakeThread)
    resp = client.post("/api/gaming/publish", json={"job_id": "j1", "facebook": True, "instagram": True}, headers=auth)
    assert resp.get_json()["ok"]
    assert [a[1] for _, a in _FakeThread.started] == ["facebook"]
    assert list(jobs["j1"]["networks"]) == ["facebook"]
    only_ig = client.post("/api/gaming/publish", json={"job_id": "j1", "instagram": True}, headers=auth)
    assert only_ig.status_code == 400 and "Facebook" in only_ig.get_json()["error"]


def test_format_caption_blank_line_and_source_from_bank():
    raw = "Shock line.\nHard fact here.\nWhat next?\nSource: Wikipedia"
    assert howbig.format_caption(raw, {"source": "NASA"}) == "Shock line.\nHard fact here.\n\nWhat next?\nSource: NASA"
    assert howbig.format_caption("Shock.\nFact.\nQ?", {"source": None}) == "Shock.\nFact.\n\nQ?"


def test_format_caption_hashtags_go_last_and_empty_is_empty():
    topic = {"source": "NASA"}
    want = "Shock.\nFact.\n\nQ?\nSource: NASA\n#HowBig"
    assert howbig.format_caption("Shock.\nFact.\nQ?\n#HowBig", topic) == want
    assert howbig.format_caption("Shock.\nFact.\n\nQ?\nSource: NASA\n#HowBig", topic) == want
    assert howbig.format_caption("Shock.\nFact.\nQ? #HowBig", topic) == want
    assert howbig.format_caption("", topic) == ""
    assert howbig.format_caption("Shock.\nFact.\nQ?\n(Source: Wikipedia)", {"source": None}) == "Shock.\nFact.\n\nQ?"
