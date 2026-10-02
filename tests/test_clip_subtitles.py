# -*- coding: utf-8 -*-
"""Subtitulos de los clips de Medal: solo lo que se oye hablar. Whisper simulado."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).parent.parent))

import gaming_clip
import video_maker


def _w(text, start, prob=0.9):
    return NS(word=f" {text}", start=start, end=start + 0.3, probability=prob)


def _seg(words, no_speech=0.05, logprob=-0.3, compression=1.2, text=None):
    return NS(words=words, no_speech_prob=no_speech, avg_logprob=logprob, compression_ratio=compression,
              text=text or " ".join(w.word for w in words))


class _FakeModel:
    def __init__(self, segments, lang_prob=0.9):
        self.segments, self.lang_prob = segments, lang_prob

    def transcribe(self, path, **kw):
        self.kw = kw
        return iter(self.segments), NS(language_probability=self.lang_prob)


def _run(monkeypatch, segments, lang_prob=0.9):
    model = _FakeModel(segments, lang_prob)
    monkeypatch.setattr(video_maker, "_get_whisper_model", lambda: model)
    return gaming_clip.transcribe_speech(Path("clip.mp4")), model


def test_speech_words_are_returned_with_times(monkeypatch):
    words, model = _run(monkeypatch, [_seg([_w("no", 1.0), _w("way", 1.4), _w("bro", 1.8)])])
    assert [x["word"] for x in words] == ["no", "way", "bro"] and words[0]["start"] == 1.0
    assert model.kw["vad_filter"] is True  # el VAD descarta disparos/musica sin voz


def test_no_speech_means_no_subtitles(monkeypatch):
    assert _run(monkeypatch, [])[0] == []
    assert _run(monkeypatch, [_seg([_w("hi", 0.5)])])[0] == []  # menos de SPEECH_MIN_WORDS: ruido


def test_noise_segments_and_boilerplate_are_dropped(monkeypatch):
    words = [_w("a", 1), _w("b", 2), _w("c", 3), _w("d", 4)]
    assert _run(monkeypatch, [_seg(words, no_speech=0.9)])[0] == []
    assert _run(monkeypatch, [_seg(words, logprob=-1.8)])[0] == []
    assert _run(monkeypatch, [_seg(words, compression=3.5)])[0] == []
    assert _run(monkeypatch, [_seg(words, text="Subtítulos realizados por la comunidad de Amara.org")])[0] == []
    assert _run(monkeypatch, [_seg(words)], lang_prob=0.2)[0] == []


def test_low_confidence_words_are_filtered(monkeypatch):
    words, _ = _run(monkeypatch, [_seg([_w("yes", 1), _w("zzz", 1.3, prob=0.2), _w("lets", 1.6), _w("go", 1.9)])])
    assert [x["word"] for x in words] == ["yes", "lets", "go"]
