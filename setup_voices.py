"""
Genera de una sola vez todos los clips de referencia de la biblioteca de
voces (carpeta voices/). Opcional: si no lo corres, cada voz se genera
sola la primera vez que la uses (con el mismo costo, solo que repartido).

Uso:
    python setup_voices.py
"""

from tts_engine import VOICE_LIBRARY, ensure_all_voices, voice_is_ready

if __name__ == "__main__":
    print("=" * 55)
    print("   🎙️  CONFIGURANDO BIBLIOTECA DE VOCES")
    print("=" * 55)
    print(f"\nSe van a preparar {len(VOICE_LIBRARY)} voces (requiere internet):\n")
    for key, info in VOICE_LIBRARY.items():
        print(f"  • {key}: {info['label']}")

    print()
    ok = ensure_all_voices()

    print()
    if ok:
        print("✅ Todas las voces quedaron listas.")
    else:
        print("⚠️  Algunas voces no se pudieron generar — revisa los errores de arriba.")

    print("\nEstado final:")
    for key in VOICE_LIBRARY:
        estado = "✓" if voice_is_ready(key) else "✗"
        print(f"  {estado} {key}")
