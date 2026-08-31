# 🎬 Generador de video (Remotion)

Convierte imágenes + audio de narración en un video vertical (1080×1920,
formato Reels/TikTok/Shorts) con efecto Ken Burns, transiciones, viñeta
cinematográfica, luciérnagas animadas y subtítulos sincronizados palabra
por palabra.

## Instalación (una sola vez)

### 1. Node.js
Descarga e instala Node.js 18 o superior: https://nodejs.org

### 2. Dependencias de Remotion
Desde la carpeta `tts-proyecto/video/`:

```cmd
cd video
npm install
```

La primera vez que renderices, Remotion descarga Chromium Headless
(~170 MB) automáticamente — es normal que tarde un par de minutos.

### 3. Dependencias Python para subtítulos automáticos
Desde la carpeta raíz del proyecto (`tts-proyecto/`):

```cmd
pip install faster-whisper mutagen
```

`faster-whisper` transcribe el audio para generar los subtítulos
sincronizados; la primera vez que lo uses descarga el modelo `small`
(~500 MB).

## Uso

### Desde la interfaz web
```cmd
python app.py
```
Abre la pestaña **🎬 Video**: escribe el título, sube las imágenes en
orden, elige un audio ya generado en la pestaña de Audio (o sube uno
nuevo) y presiona **Generar Video**.

### Desde Python directo
```cmd
python video_maker.py "El viejo perro que esperaba junto al faro" ^
    imagen1.jpg imagen2.jpg imagen3.jpg ^
    --audio narracion.mp3 --output mi_video.mp4
```

### Desde Remotion Studio (para ajustar el diseño a mano)
```cmd
cd video
npm start
```
Abre un editor visual en el navegador donde puedes ver la composición
en vivo, ajustar tiempos, colores, tipografías, etc. antes de renderizar.

## Cómo arma el video

1. Reparte la duración total del audio entre las imágenes subidas
   (mínimo ~3.5s por imagen; si sobran imágenes para el tiempo
   disponible, se descartan las últimas).
2. A cada imagen le asigna un efecto Ken Burns distinto (zoom in, zoom
   out, paneo izquierda, paneo derecha) alternando para que no se repita.
3. Transcribe el audio con `faster-whisper` para obtener el texto y el
   tiempo exacto de cada palabra.
4. Llama a `npx remotion render` pasándole todo como un JSON de props.

## Personalizar el diseño

Todo el diseño visual vive en `video/src/`:

| Archivo | Qué controla |
|---|---|
| `StoryVideo.tsx` | Composición principal, orden de las capas |
| `KenBurnsImage.tsx` | Velocidad e intensidad del zoom/paneo |
| `TitleCard.tsx` | Tarjeta de título (tipografía, animación) |
| `Subtitles.tsx` | Estilo de los subtítulos (tamaño, colores, cuántas palabras por línea) |
| `Vignette.tsx` | Intensidad de la viñeta oscura |
| `Fireflies.tsx` | Cantidad/comportamiento de las partículas |

Después de editar, no hace falta reinstalar nada — solo vuelve a generar
el video.

## ⚠️ Licencia de Remotion

Remotion es gratis (incluso para uso comercial) para individuos y empresas
de **hasta 3 personas**. Si el proyecto crece a un equipo de 4 personas o
más, se necesita una licencia de empresa paga. El detalle completo:
https://www.remotion.dev/docs/license/faq

## Solución de problemas

**"No se encontró 'npx'"** → Node.js no está instalado o no está en el
PATH. Reinstala marcando la opción de agregar a PATH.

**El render tarda mucho / se cuelga** → Es normal en CPU sin GPU
dedicada; un video de ~30s puede tardar varios minutos la primera vez
(descarga de Chromium incluida). Los renders siguientes son más rápidos.

**Los subtítulos no coinciden bien con el audio** → `faster-whisper`
funciona mejor con audio claro (como el de nuestros motores TTS). Si
usas un audio con música de fondo o ruido, la sincronización puede fallar.

**Las imágenes se ven "recortadas" raro** → Sube imágenes en formato
vertical (9:16, ej. 1080×1920) para que se ajusten mejor al video. Con
imágenes horizontales, `object-fit: cover` recorta los costados.
