# -*- coding: utf-8 -*-
"""job_store: el guardado estricto es atomico y no se traga los errores; el normal sigue siendo silencioso."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import job_store


@pytest.fixture(autouse=True)
def tmp_state_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(job_store, "_STATE_DIR", tmp_path)
    return tmp_path


def test_strict_save_round_trips_and_leaves_no_temp_file(tmp_path):
    job_store.save("demo", {"a": [1, 2], "b": "ñ"}, strict=True)
    assert job_store.load("demo") == {"a": [1, 2], "b": "ñ"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo.json"]


def test_strict_save_propagates_a_failed_replace_and_keeps_the_old_file(monkeypatch, tmp_path):
    job_store.save("demo", {"v": 1}, strict=True)

    def boom(src, dst):
        raise OSError("bloqueado")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        job_store.save("demo", {"v": 2}, strict=True)
    assert job_store.load("demo") == {"v": 1}


def test_default_save_still_swallows_errors(monkeypatch):
    def boom(src, dst):
        raise OSError("bloqueado")
    monkeypatch.setattr(os, "replace", boom)
    monkeypatch.setattr(Path, "write_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError("x")))
    job_store.save("demo", {"v": 1})  # no levanta
