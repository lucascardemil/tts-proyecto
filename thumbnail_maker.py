"""
Generación automática de miniaturas (thumbnails) para YouTube. El fondo es un
frame de la escena del video, recortado de forma inteligente (detección de
cara). Encima se superpone un titular corto estilo Anton (headline viral:
contorno grueso, sombra difuminada, tracking cerrado).
"""

import io
import re
import tempfile
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat

import auto_pipeline

THUMBNAIL_SIZE_HORIZONTAL = (1280, 720)
THUMBNAIL_SIZE_VERTICAL = (1080, 1920)
THUMBNAIL_SIZE = THUMBNAIL_SIZE_HORIZONTAL  # default para flujos sin escena de referencia (ej. subida manual)
MAX_BYTES = 2 * 1024 * 1024  # límite de YouTube para thumbnails
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}

FONTS_DIR = Path(__file__).parent / "fonts"
ANTON_FONT_PATH = FONTS_DIR / "Anton-Regular.ttf"

def _face_cascade():
    """Carga el detector de rostros de OpenCV una sola vez (perezoso)."""
    global _FACE_CASCADE
    if _FACE_CASCADE is None:
        import cv2

        _FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _FACE_CASCADE


def _detect_face_box(img: Image.Image):
    """Devuelve el bounding box (x, y, w, h) de la cara más grande, o None si no hay ninguna."""
    import cv2
    import numpy as np

    gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    faces = _face_cascade().detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
    if len(faces) == 0:
        return None
    return max(faces, key=lambda f: f[2] * f[3])  # la más grande


def _fit_with_blurred_pad(img: Image.Image, size: tuple) -> Image.Image:
    """
    Encaja `img` completa dentro de `size` SIN recortar nada (a diferencia de
    `_smart_crop`) -- la imagen de escena casi nunca viene ya en la
    proporción pedida, y recortarla a la fuerza corta patas/cuerpo del sujeto.
    Se escala la imagen entera para que quepa dentro del lienzo (letterbox) y
    el espacio sobrante se rellena con una versión de la misma imagen
    recortada+desenfocada de fondo (estilo "Stories"), para que no queden
    franjas negras/lisas.
    """
    target_w, target_h = size
    src_w, src_h = img.size

    # fondo: cubre todo el lienzo (recortado, sí, pero es solo relleno
    # desenfocado -- no importa que pierda partes del sujeto)
    bg = _smart_crop(img, size, None).filter(ImageFilter.GaussianBlur(radius=max(int(target_w * 0.03), 20)))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)

    # primer plano: la imagen ENTERA, escalada para entrar sin recortar
    scale = min(target_w / src_w, target_h / src_h)
    fit_w, fit_h = max(int(src_w * scale), 1), max(int(src_h * scale), 1)
    fitted = img.resize((fit_w, fit_h), Image.LANCZOS)

    canvas = bg.convert("RGB")
    canvas.paste(fitted, ((target_w - fit_w) // 2, (target_h - fit_h) // 2))
    return canvas


def _smart_crop(img: Image.Image, size: tuple, face_box) -> Image.Image:
    """
    Recorta a `size` centrando en la cara detectada si hay una, en vez del
    centro geométrico — así no se corta la cabeza del sujeto como pasa con
    ImageOps.fit cuando la imagen fuente no tiene ya la misma proporción.
    """
    target_w, target_h = size
    src_w, src_h = img.size
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        new_w = int(src_h * target_ratio)
        new_h = src_h
    else:
        new_w = src_w
        new_h = int(src_w / target_ratio)

    if face_box is not None:
        fx, fy, fw, fh = face_box
        cx, cy = fx + fw / 2, fy + fh / 2
    else:
        cx, cy = src_w / 2, src_h * 0.4  # sujetos suelen estar en el tercio superior

    left = min(max(cx - new_w / 2, 0), src_w - new_w)
    top = min(max(cy - new_h / 2, 0), src_h - new_h)
    cropped = img.crop((int(left), int(top), int(left) + new_w, int(top) + new_h))
    return cropped.resize(size, Image.LANCZOS)


def _open_scene_image(scene_path: str) -> Image.Image:
    """Abre una escena como imagen: si es un video, extrae un frame del medio."""
    path = Path(scene_path)
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return Image.open(path).convert("RGB")

    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(frame_count // 2, 0))
        ok, frame = cap.read()
        if not ok:
            raise ValueError(f"No se pudo leer un frame del video: {scene_path}")
    finally:
        cap.release()

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(frame_rgb)


def _probe_scene_size(scene_image_path: str) -> tuple:
    """Lee el ancho/alto real de la escena (imagen o video) sin decodificar
    todos los frames, para saber si el video es horizontal o vertical."""
    path = Path(scene_image_path)
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        with Image.open(path) as img:
            return img.size

    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    return (w, h) if w and h else THUMBNAIL_SIZE_HORIZONTAL


def _target_thumbnail_size(scene_image_path: str) -> tuple:
    """Elige 16:9 o 9:16 según la orientación real del video (escena de
    referencia) -- así un Short/Reel vertical no termina con una miniatura
    horizontal deformada."""
    try:
        w, h = _probe_scene_size(scene_image_path)
    except Exception:
        return THUMBNAIL_SIZE_HORIZONTAL
    return THUMBNAIL_SIZE_VERTICAL if h > w else THUMBNAIL_SIZE_HORIZONTAL


def _load_headline_font(size: int):
    if ANTON_FONT_PATH.exists():
        try:
            return ImageFont.truetype(str(ANTON_FONT_PATH), size)
        except Exception:
            pass
    for path in ("C:/Windows/Fonts/Impact.ttf", "C:/Windows/Fonts/arialbd.ttf"):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


_DANGLING_WORDS = {
    "que", "se", "de", "del", "en", "a", "y", "o", "la", "el", "los", "las",
    "un", "una", "unos", "unas", "su", "sus", "con", "por", "para", "al",
    "lo", "le", "les", "es", "fue", "era", "sin", "más", "pero", "como",
}


def _shorten_hook(text: str, max_words: int = 8) -> str:
    """
    Los thumbnails que enganchan usan pocas palabras gigantes (tipo
    "UNA SEGUNDA VIDA", "¿QUÉ ESCONDÍA?"), no la oración completa del título
    SEO. Se toma solo el arranque del texto, cortado en la primera puntuación
    fuerte si hay una antes del límite de palabras.

    Si el corte por límite de palabras (no por puntuación) termina en un
    conector/pronombre ("...este perro SE"), se seguiría leyendo raro/a medio
    terminar -- se recorta hacia atrás hasta una palabra que no cuelgue así.
    """
    text = (text or "").strip()
    if not text:
        return "HISTORIA DE RESCATE"
    cut = re.split(r"[.!?;:,]", text, maxsplit=1)[0].strip()
    words = cut.split()
    if len(words) > max_words:
        words = words[:max_words]
        while len(words) > 2 and words[-1].lower().strip("¿?¡!") in _DANGLING_WORDS:
            words.pop()
    return " ".join(words) or "HISTORIA DE RESCATE"


def _split_headline_lines(hook: str) -> list:
    """Divide el gancho en 2-3 líneas cortas, balanceando la cantidad de palabras."""
    words = hook.split()
    if len(words) <= 2:
        return [hook]
    n_lines = 2 if len(words) <= 4 else 3
    per_line = -(-len(words) // n_lines)  # división redondeando hacia arriba
    lines = [" ".join(words[i:i + per_line]) for i in range(0, len(words), per_line)]
    return lines


def _tracked_char_advance(draw, ch, font, tracking_ratio):
    cw = draw.textlength(ch, font=font)
    return cw * (1 + tracking_ratio) if ch != " " else cw * 0.6


def _draw_tracked(draw, xy, text, font, fill, tracking_ratio, stroke_width, stroke_fill):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
        x += _tracked_char_advance(draw, ch, font, tracking_ratio)
    return x


def _tracked_line_width(draw, text, font, tracking_ratio):
    return sum(_tracked_char_advance(draw, ch, font, tracking_ratio) for ch in text)


def _draw_headline(img: Image.Image, hook: str) -> Image.Image:
    """
    Superpone el titular estilo Anton: mayúsculas, contorno negro grueso,
    sombra difuminada, tracking cerrado, interlineado apretado y la palabra
    más larga (heurística de "palabra clave") agrandada ~25%.

    `hook` ya viene armado (titular viral de IA o fallback mecánico) -- ver
    generate_thumbnail.
    """
    W, H = img.size

    lines = _split_headline_lines(hook)
    main_idx = max(range(len(lines)), key=lambda i: max((len(w) for w in lines[i].split()), default=0))

    line_gap_ratio = 1.12
    tracking = -0.01
    warm_white = (248, 246, 240)
    black_stroke = (8, 8, 8)
    main_to_base_ratio = 172 / 130  # proporción "palabra clave" vs línea normal

    # tamaño de fuente derivado de un presupuesto de alto de bloque: el
    # titular entero (todas las líneas) no debe superar ~33% de H, para no
    # taparle la composición al fondo generado por IA.
    max_block_height = H * 0.33
    weight_total = (len(lines) - 1) + main_to_base_ratio
    base_px = max_block_height / (line_gap_ratio * weight_total)
    main_px = base_px * main_to_base_ratio

    margin_x = int(W * 0.06)
    y0 = int(H * 0.06)
    max_line_width = W - 2 * margin_x

    img = img.convert("RGBA")
    dummy_draw = ImageDraw.Draw(img)

    # achicar la fuente hasta que la línea más ancha entre en el margen disponible
    for _ in range(30):
        widest = 0
        for i, line in enumerate(lines):
            px = main_px if i == main_idx else base_px
            font = _load_headline_font(int(px))
            widest = max(widest, _tracked_line_width(dummy_draw, line, font, tracking))
        if widest <= max_line_width or base_px <= 28:
            break
        shrink = max_line_width / widest
        base_px *= shrink
        main_px *= shrink

    # sombra: capa aparte, difuminada, desplazada abajo-derecha
    shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow_layer)
    shadow_offset = max(int(W * 0.008), 6)
    yy = y0
    for i, line in enumerate(lines):
        px = main_px if i == main_idx else base_px
        font = _load_headline_font(int(px))
        sdraw.text((margin_x + shadow_offset, yy + shadow_offset), line, font=font, fill=(0, 0, 0, 220))
        yy += int(px * line_gap_ratio)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=max(int(W * 0.004), 3)))
    img = Image.alpha_composite(img, shadow_layer)

    draw = ImageDraw.Draw(img)
    yy = y0
    for i, line in enumerate(lines):
        px = main_px if i == main_idx else base_px
        font = _load_headline_font(int(px))
        stroke_w = max(int(px * 0.05), 4)
        _draw_tracked(draw, (margin_x, yy), line, font, warm_white, tracking, stroke_w, black_stroke)
        yy += int(px * line_gap_ratio)

    return img.convert("RGB")


def generate_thumbnail(scene_image_path: str, title_text: str, out_path: str) -> str:
    """
    Genera una miniatura de YouTube: un frame de la escena del video recortado
    de forma inteligente, con un titular corto estilo Anton (headline viral)
    superpuesto.

    Args:
        scene_image_path: ruta a la imagen de escena a usar de respaldo.
        title_text: texto del que se extrae el gancho corto a mostrar.
        out_path: ruta donde guardar el thumbnail (.jpg).

    Returns:
        La ruta del archivo generado (out_path).
    """
    size = _target_thumbnail_size(scene_image_path)
    img = _open_scene_image(scene_image_path)
    try:
        face_box = _detect_face_box(img)
    except Exception:
        face_box = None
    img = _smart_crop(img, size, face_box)
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Color(img).enhance(1.15)

    headline = _shorten_hook(title_text).upper()

    img = _draw_headline(img, headline)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    quality = 90
    img.save(out, "JPEG", quality=quality)
    while out.stat().st_size > MAX_BYTES and quality > 40:
        quality -= 10
        img.save(out, "JPEG", quality=quality)

    return str(out)


def import_uploaded_thumbnail(image_bytes: bytes, out_path: str) -> str:
    """Toma una miniatura subida a mano (ej. generada en ChatGPT), la ajusta a
    THUMBNAIL_SIZE (recorte centrado) y la guarda respetando MAX_BYTES."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = _smart_crop(img, THUMBNAIL_SIZE, None)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    quality = 90
    img.save(out, "JPEG", quality=quality)
    while out.stat().st_size > MAX_BYTES and quality > 40:
        quality -= 10
        img.save(out, "JPEG", quality=quality)

    return str(out)
