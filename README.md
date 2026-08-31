# 🎙️ Historias para Dormir — TTS + Video automático

Sistema para generar narraciones de historias para dormir con voz muy
natural (**Chatterbox**, open-source) en distintos acentos del español, y
armar automáticamente un video (imágenes + subtítulos + efectos) con
**Remotion**.

---

## ✨ Voz: Chatterbox con biblioteca de acentos

Chatterbox es un modelo de clonación de voz "zero-shot": no tiene voces
con nombre, genera cualquier voz a partir de un audio de referencia corto.
Es autoregresivo, lo que le da una prosodia mucho más natural y expresiva
que los motores no-autoregresivos — a cambio de poder perder alguna palabra
ocasionalmente en textos largos. Por eso cada audio generado se verifica
automáticamente (transcripción con Whisper comparada contra el texto
pedido) y se reintenta hasta 2 veces si algo no calza. Este proyecto trae
una **biblioteca de 8 voces** (una por acento/género) ya armada — la
primera vez que usas cada una, se genera automáticamente su clip de
referencia (tarda unos segundos, requiere internet solo esa vez).

| Voz | Acento | Suave 💤 |
|---|---|---|
| `es_es_mujer` | España — Mujer | ✓ |
| `es_es_hombre` | España — Hombre | |
| `es_mx_mujer` | México — Mujer | ✓ |
| `es_mx_hombre` | México — Hombre | |
| `es_ar_mujer` | Argentina — Mujer | ✓ |
| `es_ar_hombre` | Argentina — Hombre | |
| `es_co_mujer` | Colombia — Mujer | ✓ |
| `es_us_mujer` | Latinoamérica neutro — Mujer | ✓ |

Las marcadas 💤 tienen un timbre más suave/cálido, recomendadas para
cuentos. También puedes clonar tu propia voz con un audio de 5-20s
(`--voice-sample` en el CLI).

---

## 📦 Instalación en Windows

### 1. Python y entorno virtual

```cmd
cd tts-proyecto
python -m venv venv
venv\Scripts\activate
```

### 2. Dependencias

```cmd
pip install -r requirements.txt
```

Esto instala Chatterbox (descarga sus pesos, ~2GB, la primera vez que
generas audio), Edge TTS (usado solo para generar los clips de referencia
de voz), y faster-whisper/mutagen (subtítulos del video, y verificación de
que Chatterbox no perdió palabras al generar).

### 3. (Opcional) Preparar todas las voces de una vez

```cmd
python setup_voices.py
```

Si no lo corres, cada voz se prepara sola la primera vez que la uses.

### 4. (Opcional) Generación de video

Ver `video/README.md` para instalar Node.js + Remotion.

---

## 🚀 Uso

### Interfaz web (recomendada)

```cmd
python app.py
```

Abre `http://localhost:5000` — pestaña **🔊 Audio** para narrar, pestaña
**🎬 Video** para armar el video automáticamente.

### Línea de comandos

```cmd
:: Uso básico (voz por defecto: es_es_mujer)
python cli.py "Había una vez un viejo faro..."

:: Elegir voz
python cli.py "Bienvenido" --voice es_mx_hombre

:: Modo cuento para dormir (entrega calmada y contenida)
python cli.py --file cuento.txt --bedtime --output cuento.wav

:: Ajustar expresividad/fidelidad a mano
python cli.py --file cuento.txt --voice es_ar_mujer --exaggeration 0.7 --cfg-weight 0.4

:: Clonar tu propia voz desde un audio de referencia
python cli.py "Texto" --voice-sample mi_voz.wav

:: Ver todas las voces y su estado
python cli.py --list-voices
```

### Python directo

```python
from tts_engine import text_to_speech_long

audio = text_to_speech_long(
    "Había una vez un viejo faro junto al mar...",
    voice="es_es_mujer",
    exaggeration=0.3,  # más bajo = más calmado/contenido
)
```

---

## 📁 Estructura del proyecto

```
tts-proyecto/
├── tts_engine.py       # Motor: Chatterbox + biblioteca de voces
├── app.py              # Servidor web (audio + video)
├── cli.py               # Interfaz de línea de comandos
├── video_maker.py      # Orquestador de generación de video
├── setup_voices.py     # Prepara todas las voces de una vez (opcional)
├── requirements.txt
├── voices/             # Clips de referencia de voz (se generan solos)
├── output/             # Audios generados (.wav)
└── video/              # Proyecto Remotion — ver video/README.md
```

---

## 🛠️ Solución de problemas

**"Chatterbox no instalado"** → revisa que `pip install -r requirements.txt`
terminó sin errores (paso 2 de instalación).

**"Para generar la voz de referencia... instala edge-tts"** → `pip install edge-tts`
(solo se usa para crear los clips de referencia, no para la voz final).

**Lento generando audio / reintentos frecuentes** → Chatterbox es
autoregresivo y se beneficia mucho de GPU. Revisa que
`torch.cuda.is_available()` devuelva `True` si tienes GPU NVIDIA — en CPU
es notablemente más lento y más propenso a los reintentos por palabras
perdidas.

---

## 📄 Licencias

- **Chatterbox**: MIT ([Resemble AI](https://github.com/resemble-ai/chatterbox))
- **Edge TTS**: usa las voces de Microsoft Edge (solo para bootstrap de la biblioteca)
- **Remotion**: gratis para individuos y equipos de hasta 3 personas — ver `video/README.md`
- **Flask**: BSD-3-Clause
