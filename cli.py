"""
CLI (Interfaz de Línea de Comandos) para el sistema TTS con Chatterbox.
Uso: python cli.py "Tu texto aquí" [opciones]
"""

import argparse
import sys
from tts_engine import (
    text_to_speech_long,
    play_audio,
    VOICE_LIBRARY,
    DEFAULT_VOICE,
    BEDTIME_PRESET,
    voice_is_ready,
)


def main():
    parser = argparse.ArgumentParser(
        description="🎙️ Texto a Voz con Chatterbox (voces clonadas por acento)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python cli.py "Hola mundo"
  python cli.py "Bienvenido" --voice es_mx_hombre
  python cli.py --file cuento.txt --bedtime --output cuento.wav
  python cli.py --file cuento.txt --voice es_ar_mujer --exaggeration 0.7
  python cli.py --list-voices
        """,
    )

    parser.add_argument(
        "text",
        nargs="?",
        help="Texto a convertir en voz (entre comillas)",
    )
    parser.add_argument(
        "--voice", "-v",
        default=DEFAULT_VOICE,
        help=f"Voz a usar (ver --list-voices). Default: {DEFAULT_VOICE}",
    )
    parser.add_argument(
        "--lang",
        default=None,
        help="Idioma de generación, ej. 'es', 'en'. Si no se especifica, se detecta según la voz elegida",
    )
    parser.add_argument(
        "--exaggeration",
        type=float,
        default=0.5,
        help="Intensidad emocional/expresividad (0.0-1.0, default 0.5)",
    )
    parser.add_argument(
        "--cfg-weight",
        type=float,
        default=0.8,
        help="Qué tan de cerca sigue el timbre/ritmo de la voz de referencia (0.0-1.0, default 0.8)",
    )
    parser.add_argument(
        "--bedtime",
        action="store_true",
        help="Modo cuento para dormir: entrega calmada y contenida (baja la expresividad automáticamente)",
    )
    parser.add_argument(
        "--voice-sample",
        help="Ruta a un WAV/MP3 de referencia (5-20s) para clonar una voz propia en vez de la biblioteca",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Desactiva la verificación por Whisper del audio generado — más rápido, más riesgo de cortes",
    )
    parser.add_argument(
        "--max-word-mismatches",
        type=int,
        default=2,
        help="Palabras de tolerancia entre el texto pedido y la transcripción antes de reintentar (default 2)",
    )
    parser.add_argument(
        "--output", "-o",
        help="Ruta del archivo de salida (.wav)",
    )
    parser.add_argument(
        "--file", "-f",
        help="Leer texto desde un archivo .txt",
    )
    parser.add_argument(
        "--play", "-p",
        action="store_true",
        help="Reproducir el audio generado automáticamente",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="Listar todas las voces disponibles",
    )

    args = parser.parse_args()

    # ── Listar voces ──
    if args.list_voices:
        print("\n🎙️  Voces disponibles (Chatterbox):\n")
        for key, info in VOICE_LIBRARY.items():
            estado = "✓ Lista" if voice_is_ready(key) else "↓ Se genera la primera vez que se use"
            marca = " 💤 suave, ideal para cuentos" if info["gentle"] else ""
            print(f"  • {key}")
            print(f"    {info['label']}{marca}")
            print(f"    Estado: {estado}\n")
        return

    # ── Obtener texto ──
    text = None

    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                text = f.read().strip()
        except FileNotFoundError:
            print(f"[ERROR] Archivo no encontrado: {args.file}")
            sys.exit(1)
    elif args.text:
        text = args.text
    else:
        # Leer desde stdin si hay pipe
        if not sys.stdin.isatty():
            text = sys.stdin.read().strip()
        else:
            parser.print_help()
            sys.exit(0)

    if not text:
        print("[ERROR] No se proporcionó texto.")
        sys.exit(1)

    # ── Generar audio ──
    kwargs = {
        "language": args.lang,
        "exaggeration": args.exaggeration,
        "cfg_weight": args.cfg_weight,
        "verify_audio": not args.no_verify,
        "max_word_mismatches": args.max_word_mismatches,
    }
    if args.voice_sample:
        kwargs["audio_prompt_path"] = args.voice_sample
    else:
        kwargs["voice"] = args.voice

    if args.bedtime:
        kwargs["exaggeration"] = BEDTIME_PRESET["exaggeration"]
        kwargs["cfg_weight"] = BEDTIME_PRESET["cfg_weight"]
        print("💤 Modo cuento para dormir activado: entrega calmada y contenida.\n")

    output = text_to_speech_long(
        text,
        output_path=args.output,
        **kwargs,
    )

    if output:
        if args.play:
            play_audio(output)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
