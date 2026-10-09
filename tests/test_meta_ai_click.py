import pytest

import auto_pipeline

SNAP_1 = 'button "Meta AI" [ref=e7]'
SNAP_2 = 'button "Meta AI" [ref=e12]'


def _fake_browser(calls, clicks):
    snaps = iter([SNAP_1, SNAP_2, SNAP_2])

    def run(args, session, **kw):
        calls.append(args)
        if args[0] == "snapshot":
            return next(snaps)
        if args[0] == "click":
            clicks.append(args[1])
            if args[1] == "@e7":
                raise auto_pipeline.PipelineError("agent-browser click @e7 fallo: ✗ Unknown ref: e7")
        return ""

    return run


def test_open_meta_ai_retries_with_a_fresh_snapshot_on_unknown_ref(monkeypatch):
    calls, clicks = [], []
    monkeypatch.setattr(auto_pipeline, "_run_agent_browser", _fake_browser(calls, clicks))
    monkeypatch.setattr(auto_pipeline.time, "sleep", lambda s: None)
    auto_pipeline._open_meta_ai(True)
    assert clicks == ["@e7", "@e12"]


def test_open_meta_ai_gives_up_after_three_unknown_refs(monkeypatch):
    def run(args, session, **kw):
        if args[0] == "snapshot":
            return SNAP_1
        if args[0] == "click":
            raise auto_pipeline.PipelineError("agent-browser click @e7 fallo: ✗ Unknown ref: e7")
        return ""

    monkeypatch.setattr(auto_pipeline, "_run_agent_browser", run)
    monkeypatch.setattr(auto_pipeline.time, "sleep", lambda s: None)
    with pytest.raises(auto_pipeline.PipelineError, match="Unknown ref"):
        auto_pipeline._open_meta_ai(True)
