# -*- coding: utf-8 -*-
"""El lote reescribe el guion cuando su mejor gancho no llega a la meta, con tope, y siempre se queda con el mejor intento."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import batch_pipeline as bp
import hook_craft as hc

WEAK = "Hoy te voy a contar una historia increíble"
MEDIUM = "¿Por qué se tuercen los nudos de macramé y casi nadie lo explica?"
STRONG = "Tardé tres horas en este nudo de macramé y se deshizo en una semana: nadie te cuenta por qué."


def _reply(first_sentence, options=()):
    block = ("GANCHOS:\n" + "".join(f"{i}. {o}\n" for i, o in enumerate(options, 1))) if options else ""
    return (f"HOOK_TEXT: x\n{block}\nPERSONAJES:\n[PERRA]: golden dog\n\n"
            f"Guion\n{first_sentence} Segunda frase del guion con mas detalle.\n\n"
            f"Imagen 1\nFrase del guion: «{first_sentence}»\nPrompt: a dog on a bench\n")


def _run(replies, hooked=True):
    messages = []

    def generate(message):
        messages.append(message)
        return replies[min(len(messages), len(replies)) - 1]

    text, info = bp._generate_script_with_hook(generate, "dame un video", hooked)
    return text, info, messages


def test_a_strong_first_answer_is_not_retried():
    text, info, messages = _run([_reply(STRONG, [WEAK, STRONG])])
    assert len(messages) == 1 and info["hook_score"] >= hc.HOOK_TARGET and info["attempts"] == 1


def test_weak_hooks_are_rewritten_with_the_critique_until_a_strong_one_arrives():
    replies = [_reply(WEAK, [WEAK, "Un macramé muy bonito para tu casa"]), _reply(WEAK, [WEAK, "Un macramé bonito"]), _reply(STRONG, [STRONG, WEAK])]
    text, info, messages = _run(replies)
    assert len(messages) == 3 and info["attempts"] == 3 and info["hook_score"] >= hc.HOOK_TARGET
    assert "se rechazó" in messages[1] and "dame un video" in messages[1] and STRONG in text


def test_when_no_attempt_reaches_the_target_the_best_one_is_used_and_nothing_fails():
    replies = [_reply(WEAK, [WEAK, "Un macramé bonito"]), _reply(MEDIUM, [MEDIUM, WEAK]), _reply(WEAK, [WEAK, "Un macramé bonito"])]
    text, info, messages = _run(replies)
    assert len(messages) == 1 + hc.HOOK_RETRIES  # tope
    assert info["hook"] == MEDIUM and info["hook_score"] < hc.HOOK_TARGET and MEDIUM in text


def test_kinds_without_hook_scoring_generate_once():
    text, info, messages = _run([_reply(WEAK)], hooked=False)
    assert len(messages) == 1 and info is None
