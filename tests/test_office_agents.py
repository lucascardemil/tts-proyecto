# -*- coding: utf-8 -*-
"""Motor de acciones de la oficina: reglas, ciclo, propuestas y bitacora. Todo sobre datos falsos y un almacen en memoria."""
import copy
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import office_agents
import office_state

NOW = datetime(2026, 10, 15, 15, 0)


@pytest.fixture(autouse=True)
def memory_store(monkeypatch):
    saved = {}
    monkeypatch.setattr(office_agents.job_store, "load", lambda name: copy.deepcopy(saved.get(name, {})))
    monkeypatch.setattr(office_agents.job_store, "save", lambda name, d, **kw: saved.__setitem__(name, copy.deepcopy(d)))
    office_state.reset()
    return saved


def vid(status, error=None, error_at=None, **extra):
    return {"status": status, "scheduled_at": None, "error": error,
            "error_at": error_at.isoformat() if error_at else None, **extra}


def proj(videos=(), page="MACRAME CREATIVO", pid="p1"):
    return {"id": pid, "name": "Lote 1", "page_name": page, "status": "running",
            "videos": [dict(index=i, **v) for i, v in enumerate(videos)]}


def data(**kw):
    base = {"projects": [], "flow": {"state": "ok", "message": ""}, "text_backends": ["freellm:x"],
            "tokens": [], "remote_times": {}, "metrics_rows": {"facebook": [], "instagram": []},
            "seen": {}, "errors": {}}
    base.update(kw)
    return base


def snapshot(findings=(), agents=None, ok=True):
    agents = agents or {"videos": ("idle", "Sin trabajo")}
    return {"ok": ok, "agents": {k: {"status": s, "bubble": b} for k, (s, b) in agents.items()},
            "findings": [{"id": i, "agent": a, "severity": sev, "text": t, "action": None}
                         for i, a, sev, t in findings]}


# ---- estado persistido ----------------------------------------------------------------------

def test_a_missing_or_malformed_store_falls_back_to_defaults(memory_store):
    assert office_agents._load_state() == office_agents._default_state()
    memory_store["office_agents"] = {"paused": "si", "log": 5, "modes": {"republish": "off"}}
    state = office_agents._load_state()
    assert state["paused"] is False and state["log"] == []
    assert state["modes"] == {"republish": "off"}


def test_state_round_trips_through_the_store():
    state = office_agents._default_state()
    state["paused"] = True
    office_agents._save_state(state)
    assert office_agents._load_state()["paused"] is True


# ---- eventos pasivos (bitacora) ---------------------------------------------------------------

def test_first_snapshot_logs_one_status_per_agent_and_each_new_alert():
    state = office_agents._default_state()
    snap = snapshot([("token-invalid:X", "revisor", "error", "Token de Meta inválido en X")],
                    {"videos": ("idle", "Sin trabajo"), "revisor": ("error", "1 alerta")})
    office_agents.record_events(state, snap, NOW)
    kinds = [(e["kind"], e["agent"]) for e in state["log"]]
    assert ("alert_open", "revisor") in kinds
    assert ("status", "videos") in kinds and ("status", "revisor") in kinds
    status = next(e for e in state["log"] if e["kind"] == "status" and e["agent"] == "videos")
    assert status["message"] == "— → idle" and status["label"] == "Sin trabajo"
    opened = next(e for e in state["log"] if e["kind"] == "alert_open")
    assert opened["key"] == "token-invalid:X" and opened["message"] == "error" and opened["ts"] == "2026-10-15T15:00:00"


def test_an_unchanged_snapshot_adds_nothing():
    state = office_agents._default_state()
    snap = snapshot([("gap:x", "revisor", "warn", "Hoy sin posts en X")])
    office_agents.record_events(state, snap, NOW)
    before = len(state["log"])
    office_agents.record_events(state, snap, NOW + timedelta(minutes=1))
    assert len(state["log"]) == before


def test_an_alert_that_disappears_is_logged_as_resolved_once():
    state = office_agents._default_state()
    office_agents.record_events(state, snapshot([("gap:x", "revisor", "warn", "Hoy sin posts en X")]), NOW)
    office_agents.record_events(state, snapshot(), NOW + timedelta(minutes=1))
    resolved = [e for e in state["log"] if e["kind"] == "alert_resolved"]
    assert len(resolved) == 1 and resolved[0]["label"] == "Hoy sin posts en X" and resolved[0]["key"] == "gap:x"
    office_agents.record_events(state, snapshot(), NOW + timedelta(minutes=2))
    assert len([e for e in state["log"] if e["kind"] == "alert_resolved"]) == 1


def test_status_change_is_logged_with_from_and_to():
    state = office_agents._default_state()
    office_agents.record_events(state, snapshot(agents={"videos": ("idle", "Sin trabajo")}), NOW)
    office_agents.record_events(state, snapshot(agents={"videos": ("busy", "Render 1/3")}), NOW + timedelta(minutes=1))
    last = state["log"][-1]
    assert (last["kind"], last["message"], last["label"]) == ("status", "idle → busy", "Render 1/3")


def test_alerts_are_not_resolved_while_a_source_could_not_be_read():
    state = office_agents._default_state()
    office_agents.record_events(state, snapshot([("gap:x", "revisor", "warn", "Hoy sin posts en X")]), NOW)
    degraded = snapshot([("read:projects", "revisor", "warn", "No pude leer los lotes: boom")])
    office_agents.record_events(state, degraded, NOW + timedelta(minutes=1))
    assert not [e for e in state["log"] if e["kind"] == "alert_resolved"]
    office_agents.record_events(state, snapshot(), NOW + timedelta(minutes=2))
    assert {e["key"] for e in state["log"] if e["kind"] == "alert_resolved"} == {"gap:x", "read:projects"}


def test_a_failed_state_is_ignored_and_the_log_is_capped():
    state = office_agents._default_state()
    office_agents.record_events(state, {"ok": False, "error": "boom"}, NOW)
    assert state["log"] == [] and state["open_findings"] == {}
    for i in range(office_agents.LOG_MAX + 25):
        office_agents._log(state, NOW, kind="status", agent="videos", key="videos", label=str(i), message="")
    assert len(state["log"]) == office_agents.LOG_MAX and state["log"][-1]["label"] == str(office_agents.LOG_MAX + 24)


# ---- reglas -----------------------------------------------------------------------------------

OLD = NOW - timedelta(hours=3)


def keys(found):
    return [t.key for _, t in found]


def test_cause_resolved_flow_error_when_flow_is_back_and_old_enough():
    p = proj([vid("error", "Flow no devolvió imagen en 240s.", OLD, heal_cycles=office_agents.batch_pipeline.MAX_HEAL_CYCLES)])
    (found,) = office_agents._scan(data(projects=[p]), NOW)
    rule, target = found
    assert rule.id == "retry_cause_resolved" and target.key == "retry_cause_resolved:p1:0"
    assert "ítem 1" in target.label
    assert target.payload == {"project_id": "p1", "index": 0, "status": "error"}


def test_cause_resolved_waits_for_the_cause_and_for_two_hours():
    p_new = proj([vid("error", "Flow falló", NOW - timedelta(minutes=30))])
    p_ok = proj([vid("error", "Flow falló", OLD)])
    down = {"state": "no_browser", "message": "cerrado"}
    assert office_agents._scan(data(projects=[p_new]), NOW) == []
    assert office_agents._scan(data(projects=[p_ok], flow=down), NOW) == []
    assert office_agents._scan(data(projects=[p_ok], errors={"flow": "x"}, flow=None), NOW) == []


def test_cause_resolved_text_provider_error_needs_a_backend_and_unrelated_errors_are_ignored():
    p = proj([vid("error", "Sin proveedor de texto disponible", OLD), vid("error", "ffmpeg murió", OLD)])
    assert keys(office_agents._scan(data(projects=[p]), NOW)) == ["retry_cause_resolved:p1:0"]
    assert office_agents._scan(data(projects=[p], text_backends=[]), NOW) == []
    assert office_agents._scan(data(projects=[p], text_backends=None), NOW) == []


def test_cause_resolved_error_without_error_at_is_eligible_immediately():
    p = proj([vid("error", "Flow falló", None)])
    assert keys(office_agents._scan(data(projects=[p]), NOW)) == ["retry_cause_resolved:p1:0"]


def test_cause_resolved_leaves_healable_errors_to_the_batch_healer_until_it_gives_up():
    bp = office_agents.batch_pipeline
    healable = "Flow no devolvió imagen en 240s."
    assert bp._is_healable_error(healable)
    p = proj([vid("error", healable, OLD, heal_cycles=0)])
    assert office_agents._scan(data(projects=[p]), NOW) == []
    p = proj([vid("error", healable, OLD, heal_cycles=bp.MAX_HEAL_CYCLES)])
    assert keys(office_agents._scan(data(projects=[p]), NOW)) == ["retry_cause_resolved:p1:0"]


def test_cause_resolved_only_looks_at_running_projects():
    p = proj([vid("error", "Flow falló", OLD)])
    p["status"] = "paused"
    assert office_agents._scan(data(projects=[p]), NOW) == []


def test_cause_resolved_matches_flow_as_a_whole_word_only():
    p = proj([vid("error", "workflow broke", OLD), vid("error", "overflow en el buffer", OLD),
              vid("error", "Falló Flow.", OLD)])
    assert keys(office_agents._scan(data(projects=[p]), NOW)) == ["retry_cause_resolved:p1:2"]


def test_republish_needs_a_valid_token_for_that_page_and_no_auth_error():
    p = proj([vid("publish_error"), vid("publish_error", last_publish_auth_error=True)])
    ok = [{"page_name": "macrame creativo", "ok": True}]
    assert keys(office_agents._scan(data(projects=[p], tokens=ok), NOW)) == ["republish:p1:0"]
    bad = [{"page_name": "MACRAME CREATIVO", "ok": False, "auth_error": True}]
    assert office_agents._scan(data(projects=[p], tokens=bad), NOW) == []
    other = [{"page_name": "OTRA PAGINA", "ok": True}]
    assert office_agents._scan(data(projects=[p], tokens=other), NOW) == []
    assert office_agents._scan(data(projects=[p], tokens=[]), NOW) == []


def test_release_stuck_uses_the_stuck_threshold_and_remembers_the_expected_status():
    p = proj([vid("generating"), vid("publishing")])
    seen = {office_state.stuck_key("p1", 0, "generating"): NOW - timedelta(minutes=50),
            office_state.stuck_key("p1", 1, "publishing"): NOW - timedelta(minutes=10)}
    (found,) = office_agents._scan(data(projects=[p], seen=seen), NOW)
    assert found[1].key == "release_stuck:p1:0" and found[1].payload["status"] == "generating"


def test_all_rules_skip_when_batches_could_not_be_read():
    p = proj([vid("error", "Flow falló", OLD)])
    assert office_agents._scan(data(projects=[p], errors={"projects": "boom"}), NOW) == []


def test_a_rule_that_raises_is_skipped_and_the_others_still_run(monkeypatch):
    def boom(d, n):
        raise RuntimeError("regla rota")
    broken = office_agents.Rule("broken", "videos", "x", "x", "auto", 1, timedelta(0), boom, lambda t: (True, ""))
    monkeypatch.setattr(office_agents, "RULES", (broken,) + office_agents.RULES)
    p = proj([vid("error", "Flow falló", OLD)])
    assert keys(office_agents._scan(data(projects=[p]), NOW)) == ["retry_cause_resolved:p1:0"]


def test_executors_call_the_existing_batch_functions(monkeypatch):
    calls = []
    monkeypatch.setattr(office_agents.batch_pipeline, "retry_video", lambda pid, i: calls.append(("retry", pid, i)) or True)
    monkeypatch.setattr(office_agents.batch_pipeline, "retry_publish_video",
                        lambda pid, i: calls.append(("republish", pid, i)) or False)
    monkeypatch.setattr(office_agents.batch_pipeline, "release_stuck_item",
                        lambda pid, i, s: calls.append(("release", pid, i, s)) or True)
    t = office_agents.Target("k", "l", {"project_id": "p1", "index": 2, "status": "generating"})
    assert office_agents.RULES_BY_ID["retry_cause_resolved"].execute(t)[0] is True
    ok, message = office_agents.RULES_BY_ID["republish"].execute(t)
    assert ok is False and "ya no" in message.lower()
    assert office_agents.RULES_BY_ID["release_stuck"].execute(t)[0] is True
    assert calls == [("retry", "p1", 2), ("republish", "p1", 2), ("release", "p1", 2, "generating")]


def test_default_modes_and_limits_follow_the_spec():
    r = office_agents.RULES_BY_ID
    assert (r["retry_cause_resolved"].default_mode, r["retry_cause_resolved"].max_attempts,
            r["retry_cause_resolved"].cooldown) == ("auto", 2, timedelta(hours=2))
    assert (r["republish"].default_mode, r["republish"].max_attempts, r["republish"].cooldown) == ("auto", 2, timedelta(hours=1))
    assert (r["release_stuck"].default_mode, r["release_stuck"].max_attempts, r["release_stuck"].cooldown) == ("propose", 3, timedelta(hours=1))


# ---- evaluate / apply -------------------------------------------------------------------------

def flow_error_data():
    return data(projects=[proj([vid("error", "Flow falló", OLD)])])


def run_once(state, d, now=NOW, saves=None):
    found = office_agents._scan(d, now)
    office_agents.expire(state, found, now)
    todo = office_agents.evaluate(state, found, now)
    office_agents.apply(state, todo, now, (lambda s: saves.append(copy.deepcopy(s))) if saves is not None else (lambda s: None))
    return todo


@pytest.fixture
def batch_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(office_agents.batch_pipeline, "retry_video", lambda pid, i: calls.append(("retry", pid, i)) or True)
    monkeypatch.setattr(office_agents.batch_pipeline, "retry_publish_video", lambda pid, i: calls.append(("republish", pid, i)) or True)
    monkeypatch.setattr(office_agents.batch_pipeline, "release_stuck_item", lambda pid, i, s: calls.append(("release", pid, i, s)) or True)
    return calls


def test_auto_rule_executes_and_saves_the_attempt_before_running(batch_calls):
    state, saves = office_agents._default_state(), []
    run_once(state, flow_error_data(), saves=saves)
    assert batch_calls == [("retry", "p1", 0)]
    assert saves[0]["attempts"]["retry_cause_resolved:p1:0"]["count"] == 1 and saves[0]["log"] == []
    entry = state["log"][-1]
    assert (entry["kind"], entry["mode"], entry["ok"], entry["rule"]) == ("action", "auto", True, "retry_cause_resolved")
    assert entry["message"] == "Reintentado" and "ítem 1" in entry["label"]


def test_off_mode_does_nothing_and_propose_mode_creates_one_proposal(batch_calls):
    state = office_agents._default_state()
    state["modes"]["retry_cause_resolved"] = "off"
    assert run_once(state, flow_error_data()) == [] and batch_calls == []
    state["modes"]["retry_cause_resolved"] = "propose"
    run_once(state, flow_error_data())
    run_once(state, flow_error_data(), NOW + timedelta(minutes=1))
    (proposal,) = state["proposals"]
    assert proposal["rule"] == "retry_cause_resolved" and proposal["key"] == "retry_cause_resolved:p1:0"
    assert proposal["expires_at"] == "2026-10-16T15:00:00" and len(proposal["id"]) == 8
    assert batch_calls == []


def test_release_stuck_defaults_to_a_proposal():
    state = office_agents._default_state()
    p = proj([vid("generating")])
    seen = {office_state.stuck_key("p1", 0, "generating"): NOW - timedelta(minutes=50)}
    run_once(state, data(projects=[p], seen=seen))
    assert [x["rule"] for x in state["proposals"]] == ["release_stuck"]


def test_pause_stops_execution_proposals_and_exhaustion_notes(batch_calls):
    state = office_agents._default_state()
    state["paused"] = True
    state["modes"]["release_stuck"] = "propose"
    assert run_once(state, flow_error_data()) == [] and batch_calls == [] and state["proposals"] == []


def test_cooldown_and_attempt_cap(batch_calls):
    state = office_agents._default_state()
    run_once(state, flow_error_data(), NOW)
    run_once(state, flow_error_data(), NOW + timedelta(minutes=30))      # dentro del cooldown de 2 h
    assert len(batch_calls) == 1
    run_once(state, flow_error_data(), NOW + timedelta(hours=3))          # segundo y ultimo intento
    assert len(batch_calls) == 2
    run_once(state, flow_error_data(), NOW + timedelta(hours=6))          # agotado
    run_once(state, flow_error_data(), NOW + timedelta(hours=7))
    assert len(batch_calls) == 2
    exhausted = [e for e in state["log"] if e["kind"] == "exhausted"]
    assert len(exhausted) == 1 and exhausted[0]["key"] == "retry_cause_resolved:p1:0"


def test_hourly_brake_blocks_further_automatic_actions(batch_calls):
    state = office_agents._default_state()
    for i in range(office_agents.AUTO_ACTIONS_PER_HOUR):
        office_agents._log(state, NOW - timedelta(minutes=5), kind="action", mode="auto", ok=True, key=f"k{i}",
                           agent="videos", label="x", message="x", rule="retry_cause_resolved")
    assert office_agents._auto_used(state, NOW) == office_agents.AUTO_ACTIONS_PER_HOUR
    run_once(state, flow_error_data())
    assert batch_calls == []
    assert office_agents._auto_used(state, NOW + timedelta(hours=2)) == 0


def test_an_executor_that_raises_is_logged_as_failed_and_counts_as_an_attempt(monkeypatch):
    def boom(pid, i):
        raise RuntimeError("access_token=SECRET fallo")
    monkeypatch.setattr(office_agents.batch_pipeline, "retry_video", boom)
    state = office_agents._default_state()
    run_once(state, flow_error_data())
    entry = state["log"][-1]
    assert entry["ok"] is False and "SECRET" not in entry["message"] and "RuntimeError" in entry["message"]
    assert state["attempts"]["retry_cause_resolved:p1:0"]["count"] == 1


def test_expire_drops_old_proposals_vanished_targets_and_stale_attempts():
    state = office_agents._default_state()
    state["proposals"] = [
        {"id": "a", "rule": "retry_cause_resolved", "key": "retry_cause_resolved:p1:0", "label": "x",
         "created_at": "2026-10-15T14:00:00", "expires_at": "2026-10-16T14:00:00"},
        {"id": "b", "rule": "retry_cause_resolved", "key": "retry_cause_resolved:p1:9", "label": "x",
         "created_at": "2026-10-15T14:00:00", "expires_at": "2026-10-16T14:00:00"},
        {"id": "c", "rule": "retry_cause_resolved", "key": "retry_cause_resolved:p1:0", "label": "x",
         "created_at": "2026-10-14T00:00:00", "expires_at": "2026-10-15T00:00:00"},
    ]
    state["attempts"] = {"old": {"count": 1, "last_at": "2026-10-01T00:00:00"},
                         "recent": {"count": 1, "last_at": "2026-10-14T00:00:00"}}
    office_agents.expire(state, office_agents._scan(flow_error_data(), NOW), NOW)
    assert [p["id"] for p in state["proposals"]] == ["a"]
    assert list(state["attempts"]) == ["recent"]


# ---- ciclo ------------------------------------------------------------------------------------

def bootstrapped_state():
    state = office_agents._default_state()
    state["bootstrapped"] = True
    office_agents._save_state(state)


def test_run_cycle_records_events_runs_rules_and_persists(batch_calls, memory_store):
    bootstrapped_state()
    readers = {"projects": lambda: [proj([vid("error", "Flow falló", OLD)])],
               "flow": lambda: {"state": "ok", "message": ""}, "text_backends": lambda: ["freellm:x"]}
    office_agents.run_cycle(readers, NOW)
    assert batch_calls == [("retry", "p1", 0)]
    saved = memory_store["office_agents"]
    kinds = {e["kind"] for e in saved["log"]}
    assert {"action", "status", "alert_open"} <= kinds
    assert saved["attempts"]["retry_cause_resolved:p1:0"]["count"] == 1


def test_run_cycle_does_not_act_when_the_batches_could_not_be_read(batch_calls, monkeypatch):
    def broken():
        raise RuntimeError("sin disco")
    office_agents.run_cycle({"projects": broken}, NOW)
    assert batch_calls == []
    assert any(e["kind"] == "alert_open" and e["key"] == "read:projects" for e in office_agents._load_state()["log"])


def test_run_cycle_feeds_the_brake_finding_to_the_reviewer(batch_calls):
    state = office_agents._default_state()
    for i in range(office_agents.AUTO_ACTIONS_PER_HOUR):
        office_agents._log(state, NOW - timedelta(minutes=5), kind="action", mode="auto", ok=True, key=f"k{i}",
                           agent="videos", label="x", message="x", rule="retry_cause_resolved")
    office_agents._save_state(state)
    office_agents.run_cycle({"projects": lambda: []}, NOW)
    assert any(e["kind"] == "alert_open" and e["key"] == "automation-brake" for e in office_agents._load_state()["log"])
    assert office_agents.brake(NOW) == {"used": office_agents.AUTO_ACTIONS_PER_HOUR, "limit": office_agents.AUTO_ACTIONS_PER_HOUR}


def test_run_forever_survives_a_failing_cycle_and_stops_on_the_event(monkeypatch):
    stop, calls = threading.Event(), []

    def cycle(readers, now=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        stop.set()
    monkeypatch.setattr(office_agents, "run_cycle", cycle)
    office_agents.run_forever(lambda: {}, stop, interval=0)
    assert len(calls) == 2


# ---- acciones del usuario ----------------------------------------------------------------------

def stuck_data():
    p = proj([vid("generating")])
    return data(projects=[p], seen={office_state.stuck_key("p1", 0, "generating"): NOW - timedelta(minutes=50)})


def stuck_readers():
    return {"projects": lambda: [proj([vid("generating")])]}


def make_proposal():
    state = office_agents._default_state()
    run_once(state, stuck_data())
    office_agents._save_state(state)
    return state["proposals"][0]["id"]


def test_pause_and_mode_are_validated_and_persisted():
    assert office_agents.set_paused(True) == {"ok": True} and office_agents._load_state()["paused"] is True
    assert office_agents.set_mode("republish", "off") == {"ok": True}
    assert office_agents._load_state()["modes"] == {"republish": "off"}
    assert office_agents.set_mode("nope", "off")["ok"] is False
    assert office_agents.set_mode("republish", "turbo")["ok"] is False
    assert office_agents._load_state()["modes"] == {"republish": "off"}


def test_reject_removes_the_proposal_and_silences_it_for_24_hours():
    pid = make_proposal()
    assert office_agents.reject(pid, NOW) == {"ok": True}
    state = office_agents._load_state()
    assert state["proposals"] == []
    run_once(state, stuck_data(), NOW + timedelta(hours=2))
    assert state["proposals"] == []
    run_once(state, stuck_data(), NOW + timedelta(hours=25))
    assert len(state["proposals"]) == 1
    assert office_agents.reject("zzzz", NOW)["ok"] is False


def test_approve_revalidates_executes_logs_as_approved_and_ignores_the_cooldown(batch_calls):
    pid = make_proposal()
    state = office_agents._load_state()
    state["attempts"]["release_stuck:p1:0"] = {"count": 1, "last_at": office_agents._iso(NOW)}  # cooldown vigente
    state["paused"] = True                                                                       # y pausa general
    office_agents._save_state(state)
    seen_data = office_state.collect_data(stuck_readers(), NOW)  # prepara 'seen'
    assert not seen_data["errors"]
    result = office_agents.approve(pid, stuck_readers(), NOW + timedelta(minutes=50))
    assert result["ok"] is True
    assert batch_calls == [("release", "p1", 0, "generating")]
    state = office_agents._load_state()
    assert state["proposals"] == [] and state["attempts"]["release_stuck:p1:0"]["count"] == 2
    entry = state["log"][-1]
    assert (entry["kind"], entry["mode"], entry["ok"]) == ("action", "approved", True)


def test_approve_refuses_when_the_target_is_gone_or_the_data_is_incomplete(batch_calls):
    pid = make_proposal()
    gone = {"projects": lambda: [proj([vid("ready")])]}
    result = office_agents.approve(pid, gone, NOW)
    assert result["ok"] is False and "ya no hace falta" in result["error"].lower() and batch_calls == []
    assert office_agents._load_state()["proposals"] == []
    pid = make_proposal()

    def broken():
        raise RuntimeError("sin disco")
    result = office_agents.approve(pid, {"projects": broken}, NOW)
    assert result["ok"] is False and batch_calls == []
    assert len(office_agents._load_state()["proposals"]) == 1  # sigue pendiente
    assert office_agents.approve("zzzz", gone, NOW)["ok"] is False


def test_automation_block_lists_rules_proposals_newest_log_first_and_the_brake():
    state = office_agents._default_state()
    state["modes"]["republish"] = "off"
    for i in range(25):
        office_agents._log(state, NOW, kind="status", agent="videos", key="videos", label=str(i), message="")
    office_agents._save_state(state)
    block = office_agents.automation_block(NOW)
    assert block["paused"] is False and block["proposals"] == []
    assert [r["id"] for r in block["rules"]] == ["retry_cause_resolved", "republish", "release_stuck"]
    republish = next(r for r in block["rules"] if r["id"] == "republish")
    assert republish["mode"] == "off" and republish["default_mode"] == "auto" and republish["agent"] == "publica"
    assert len(block["log"]) == office_agents.LOG_SHOWN and block["log"][0]["label"] == "24"
    assert block["brake"] == {"used": 0, "limit": office_agents.AUTO_ACTIONS_PER_HOUR}


def test_approve_twice_executes_once_and_reject_between_sections_is_respected(batch_calls):
    pid = make_proposal()
    office_state.collect_data(stuck_readers(), NOW)  # prepara 'seen'
    first = office_agents.approve(pid, stuck_readers(), NOW + timedelta(minutes=50))
    second = office_agents.approve(pid, stuck_readers(), NOW + timedelta(minutes=50))
    assert first["ok"] is True
    assert second["ok"] is False and "ya no existe" in second["error"].lower()
    assert batch_calls == [("release", "p1", 0, "generating")]

    pid = make_proposal()
    office_state.collect_data(stuck_readers(), NOW)  # primer avistamiento: el objetivo SI se halla a los 50 min

    def reject_while_reading():
        office_agents.reject(pid, NOW)  # el usuario rechaza entre las dos secciones del lock
        return [proj([vid("generating")])]
    result = office_agents.approve(pid, {"projects": reject_while_reading}, NOW + timedelta(minutes=50))
    assert result["ok"] is False and "ya no existe" in result["error"].lower()
    assert batch_calls == [("release", "p1", 0, "generating")]
    attempt = office_agents._load_state()["attempts"]["release_stuck:p1:0"]
    assert attempt["count"] == 0 and "silenced_until" in attempt


# ---- correcciones de la ronda final ------------------------------------------------------------

def test_unknown_mode_never_executes(batch_calls):
    state = office_agents._default_state()
    state["modes"]["retry_cause_resolved"] = "turbo"
    assert run_once(state, flow_error_data()) == [] and batch_calls == []


def test_load_state_keeps_only_valid_entries_and_the_cycle_survives(memory_store, batch_calls):
    good_log = {"ts": "2026-10-15T14:00:00", "kind": "status", "agent": "videos", "key": "videos",
                "label": "x", "message": ""}
    good_proposal = {"id": "a1", "rule": "release_stuck", "key": "release_stuck:p1:0", "label": "x",
                     "created_at": "2026-10-15T14:00:00", "expires_at": "2026-10-16T14:00:00"}
    memory_store["office_agents"] = {
        "modes": {"republish": "off", "ghost": "off", "release_stuck": "turbo", 5: "off"},
        "log": [good_log, "basura", 7, None],
        "proposals": [good_proposal, {"id": 3, "rule": "r", "key": "k"}, {"id": "b"}, "x"],
        "attempts": {"good": {"count": 1}, "bad": 5, "worse": "x"},
        "open_findings": {"f1": {"agent": "videos", "text": "t"}, "f2": "x"},
        "statuses": {"videos": "idle", "revisor": 5},
        "bootstrapped": "si",
    }
    state = office_agents._load_state()
    assert state["modes"] == {"republish": "off"}
    assert state["log"] == [good_log] and state["proposals"] == [good_proposal]
    assert state["attempts"] == {"good": {"count": 1}}
    assert state["open_findings"] == {"f1": {"agent": "videos", "text": "t"}}
    assert state["statuses"] == {"videos": "idle"} and state["bootstrapped"] is False
    office_agents.run_cycle({"projects": lambda: []}, NOW)
    assert office_agents.automation_block(NOW)["proposals"] == []


def test_agent_cycle_uses_longer_read_ttls_for_graph_sources(monkeypatch):
    captured = {}

    def fake_collect(readers, now=None, extra=None, ttl_overrides=None):
        captured["ttl"] = ttl_overrides
        return {"errors": {}}
    monkeypatch.setattr(office_agents.office_state, "collect_data", fake_collect)
    office_agents.run_cycle({}, NOW)
    assert captured["ttl"] == office_agents.AGENT_READ_TTL
    assert office_agents.AGENT_READ_TTL == {"remote_times": 900, "tokens": 900, "metrics_rows": 900}


def test_save_state_failure_inside_apply_propagates_and_skips_the_executor(batch_calls, monkeypatch):
    def failing(name, d, **kw):
        raise OSError("disco lleno")
    monkeypatch.setattr(office_agents.job_store, "save", failing)
    state = office_agents._default_state()
    todo = office_agents.evaluate(state, office_agents._scan(flow_error_data(), NOW), NOW)
    assert [v for v, _, _ in todo] == ["execute"]
    with pytest.raises(OSError):
        office_agents.apply(state, todo, NOW, office_agents._save_state)
    assert batch_calls == []


def test_save_state_asks_job_store_for_a_strict_write(monkeypatch):
    seen = {}
    monkeypatch.setattr(office_agents.job_store, "save", lambda name, d, **kw: seen.update(kw))
    office_agents._save_state(office_agents._default_state())
    assert seen == {"strict": True}


def flow_cycle_readers():
    return {"projects": lambda: [proj([vid("error", "Flow falló", OLD)])],
            "flow": lambda: {"state": "ok", "message": ""}, "text_backends": lambda: ["freellm:x"]}


def test_first_cycle_turns_the_backlog_into_proposals_and_says_so(batch_calls, memory_store):
    office_agents.run_cycle(flow_cycle_readers(), NOW)
    assert batch_calls == []
    state = office_agents._load_state()
    (proposal,) = state["proposals"]
    assert proposal["key"] == "retry_cause_resolved:p1:0" and state["bootstrapped"] is True
    infos = [e for e in state["log"] if e["kind"] == "info"]
    assert len(infos) == 1 and infos[0]["key"] == "bootstrap" and infos[0]["agent"] == "revisor"
    # el siguiente ciclo no duplica la propuesta ni vuelve a anunciar el arranque
    office_state.reset()
    office_agents.run_cycle(flow_cycle_readers(), NOW + timedelta(minutes=1))
    state = office_agents._load_state()
    assert len(state["proposals"]) == 1 and len([e for e in state["log"] if e["kind"] == "info"]) == 1
    assert batch_calls == []
    result = office_agents.approve(proposal["id"], flow_cycle_readers(), NOW + timedelta(minutes=2))
    assert result["ok"] is True and batch_calls == [("retry", "p1", 0)]


def test_first_cycle_with_unreadable_batches_stays_unbootstrapped(batch_calls):
    def broken():
        raise RuntimeError("sin disco")
    office_agents.run_cycle({"projects": broken}, NOW)
    state = office_agents._load_state()
    assert state["bootstrapped"] is False and not [e for e in state["log"] if e["kind"] == "info"]


def test_expire_drops_proposals_of_unknown_or_switched_off_rules():
    state = office_agents._default_state()
    state["modes"]["release_stuck"] = "off"
    base = {"label": "x", "created_at": "2026-10-15T14:00:00", "expires_at": "2026-10-16T14:00:00"}
    state["proposals"] = [
        {"id": "a", "rule": "retry_cause_resolved", "key": "retry_cause_resolved:p1:0", **base},
        {"id": "b", "rule": "release_stuck", "key": "release_stuck:p1:0", **base},
        {"id": "c", "rule": "borrada", "key": "borrada:p1:0", **base},
    ]
    found = office_agents._scan(flow_error_data(), NOW)
    live = found + [(office_agents.RULES_BY_ID["release_stuck"],
                     office_agents.Target("release_stuck:p1:0", "x", {}))]
    office_agents.expire(state, live, NOW)
    assert [p["id"] for p in state["proposals"]] == ["a"]


def test_paused_first_cycle_does_not_bootstrap_and_resuming_proposes_the_backlog(batch_calls):
    office_agents.set_paused(True)
    office_agents.run_cycle(flow_cycle_readers(), NOW)
    state = office_agents._load_state()
    assert state["bootstrapped"] is False and not [e for e in state["log"] if e["kind"] == "info"]
    assert state["proposals"] == [] and batch_calls == []
    assert any(e["kind"] == "status" for e in state["log"])  # los eventos pasivos se siguen anotando
    office_agents.set_paused(False)
    office_state.reset()
    office_agents.run_cycle(flow_cycle_readers(), NOW + timedelta(minutes=1))
    state = office_agents._load_state()
    assert batch_calls == [] and state["bootstrapped"] is True
    assert [p["key"] for p in state["proposals"]] == ["retry_cause_resolved:p1:0"]


def test_load_state_drops_open_findings_without_agent_and_text(memory_store):
    memory_store["office_agents"] = {"open_findings": {"f1": {}, "f2": {"agent": "videos"},
                                                       "f3": {"agent": "videos", "text": "t"}}}
    assert office_agents._load_state()["open_findings"] == {"f3": {"agent": "videos", "text": "t"}}
    memory_store["office_agents"] = {"open_findings": {"f1": {}}}
    office_agents.run_cycle({"projects": lambda: []}, NOW)
