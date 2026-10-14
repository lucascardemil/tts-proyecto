# -*- coding: utf-8 -*-
"""Estado de la oficina de agentes: pura sobre datos falsos, sin red ni disco."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import office_state as os_

NOW = datetime(2026, 10, 13, 15, 0)


def vid(status, when=None, error=None):
    return {"status": status, "scheduled_at": when.isoformat() if when else None, "error": error}


def proj(videos=(), page="MACRAME CREATIVO", status="running", pid="p1", **extra):
    return {"id": pid, "name": "Lote 1", "page_name": page, "status": status,
            "videos": [dict(index=i, **v) for i, v in enumerate(videos)], **extra}


def data(**kw):
    base = {"projects": [], "flow": {"state": "ok", "message": ""}, "text_backends": ["freellm:x"],
            "tokens": [], "remote_times": {}, "metrics_rows": {"facebook": [], "instagram": []},
            "seen": {}, "errors": {}}
    base.update(kw)
    return base


def test_videos_agent_is_idle_without_batches():
    state = os_.build_state(data(), NOW)
    assert state["ok"] is True
    assert state["agents"]["videos"] == {
        "status": "idle", "bubble": "Sin trabajo",
        "detail": {"generating": 0, "pending": 0, "errors": 0, "projects": []}}
    assert state["findings"] == []


def test_videos_agent_busy_shows_render_progress():
    p = proj([vid("ready"), vid("generating"), vid("pending")])
    agent = os_.build_state(data(projects=[p]), NOW)["agents"]["videos"]
    assert agent["status"] == "busy"
    assert agent["bubble"] == "Render 1/3"
    assert agent["detail"]["projects"] == [
        {"id": "p1", "name": "Lote 1", "page_name": "MACRAME CREATIVO", "done": 1, "total": 3}]


def test_videos_agent_shows_queue_when_nothing_is_generating():
    p = proj([vid("pending"), vid("pending")])
    assert os_.build_state(data(projects=[p]), NOW)["agents"]["videos"]["bubble"] == "2 en cola"


def test_error_item_becomes_a_finding_with_retry_action():
    p = proj([vid("error", error="Flow no devolvió imagen en 240s.")])
    state = os_.build_state(data(projects=[p]), NOW)
    assert state["agents"]["videos"]["status"] == "error"
    assert state["agents"]["videos"]["bubble"] == "1 con error"
    (finding,) = [f for f in state["findings"] if f["agent"] == "videos"]
    assert finding["id"] == "error:p1:0"
    assert finding["agent"] == "videos" and finding["severity"] == "error"
    assert "ítem 1" in finding["text"] and "Flow no devolvió" in finding["text"]
    assert finding["action"] == {"label": "Reintentar", "kind": "call", "method": "POST",
                                 "url": "/api/batch/retry/p1/0"}


def test_inactive_batches_and_removed_items_are_ignored():
    done = proj([vid("error")], status="done", pid="p1")
    removed = proj([vid("removed")], pid="p2")
    state = os_.build_state(data(projects=[done, removed]), NOW)
    assert state["agents"]["videos"]["status"] == "idle"
    assert state["findings"] == []


def test_projects_read_failure_degrades_videos_to_warn():
    agent = os_.build_state(data(errors={"projects": "boom"}), NOW)["agents"]["videos"]
    assert agent["status"] == "warn" and agent["bubble"] == "Sin datos"


def at(day_offset, hour):
    d = NOW + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour)


def test_publica_counts_scheduled_items_and_reports_next_post():
    p = proj([vid("ready", at(0, 19)), vid("published", at(1, 12)), vid("pending", at(0, 9))])
    agent = os_.build_state(data(projects=[p]), NOW)["agents"]["publica"]
    assert agent["status"] == "ok" and agent["bubble"] == "2 programados"
    assert agent["detail"]["next_post"] == at(0, 19).isoformat()
    assert agent["detail"]["per_page"] == {"MACRAME CREATIVO": 2}


def test_publica_is_idle_when_nothing_is_scheduled():
    agent = os_.build_state(data(), NOW)["agents"]["publica"]
    assert agent["status"] == "idle" and agent["bubble"] == "Nada agendado"
    assert agent["detail"]["next_post"] is None


def test_publica_shows_publishing_and_publish_errors():
    busy = os_.build_state(data(projects=[proj([vid("publishing", at(0, 19))])]), NOW)["agents"]["publica"]
    assert busy["status"] == "busy" and busy["bubble"] == "Publicando"
    state = os_.build_state(data(projects=[proj([vid("publish_error", at(0, 19), error="token vencido")])]), NOW)
    assert state["agents"]["publica"]["status"] == "error"
    assert state["agents"]["publica"]["bubble"] == "1 sin publicar"
    (finding,) = [f for f in state["findings"] if f["agent"] == "publica"]
    assert finding["id"] == "publish_error:p1:0"
    assert finding["action"]["url"] == "/api/batch/retry-publish/p1/0"


def test_next_post_also_looks_at_posts_already_scheduled_on_facebook():
    remote = {"macrame creativo": [at(0, 17)]}
    agent = os_.build_state(data(projects=[proj([vid("ready", at(1, 12))])], remote_times=remote), NOW)["agents"]["publica"]
    assert agent["detail"]["next_post"] == at(0, 17).isoformat()
    assert agent["bubble"] == "1 programado"  # el conteo es solo de los lotes locales


def gap_ids(state):
    return {f["id"] for f in state["findings"] if f["id"].startswith("gap:")}


def test_gap_finding_when_today_and_tomorrow_have_no_posts():
    p = proj([vid("ready", at(2, 19))])
    state = os_.build_state(data(projects=[p]), NOW)
    assert gap_ids(state) == {f"gap:MACRAME CREATIVO:{NOW.date()}", f"gap:MACRAME CREATIVO:{(NOW + timedelta(days=1)).date()}"}
    today = next(f for f in state["findings"] if f["id"].endswith(str(NOW.date())))
    assert today["severity"] == "error" and today["text"] == "Hoy sin posts en MACRAME CREATIVO"
    assert today["action"] == {"label": "Abrir lotes", "kind": "open_view", "target": "lote"}
    tomorrow = next(f for f in state["findings"] if f["id"].endswith(str((NOW + timedelta(days=1)).date())))
    assert tomorrow["severity"] == "warn" and tomorrow["text"] == "Mañana sin posts en MACRAME CREATIVO"


def test_no_gap_when_both_days_are_covered_by_the_batch():
    p = proj([vid("published", at(0, 9)), vid("ready", at(1, 19))])
    assert gap_ids(os_.build_state(data(projects=[p]), NOW)) == set()


def test_no_gap_when_facebook_already_has_the_posts():
    p = proj([vid("ready", at(2, 19))])
    remote = {"macrame creativo": [at(0, 19), at(1, 19)]}
    assert gap_ids(os_.build_state(data(projects=[p], remote_times=remote), NOW)) == set()


def test_after_9pm_only_tomorrow_is_checked():
    late = datetime(2026, 10, 13, 21, 30)
    p = proj([vid("ready", at(2, 19))])
    assert gap_ids(os_.build_state(data(projects=[p]), late)) == {f"gap:MACRAME CREATIVO:{(late + timedelta(days=1)).date()}"}


def test_error_items_do_not_cover_a_day():
    p = proj([vid("error", at(0, 19)), vid("ready", at(1, 19))])
    assert gap_ids(os_.build_state(data(projects=[p]), NOW)) == {f"gap:MACRAME CREATIVO:{NOW.date()}"}


def ids(state):
    return {f["id"] for f in state["findings"]}


def test_invalid_meta_token_is_an_error_finding():
    tokens = [{"page_name": "MACRAME CREATIVO", "ok": False, "error": "token vencido"}]
    state = os_.build_state(data(tokens=tokens), NOW)
    finding = next(f for f in state["findings"] if f["id"] == "token-invalid:MACRAME CREATIVO")
    assert finding["severity"] == "error" and finding["agent"] == "revisor"
    assert finding["text"] == "Token de Meta inválido en MACRAME CREATIVO"
    assert finding["action"]["target"] == "ajustes"


def test_token_that_could_not_be_checked_is_a_warning_without_the_error_text():
    tokens = [{"page_name": "MACRAME CREATIVO", "ok": False, "auth_error": False,
               "error": "GET https://graph.facebook.com/x?access_token=SECRET123 timeout"}]
    state = os_.build_state(data(tokens=tokens), NOW)
    assert ids(state) == {"token-check:MACRAME CREATIVO"}
    finding = state["findings"][0]
    assert finding["severity"] == "warn" and finding["agent"] == "revisor"
    assert finding["text"] == "No pude verificar el token de MACRAME CREATIVO"
    assert finding["action"] == {"label": "Abrir ajustes", "kind": "open_view", "target": "ajustes"}
    assert "SECRET123" not in str(finding)
    explicit = [{"page_name": "X", "ok": False, "auth_error": True}]
    assert ids(os_.build_state(data(tokens=explicit), NOW)) == {"token-invalid:X"}


def test_token_close_to_expiry_is_a_warning_and_healthy_token_is_silent():
    soon = {"page_name": "Bebé Héroe", "ok": True, "expires_at": (NOW + timedelta(days=3)).isoformat()}
    fine = {"page_name": "HISTORIAS", "ok": True, "expires_at": (NOW + timedelta(days=40)).isoformat()}
    never = {"page_name": "Niño", "ok": True, "expires_at": None}
    state = os_.build_state(data(tokens=[soon, fine, never]), NOW)
    assert ids(state) == {"token-expiry:BEBÉ HÉROE"}
    assert state["findings"][0]["severity"] == "warn"
    assert state["findings"][0]["text"] == "El token de Bebé Héroe vence en 3 días"


def test_token_expiring_in_one_day_uses_the_singular():
    one = {"page_name": "HISTORIAS", "ok": True, "expires_at": (NOW + timedelta(days=1, hours=2)).isoformat()}
    finding = os_.build_state(data(tokens=[one]), NOW)["findings"][0]
    assert finding["text"] == "El token de HISTORIAS vence en 1 día"


def test_missing_text_provider_is_an_error_but_unknown_is_not():
    assert "text-provider" in ids(os_.build_state(data(text_backends=[]), NOW))
    assert "text-provider" not in ids(os_.build_state(data(text_backends=None), NOW))


def test_flow_finding_only_when_a_flow_batch_has_work_left():
    down = {"state": "no_browser", "message": "El navegador de Flow está cerrado."}
    flow_batch = proj([vid("pending", at(1, 19))], video_settings={"image_provider": "flow"})
    other_batch = proj([vid("pending", at(1, 19))], video_settings={"image_provider": "whatsapp"})
    with_flow = os_.build_state(data(projects=[flow_batch], flow=down), NOW)
    assert next(f for f in with_flow["findings"] if f["id"] == "flow")["text"] == "Flow: El navegador de Flow está cerrado."
    assert "flow" not in ids(os_.build_state(data(projects=[other_batch], flow=down), NOW))
    assert "flow" not in ids(os_.build_state(data(projects=[flow_batch], flow={"state": "ok", "message": ""}), NOW))


def test_item_stuck_longer_than_the_threshold_is_flagged():
    p = proj([vid("generating", at(1, 19))])
    key = os_.stuck_key("p1", 0, "generating")
    stuck = os_.build_state(data(projects=[p], seen={key: NOW - timedelta(minutes=50)}), NOW)
    finding = next(f for f in stuck["findings"] if f["id"] == f"stuck:{key}")
    assert finding["severity"] == "warn" and "50 min" in finding["text"]
    fresh = os_.build_state(data(projects=[p], seen={key: NOW - timedelta(minutes=10)}), NOW)
    assert f"stuck:{key}" not in ids(fresh)


def test_stuck_tracker_remembers_first_sighting_and_forgets_finished_items():
    tracker = os_.StuckTracker()
    p = proj([vid("generating")])
    key = os_.stuck_key("p1", 0, "generating")
    assert tracker.update([p], NOW) == {key: NOW}
    assert tracker.update([p], NOW + timedelta(minutes=5)) == {key: NOW}
    p["videos"][0]["status"] = "ready"
    assert tracker.update([p], NOW + timedelta(minutes=6)) == {}


def test_failed_reads_become_warnings_and_other_agents_survive():
    state = os_.build_state(data(errors={"tokens": "RuntimeError: sin red"}), NOW)
    finding = next(f for f in state["findings"] if f["id"] == "read:tokens")
    assert finding["severity"] == "warn"
    assert finding["text"] == "No pude leer los tokens de Meta: RuntimeError: sin red"
    assert {"videos", "publica", "revisor"} <= set(state["agents"])


def test_revisor_summarises_all_findings():
    clean = os_.build_state(data(), NOW)["agents"]["revisor"]
    assert clean["status"] == "ok" and clean["bubble"] == "Todo en orden"
    one = os_.build_state(data(text_backends=[]), NOW)["agents"]["revisor"]
    assert one["status"] == "error" and one["bubble"] == "1 alerta"
    warn_only = os_.build_state(data(errors={"tokens": "x"}), NOW)["agents"]["revisor"]
    assert warn_only["status"] == "warn"
    p = proj([vid("error"), vid("error")])
    many = os_.build_state(data(projects=[p], text_backends=[]), NOW)["agents"]["revisor"]
    assert many["bubble"] == "5 alertas"  # 2 errores de ítem + 2 huecos (hoy, mañana) + sin proveedor


def test_gap_findings_are_skipped_when_batches_could_not_be_read():
    remote = {"macrame creativo": [at(0, 19)]}  # sin lotes leidos, esto solo daria un falso hueco de mañana
    state = os_.build_state(data(errors={"projects": "boom"}, remote_times=remote), NOW)
    assert not any(i.startswith("gap:") for i in ids(state))
    finding = next(f for f in state["findings"] if f["id"] == "read:projects")
    assert finding["text"] == "No pude leer los lotes: boom"


def rows(*hours_and_views, titles=("uno dos tres", "cuatro cinco", "seis siete ocho")):
    return [{"published_at": f"2026-10-1{i}T{h:02d}:00:00", "views": v, "title": titles[i % 3]}
            for i, (h, v) in enumerate(hours_and_views)]


def test_metricas_agent_reports_best_daypart_and_top_posts():
    fb = rows((20, 500), (21, 700), (9, 50))
    agent = os_.build_state(data(metrics_rows={"facebook": fb, "instagram": []}), NOW)["agents"]["metricas"]
    assert agent["status"] == "ok" and agent["bubble"] == "Mejor: noche"
    assert agent["detail"]["facebook"]["best_time"]["best_daypart"].startswith("noche")
    assert [t["views"] for t in agent["detail"]["facebook"]["top"]] == [700, 500, 50]
    assert agent["detail"]["instagram"]["best_time"]["insufficient_data"] is True


def test_metricas_agent_is_idle_without_enough_data_and_warns_on_read_failure():
    assert os_.build_state(data(), NOW)["agents"]["metricas"]["bubble"] == "Sin datos"
    assert os_.build_state(data(), NOW)["agents"]["metricas"]["status"] == "idle"
    failed = os_.build_state(data(errors={"metrics_rows": "Graph caído"}), NOW)["agents"]["metricas"]
    assert failed["status"] == "warn" and failed["bubble"] == "Sin datos"


def test_all_four_agents_exist_and_bubbles_fit_the_scene():
    busy = proj([vid("generating", at(1, 19)), vid("error", error="x" * 300), vid("publish_error", at(1, 19))])
    state = os_.build_state(data(projects=[busy], text_backends=[],
                                 metrics_rows={"facebook": rows((20, 5), (21, 6), (22, 7)), "instagram": []}), NOW)
    assert set(state["agents"]) == {"videos", "publica", "metricas", "revisor"}
    for agent in state["agents"].values():
        assert set(agent) == {"status", "bubble", "detail"}
        assert agent["status"] in {"busy", "ok", "idle", "warn", "error"}
        assert len(agent["bubble"]) <= os_.MAX_BUBBLE
    assert all(len(f["text"]) < 400 for f in state["findings"])


def test_findings_are_sorted_errors_first():
    state = os_.build_state(data(text_backends=[], errors={"tokens": "x"}), NOW)
    severities = [f["severity"] for f in state["findings"]]
    assert severities == sorted(severities, key=lambda s: os_.SEVERITY_ORDER[s])


def test_collect_state_caches_failures_and_keeps_going():
    os_.reset()
    calls = {"tokens": 0}

    def bad_tokens():
        calls["tokens"] += 1
        raise RuntimeError("sin red")

    readers = {"projects": lambda: [], "tokens": bad_tokens}
    first = os_.collect_state(readers, NOW)
    second = os_.collect_state(readers, NOW)
    assert calls["tokens"] == 1  # el fallo se cachea: no se martilla a Meta cada 3 s
    assert {f["id"] for f in first["findings"] if f["id"].startswith("read:")} == {"read:tokens"}
    assert second["agents"]["videos"]["status"] == "idle"


def test_collect_state_caches_slow_sources_but_not_the_batches():
    os_.reset()
    calls = {"projects": 0, "tokens": 0}

    def projects():
        calls["projects"] += 1
        return []

    def tokens():
        calls["tokens"] += 1
        return []

    readers = {"projects": projects, "tokens": tokens}
    os_.collect_state(readers, NOW)
    os_.collect_state(readers, NOW)
    assert calls == {"projects": 2, "tokens": 1}


def test_collect_state_feeds_the_stuck_tracker():
    os_.reset()
    p = proj([vid("generating")])
    os_.collect_state({"projects": lambda: [p]}, NOW)
    later = os_.collect_state({"projects": lambda: [p]}, NOW + timedelta(minutes=50))
    assert any(f["id"].startswith("stuck:") for f in later["findings"])


import base64


def _auth():
    return {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}


def test_office_endpoint_requires_auth_and_returns_the_four_agents(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    monkeypatch.setattr(app_module, "_office_readers", lambda: {"projects": lambda: []})
    os_.reset()
    client = app_module.app.test_client()
    assert client.get("/api/office/state").status_code == 401
    body = client.get("/api/office/state", headers=_auth()).get_json()
    assert body["ok"] is True
    assert set(body["agents"]) == {"videos", "publica", "metricas", "revisor"}


def test_office_endpoint_never_returns_500(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    monkeypatch.setattr(app_module, "_office_readers", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    response = app_module.app.test_client().get("/api/office/state", headers=_auth())
    assert response.status_code == 200
    assert response.get_json()["ok"] is False


def test_office_endpoint_error_does_not_leak_tokens(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")

    def boom():
        raise RuntimeError("GET https://graph.facebook.com/x?access_token=SECRET9&a=1")

    monkeypatch.setattr(app_module, "_office_readers", boom)
    body = app_module.app.test_client().get("/api/office/state", headers=_auth()).get_json()
    assert body["ok"] is False
    assert "SECRET9" not in body["error"] and "access_token=***" in body["error"]


def test_office_read_published_degrades_to_empty_list(tmp_path):
    import app as app_module
    assert app_module._office_read_published(tmp_path / "no_existe.json") == []
    broken = tmp_path / "roto.json"
    broken.write_text("{no es json", encoding="utf-8")
    assert app_module._office_read_published(broken) == []
    not_a_list = tmp_path / "objeto.json"
    not_a_list.write_text('{"a": 1}', encoding="utf-8")
    assert app_module._office_read_published(not_a_list) == []
    good = tmp_path / "ok.json"
    good.write_text('[{"video_id": "1"}]', encoding="utf-8")
    assert app_module._office_read_published(good) == [{"video_id": "1"}]
    mixed = tmp_path / "mixto.json"
    mixed.write_text('[1, "x", null, {"video_id": "2"}]', encoding="utf-8")
    assert app_module._office_read_published(mixed) == [{"video_id": "2"}]


def test_office_metrics_rows_only_reads_the_files(monkeypatch, tmp_path):
    import app as app_module
    fb = tmp_path / "facebook_published.json"
    ig = tmp_path / "instagram_published.json"
    fb_text = '[{"video_id": "1", "page_name": "A"}]'
    ig_text = '[{"media_id": "2", "page_name": "B"}]'
    fb.write_text(fb_text, encoding="utf-8")
    ig.write_text(ig_text, encoding="utf-8")
    monkeypatch.setattr(app_module, "_PUBLISHED_FB_PATH", fb)
    monkeypatch.setattr(app_module, "_PUBLISHED_IG_PATH", ig)

    def no_graph(*args, **kwargs):
        raise AssertionError("la oficina no debe llamar a Graph")

    monkeypatch.setattr(app_module.facebook_publisher, "get_video_stats", no_graph)
    monkeypatch.setattr(app_module.instagram_publisher, "get_media_stats", no_graph)
    rows = app_module._office_metrics_rows()
    assert rows == {"facebook": [{"video_id": "1", "page_name": "A"}],
                    "instagram": [{"media_id": "2", "page_name": "B"}]}
    assert fb.read_text(encoding="utf-8") == fb_text
    assert ig.read_text(encoding="utf-8") == ig_text


def test_office_tokens_drops_access_token_and_keeps_page_name(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module.meta_auth, "list_pages",
                        lambda: [{"page_id": "1", "name": "PAG UNO"}, {"page_id": "2", "name": "PAG DOS"}])
    monkeypatch.setattr(app_module.meta_auth, "validate",
                        lambda page_id: {"ok": True, "page_id": page_id, "access_token": "SECRETO", "days_left": 30})
    tokens = app_module._office_tokens()
    assert [t["page_name"] for t in tokens] == ["PAG UNO", "PAG DOS"]
    assert all("access_token" not in t for t in tokens)


def test_office_remote_times_skips_pages_that_could_not_be_read(monkeypatch):
    import app as app_module
    when = [datetime(2026, 10, 14, 10, 0)]
    monkeypatch.setattr(app_module.meta_auth, "list_pages",
                        lambda: [{"page_id": "1", "name": "PAG UNO"}, {"page_id": "2", "name": "PAG DOS"}])
    monkeypatch.setattr(app_module.facebook_publisher, "list_post_times",
                        lambda page_id: when if page_id == "1" else None)
    assert app_module._office_remote_times() == {"PAG UNO": when}


def test_read_errors_redact_access_tokens():
    os_.reset()

    def boom():
        raise RuntimeError("GET https://graph.facebook.com/x?access_token=SECRET123&a=1 fallo")

    state = os_.collect_state({"projects": lambda: [], "tokens": boom}, NOW)
    finding = next(f for f in state["findings"] if f["id"] == "read:tokens")
    assert "SECRET123" not in finding["text"]
    assert "access_token=***" in finding["text"]


def test_item_error_text_does_not_leak_tokens():
    p = proj([vid("error", error="GET https://graph.facebook.com/x?input_token=SECRET1&client_secret=SECRET2&access_token=SECRET3 fallo")])
    text = next(f for f in os_.build_state(data(projects=[p]), NOW)["findings"] if f["agent"] == "videos")["text"]
    assert "SECRET" not in text
    assert "input_token=***" in text and "client_secret=***" in text and "access_token=***" in text


def test_a_broken_metrics_payload_only_degrades_metricas():
    state = os_.build_state(data(metrics_rows={"facebook": ["x"], "instagram": []}), NOW)
    assert state["agents"]["metricas"]["status"] in ("warn", "idle")
    assert state["agents"]["metricas"]["bubble"] == "Sin datos"
    assert state["agents"]["videos"]["bubble"] == "Sin trabajo"
    assert state["agents"]["publica"]["bubble"] == "Nada agendado"
    assert state["agents"]["revisor"]["bubble"] == "Todo en orden"
    assert os_.build_state(data(metrics_rows=["x"]), NOW)["agents"]["metricas"]["bubble"] == "Sin datos"


def test_an_agent_that_raises_does_not_take_the_others_down(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(os_, "_publica_agent", boom)
    state = os_.build_state(data(), NOW)
    assert state["agents"]["publica"] == {"status": "warn", "bubble": "Sin datos", "detail": {}}
    assert state["agents"]["videos"]["bubble"] == "Sin trabajo"
    assert state["agents"]["revisor"]["bubble"] == "Todo en orden"


def test_a_failing_revisor_still_returns_the_state(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(os_, "_revisor_findings", boom)
    state = os_.build_state(data(), NOW)
    assert state["findings"] == []
    assert state["agents"]["revisor"]["bubble"] == "Todo en orden"


import re

AGENT_IDS = ("videos", "publica", "metricas", "revisor")


def _office_blocks():
    import app as app_module
    blocks = re.findall(r"(?:/\*|<!--) OFFICE:START (?:\*/|-->)(.*?)(?:/\*|<!--) OFFICE:END (?:\*/|-->)",
                        app_module.HTML, flags=re.S)
    assert blocks, "faltan los marcadores OFFICE:START / OFFICE:END"
    return blocks


def test_office_blocks_do_not_contain_jinja_sequences_or_triple_quotes():
    for block in _office_blocks():
        assert "{{" not in block and "{%" not in block and "{#" not in block
        assert '"""' not in block


def test_home_page_renders_with_the_office_scene_as_default_tab(monkeypatch):
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    response = app_module.app.test_client().get("/", headers=_auth())
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    for agent in AGENT_IDS:
        assert f'id="office-agent-{agent}"' in html and f'id="office-bubble-{agent}"' in html
    assert 'id="office-panel"' in html
    assert '<div id="tab-oficina">' in html
    assert '<div id="tab-video" style="display:none">' in html
    assert 'class="nav-item active" data-tab="oficina"' in html


def test_office_script_is_wired_and_free_of_unsafe_sinks():
    import app as app_module
    script_blocks = [b for b in _office_blocks() if "officePoll" in b]
    assert script_blocks, "falta el JS de la oficina entre los marcadores"
    js = "\n".join(script_blocks)
    for name in ("officeStart", "officeStop", "officePoll", "officeRender", "officeRenderPanel", "officeRunAction"):
        assert f"function {name}" in js or f"async function {name}" in js
    assert "innerHTML" not in js  # todo el texto del servidor entra con textContent
    assert "\nofficeStart();" in app_module.HTML  # arranque al cargar (la oficina es la pestaña por defecto)
    assert "officeInFlight" in js and "officePrune" in js  # sin sondeos solapados; descartes podados
    assert "document.hidden" in js  # el polling se pausa con la pestaña oculta
