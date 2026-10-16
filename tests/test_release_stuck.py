# -*- coding: utf-8 -*-
"""release_stuck_item: suelta un unico item trabado, bajo el lock del modulo y solo si sigue en el estado esperado."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline


@pytest.fixture
def store(monkeypatch):
    projects = {"p1": {"id": "p1", "videos": [
        {"index": 0, "status": "generating", "stage": "voz"},
        {"index": 1, "status": "publishing", "stage": None},
        {"index": 2, "status": "ready", "stage": None},
    ]}}
    monkeypatch.setattr(batch_pipeline, "_load", lambda: copy.deepcopy(projects))
    monkeypatch.setattr(batch_pipeline, "_save", lambda p: (projects.clear(), projects.update(copy.deepcopy(p))))
    return projects


def status_of(store, index):
    return next(v for v in store["p1"]["videos"] if v["index"] == index)


def test_generating_goes_back_to_pending_and_clears_the_stage(store):
    assert batch_pipeline.release_stuck_item("p1", 0, "generating") is True
    assert status_of(store, 0)["status"] == "pending" and status_of(store, 0)["stage"] is None


def test_publishing_goes_back_to_ready(store):
    assert batch_pipeline.release_stuck_item("p1", 1, "publishing") is True
    assert status_of(store, 1)["status"] == "ready"


def test_does_nothing_when_the_item_changed_state(store):
    assert batch_pipeline.release_stuck_item("p1", 2, "generating") is False
    assert status_of(store, 2)["status"] == "ready"


def test_unknown_project_item_or_status_is_refused(store):
    assert batch_pipeline.release_stuck_item("nope", 0, "generating") is False
    assert batch_pipeline.release_stuck_item("p1", 9, "generating") is False
    assert batch_pipeline.release_stuck_item("p1", 0, "error") is False
    assert status_of(store, 0)["status"] == "generating"


def test_holds_scheduler_lock_reflects_the_module_handle(monkeypatch):
    monkeypatch.setattr(batch_pipeline, "_scheduler_lock_file", None)
    assert batch_pipeline.holds_scheduler_lock() is False
    monkeypatch.setattr(batch_pipeline, "_scheduler_lock_file", object())
    assert batch_pipeline.holds_scheduler_lock() is True
