# -*- coding: utf-8 -*-
"""Voces clonadas (voices/custom/): alta, lista, borrado, resolucion y motor.
Sin GPU ni red; el audio de prueba es un tono sintetico generado con wave."""
import io
import math
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import tts_engine


def _tone(path: Path, seconds: float, sr: int = 16000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(
            struct.pack("<h", int(9000 * math.sin(2 * math.pi * 220 * i / sr) * (0.6 + 0.4 * math.sin(i / 900))))
            for i in range(int(sr * seconds))
        ))
    return path


@pytest.fixture(autouse=True)
def custom_dir(tmp_path, monkeypatch):
    d = tmp_path / "custom"
    monkeypatch.setattr(tts_engine, "CUSTOM_VOICES_DIR", d)
    return d


def test_add_list_sample_delete(tmp_path):
    key = tts_engine.add_custom_voice(str(_tone(tmp_path / "a.wav", 7)), "Mamá Laura")
    assert key == "custom:mama_laura"
    voices = tts_engine.list_custom_voices()
    assert voices["mama_laura"]["label"] == "Mamá Laura"
    sample = tts_engine.custom_voice_sample(key)
    assert sample and sample.exists() and 5 <= tts_engine._wav_seconds(sample) <= 20.5
    assert tts_engine.delete_custom_voice("mama_laura")
    assert tts_engine.custom_voice_sample(key) is None and tts_engine.list_custom_voices() == {}


def test_long_sample_is_trimmed_and_duplicate_label_gets_new_slug(tmp_path):
    src = _tone(tmp_path / "long.wav", 30)
    k1 = tts_engine.add_custom_voice(str(src), "Voz")
    k2 = tts_engine.add_custom_voice(str(src), "Voz")
    assert k1 != k2
    assert tts_engine._wav_seconds(tts_engine.custom_voice_sample(k1)) <= tts_engine.CUSTOM_VOICE_MAX_SECONDS + 0.2


def test_too_short_or_invalid_audio_rejected(tmp_path):
    with pytest.raises(ValueError, match="muy corta"):
        tts_engine.add_custom_voice(str(_tone(tmp_path / "s.wav", 2)), "Corta")
    bad = tmp_path / "x.mp3"
    bad.write_bytes(b"no es audio")
    with pytest.raises(ValueError):
        tts_engine.add_custom_voice(str(bad), "Mala")
    with pytest.raises(ValueError, match="nombre"):
        tts_engine.add_custom_voice(str(_tone(tmp_path / "o.wav", 6)), "  ")
    assert tts_engine.list_custom_voices() == {}


def test_loose_file_in_folder_is_imported(custom_dir, tmp_path):
    custom_dir.mkdir(parents=True)
    _tone(custom_dir / "Tio Pedro.wav", 6)
    voices = tts_engine.list_custom_voices()
    assert "tio_pedro" in voices and voices["tio_pedro"]["label"] == "Tio Pedro"
    assert (custom_dir / "_originals" / "Tio Pedro.wav").exists()
    assert len(tts_engine.list_custom_voices()) == 1  # no se importa dos veces


def test_slug_traversal_is_not_resolved():
    assert tts_engine.custom_voice_sample("custom:../../etc/passwd") is None
    assert tts_engine.custom_voice_sample("es_es_mujer") is None
    assert not tts_engine.delete_custom_voice("../x")


def test_custom_voice_uses_omnivoice_with_sample(tmp_path, monkeypatch):
    key = tts_engine.add_custom_voice(str(_tone(tmp_path / "a.wav", 6)), "Ana")
    calls = {}

    def fake_omni(text, output_path=None, voice_sample_path=None, **k):
        calls["omni"] = voice_sample_path
        return "ok.wav"

    monkeypatch.setattr(tts_engine, "tts_omnivoice", fake_omni)
    monkeypatch.setattr(tts_engine, "text_to_speech", lambda *a, **k: pytest.fail("no debe usar Chatterbox"))
    assert tts_engine._text_to_speech_with_fallback("hola", voice=key, exaggeration=0.3) == "ok.wav"
    assert Path(calls["omni"]) == tts_engine.custom_voice_sample(key)


def test_omnivoice_failure_falls_back_to_chatterbox_with_same_sample(tmp_path, monkeypatch):
    key = tts_engine.add_custom_voice(str(_tone(tmp_path / "a.wav", 6)), "Ana")
    seen = {}
    monkeypatch.setattr(tts_engine, "tts_omnivoice", lambda *a, **k: None)
    monkeypatch.setattr(tts_engine, "unload_omnivoice", lambda: None)

    def fake_cb(text, output_path=None, **k):
        seen.update(k)
        return "cb.wav"

    monkeypatch.setattr(tts_engine, "tts_chatterbox", fake_cb)
    assert tts_engine._text_to_speech_with_fallback("hola", voice=key) == "cb.wav"
    assert Path(seen["audio_prompt_path"]) == tts_engine.custom_voice_sample(key)
    assert "voice" not in seen  # no se cae a la voz de biblioteca ni avisa "voz no reconocida"


def test_missing_custom_voice_raises_clear_error():
    with pytest.raises(ValueError, match="ya no existe"):
        tts_engine._text_to_speech_with_fallback("hola", voice="custom:borrada")


def test_four_second_sample_is_accepted(tmp_path):
    assert tts_engine.add_custom_voice(str(_tone(tmp_path / "c.wav", 4.6)), "Corta valida") == "custom:corta_valida"


def test_legacy_library_voice_is_replaced_by_default_custom_voice(tmp_path, monkeypatch):
    key = tts_engine.add_custom_voice(str(_tone(tmp_path / "a.wav", 6)), "Ana")
    assert tts_engine.get_default_voice() == key
    monkeypatch.delenv("DEFAULT_CUSTOM_VOICE", raising=False)
    seen = {}
    monkeypatch.setattr(tts_engine, "tts_omnivoice", lambda text, output_path=None, voice_sample_path=None, **k: seen.update(s=voice_sample_path) or "ok.wav")
    monkeypatch.setattr(tts_engine, "text_to_speech", lambda *a, **k: pytest.fail("no debe usar la voz de biblioteca"))
    assert tts_engine._text_to_speech_with_fallback("hola", voice="es_es_mujer") == "ok.wav"
    assert Path(seen["s"]) == tts_engine.custom_voice_sample(key)


def test_default_voice_is_library_when_no_custom_voices():
    assert tts_engine.get_default_voice() == tts_engine.DEFAULT_VOICE


def test_library_voice_still_uses_chatterbox(monkeypatch):
    monkeypatch.setattr(tts_engine, "tts_omnivoice", lambda *a, **k: pytest.fail("no debe usar OmniVoice"))
    seen = {}
    monkeypatch.setattr(tts_engine, "tts_chatterbox", lambda text, output_path=None, **k: seen.update(k) or "cb.wav")
    assert tts_engine._text_to_speech_with_fallback("hola", voice="es_es_mujer") == "cb.wav"
    assert seen["voice"] == "es_es_mujer"


def test_verified_unloads_omnivoice_at_the_end(monkeypatch, tmp_path):
    unloaded = []
    monkeypatch.setattr(tts_engine, "unload_omnivoice", lambda: unloaded.append(1))
    monkeypatch.setattr(tts_engine, "text_to_speech_long", lambda *a, **k: None)
    assert tts_engine.text_to_speech_verified("hola") == (None, None)
    assert unloaded == [1]


def _client(monkeypatch):
    """Cliente de Flask con las credenciales del dashboard (la app exige Basic Auth)."""
    import base64
    import app as app_module
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    client = app_module.app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = "Basic " + base64.b64encode(b"u:p").decode()
    return client


def test_api_voices_lists_custom_first_and_upload_endpoint(tmp_path, monkeypatch):
    client = _client(monkeypatch)
    wav = _tone(tmp_path / "up.wav", 6)
    res = client.post("/api/voices/custom", data={"label": "Doña Rosa", "file": (io.BytesIO(wav.read_bytes()), "up.wav")},
                      content_type="multipart/form-data")
    assert res.get_json() == {"ok": True, "voice": "custom:dona_rosa"}
    voices = client.get("/api/voices").get_json()
    assert list(voices) == ["custom:dona_rosa"] and voices["custom:dona_rosa"]["custom"] is True  # sin las de biblioteca
    assert client.get("/api/voices/custom/dona_rosa/sample").status_code == 200
    bad = client.post("/api/voices/custom", data={"label": "x", "file": (io.BytesIO(b"abc"), "x.txt")},
                      content_type="multipart/form-data")
    assert bad.status_code == 400
    assert client.delete("/api/voices/custom/dona_rosa").get_json()["ok"] is True
    after = client.get("/api/voices").get_json()
    assert "custom:dona_rosa" not in after and "es_es_mujer" in after  # sin clonadas vuelve la biblioteca de respaldo


def test_pipeline_start_rejects_missing_custom_voice(monkeypatch):
    client = _client(monkeypatch)
    res = client.post("/api/pipeline/start", json={"story_text": "x", "voice": "custom:no_existe"})
    assert res.status_code == 400 and "ya no existe" in res.get_json()["error"]
