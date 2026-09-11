"""
Generación automática de miniaturas (thumbnails) para YouTube. El fondo se
genera vía chat.qwen.ai (modo "Create Image", composición fotorrealista tipo
"animal protagonista + secundario + contraluz"); si Qwen no está disponible
se usa como respaldo un frame de la escena del video, recortado de forma
inteligente. Encima se superpone un titular corto estilo Anton (headline
viral: contorno grueso, sombra difuminada, tracking cerrado).
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

AI_BACKGROUND_PROMPT_TEMPLATE = (
    "documentary premium photograph, hyperrealistic emotional rescue scene, "
    "action shot caught mid-moment (not a calm posed portrait). "
    "This thumbnail is for this real story: \"{hook}\" — the main "
    "subject/animal in the image MUST be exactly the one mentioned there, "
    "no other animal or subject. Depict the single most dramatic, climactic, "
    "high-impact moment implied by that story — the peak of the action or "
    "emotion (the danger, the struggle, the rescue in progress, the reveal, "
    "the reunion) — the subject caught mid-motion or mid-emotion, not "
    "standing still and calmly staring at the camera. Medium shot, NOT an "
    "extreme close-up: the subject's full body (or at least head, torso and "
    "legs) must be entirely visible inside the frame with a comfortable "
    "margin on all sides — nothing cropped or cut off at the edges. Subject "
    "large and clearly the focus of the image, but with visible breathing "
    "room around it, sharp eyes, extreme fur/feather detail, a second "
    "smaller subject nearby creating scale contrast, cinematic depth of "
    "field, sharp subject with blurred background, warm dramatic backlight, "
    "rim light, tense/emotional atmosphere matching that climactic moment. "
    "Framing: shot from a slightly low angle so the subject's body sits "
    "within the LOWER two-thirds of the frame, fully contained with margin "
    "— the TOP quarter of the image must be empty open background (sky, "
    "blurred distant scenery, or out-of-focus space) with absolutely no "
    "fur, horns, ears, head or any part of the subject in it, reserved for "
    "a text overlay to be added later. 35mm photograph, volumetric lighting, cinematic contrast, "
    "natural colors, professional photographic finish, scroll-stopping "
    "composition, {ratio_label} format. "
    "Avoid: any text, letters, words, captions, watermark, logo, low "
    "quality, blurry, cartoon, illustration, 3d render, deformed, extra "
    "limbs, bad anatomy, painting, drawing, generic calm portrait, subject "
    "touching the top edge of the frame, subject cropped or cut off by the "
    "frame edges, extreme close-up, and avoid any animal/subject that "
    "is not the one mentioned in the story."
)

CHATGPT_PROMPT_TEMPLATE = (
    "Generá una miniatura de YouTube horizontal 1280x720 (16:9), fotografía "
    "documental hiperrealista y emocional: un animal protagonista enorme y "
    "dominante en primer plano mirando directo a cámara, ojos nítidos, "
    "detalle de pelaje extremo, un segundo animal más chico cerca creando "
    "contraste de escala, historia de rescate y segunda oportunidad, "
    "profundidad de campo cinematográfica, contraluz cálido y dramático, "
    "luz de borde sobre el pelaje, composición en capas con espacio libre "
    "arriba para texto, foto de 35mm, contraste cinematográfico, colores "
    "naturales, terminación fotográfica profesional. La imagen tiene que "
    "coincidir fielmente con la historia real relatada (mismo animal, mismo "
    "contexto), sin inventar otro sujeto.\n\n"
    "Sumale un titular grande en MAYÚSCULAS que diga exactamente: "
    "\"{headline}\"\n"
    "Tipografía sans-serif ultra condensada y pesada tipo Anton/Impact "
    "Condensed, relleno blanco cálido, contorno negro grueso (~5% del alto "
    "de letra), tracking cerrado (-2% a -5%), interlineado 80-88%, sombra "
    "difuminada con offset, la palabra clave más grande que el resto, "
    "bloque de texto ocupando 45-65% del ancho, márgenes 5-7%, sin tapar "
    "nunca los ojos ni la cara del animal — el titular va arriba, en el "
    "espacio libre reservado para eso, nunca encima de la cabeza o cara "
    "del animal."
)


def build_chatgpt_prompt(title_text: str) -> str:
    """Arma el prompt de texto para pegar a mano en ChatGPT/DALL-E y generar la miniatura ahí."""
    headline = _shorten_hook(title_text).upper()
    return CHATGPT_PROMPT_TEMPLATE.format(headline=headline)

_FACE_CASCADE = None


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
    `_smart_crop`) -- la imagen generada por Qwen casi nunca viene ya en la
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


AI_BACKGROUND_MAX_ATTEMPTS = 3
_TOP_STRIP_RATIO = 0.35  # coincide aprox. con el bloque de titular (_draw_headline)
_TOP_STRIP_BUSY_THRESHOLD = 18.0  # heuristico: por debajo, la franja se considera "libre"


def _top_strip_busyness(img: Image.Image) -> float:
    """Heurística barata (densidad de bordes) de qué tan 'ocupado' está el
    tercio superior de la imagen ya recortada -- sirve para detectar cuando
    Qwen ignoró el pedido de dejar esa franja libre para el titular (más
    alto = más ocupado, ej. pelaje/cuernos/orejas metidos ahí)."""
    W, H = img.size
    strip = img.crop((0, 0, W, int(H * _TOP_STRIP_RATIO))).convert("L")
    edges = strip.filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).mean[0]


def _generate_ai_background(size: tuple, title_text: str) -> Image.Image:
    """
    Genera el fondo vía chat.qwen.ai (modo "Create Image"), reusando la misma
    sesión persistente que ya usa la generación de clips (QWEN_SESSION) --
    sin login ni servidor local aparte. El prompt incluye el título/gancho de
    la historia para que el sujeto de la imagen coincida con la historia real
    (sin esto, Qwen inventa cualquier animal). Lanza una excepción si Qwen no
    responde o la generación falla en TODOS los intentos; el llamador debe
    capturarla y usar un respaldo (frame de escena).

    Qwen no siempre respeta el pedido de dejar libre la franja superior (para
    el titular) al primer intento -- se reintenta hasta `AI_BACKGROUND_MAX_ATTEMPTS`
    veces y se usa el resultado con la franja superior menos "ocupada"
    (`_top_strip_busyness`), cortando apenas se logra uno suficientemente libre.
    """
    is_vertical = size[1] > size[0]
    ratio_label = "9:16 vertical (formato Reels/Shorts)" if is_vertical else "16:9 horizontal"
    ratio_code = "9:16" if is_vertical else "16:9"
    prompt = AI_BACKGROUND_PROMPT_TEMPLATE.format(
        hook=_shorten_hook(title_text, max_words=40), ratio_label=ratio_label
    )

    best_img = None
    best_score = None
    last_error = None
    for attempt in range(AI_BACKGROUND_MAX_ATTEMPTS):
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir) / f"qwen_bg_{uuid.uuid4().hex}.png"
                auto_pipeline.generate_qwen_image(
                    prompt, tmp_path, unattended=True, image_ratio=ratio_code
                )
                raw = Image.open(tmp_path).convert("RGB")
        except Exception as e:
            last_error = e
            continue
        # encajar completa (sin recortar cuerpo/patas) por si Qwen no respeta el formato pedido exacto
        fitted = _fit_with_blurred_pad(raw, size)
        score = _top_strip_busyness(fitted)
        if best_score is None or score < best_score:
            best_img, best_score = fitted, score
        if score <= _TOP_STRIP_BUSY_THRESHOLD:
            break

    if best_img is None:
        raise last_error or auto_pipeline.PipelineError("Qwen no generó ninguna imagen de fondo.")
    return best_img


def is_ai_backend_available() -> bool:
    """Chequeo rápido (sin generar nada) de si la sesión de Qwen está lista para usarse."""
    try:
        status = auto_pipeline.check_session_status(auto_pipeline.QWEN_SESSION, force_reload=False)
        return status.get("state") == "ok"
    except Exception:
        return False


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
        return "HISTORIA REAL"
    cut = re.split(r"[.!?;:,]", text, maxsplit=1)[0].strip()
    words = cut.split()
    if len(words) > max_words:
        words = words[:max_words]
        while len(words) > 2 and words[-1].lower().strip("¿?¡!") in _DANGLING_WORDS:
            words.pop()
    return " ".join(words) or "HISTORIA REAL"


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


HEADLINE_PROMPT_TEMPLATE = (
    "Escribí SOLO un titular corto y viral en español para la miniatura de un "
    "video de YouTube Shorts, basado en esta historia real: \"{hook}\". "
    "Estilo clickbait pero verídico (no inventes nada que no esté en la "
    "historia): que genere curiosidad, 3 a 6 palabras, todo en MAYÚSCULAS, "
    "sin comillas, sin emojis, sin punto final. Respondé ÚNICAMENTE el "
    "titular y nada más."
)


def _generate_viral_headline(title_text: str) -> str:
    """Le pide a Qwen (mismo chat/sesión que ya generó el fondo) un titular
    corto y viral acorde a la historia real -- reemplaza el truncado mecánico
    de _shorten_hook cuando Qwen está disponible."""
    prompt = HEADLINE_PROMPT_TEMPLATE.format(hook=_shorten_hook(title_text, max_words=25))
    reply = auto_pipeline.generate_qwen_text(prompt, unattended=True)
    headline = reply.strip().strip('"').strip("'").upper()
    words = headline.split()
    if not headline or len(words) > 8:
        raise ValueError(f"Titular de Qwen vacío o demasiado largo: {headline!r}")
    return headline


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
    Genera una miniatura de YouTube: fondo fotorrealista vía Qwen (chat.qwen.ai)
    y, si la sesión no está disponible, un frame de la escena del video
    recortado de forma inteligente como respaldo. Encima superpone un
    titular corto estilo Anton (headline viral).

    Args:
        scene_image_path: ruta a la imagen de escena a usar de respaldo.
        title_text: texto del que se extrae el gancho corto a mostrar.
        out_path: ruta donde guardar el thumbnail (.jpg).

    Returns:
        La ruta del archivo generado (out_path).
    """
    size = _target_thumbnail_size(scene_image_path)
    ai_background_ok = False
    if title_text.strip():
        try:
            img = _generate_ai_background(size, title_text)
            ai_background_ok = True
        except Exception as e:
            auto_pipeline.logger.warning(
                "thumbnail: fondo IA (Qwen) fallo, usando frame de escena de respaldo: %s", e
            )
    if not ai_background_ok:
        img = _open_scene_image(scene_image_path)
        try:
            face_box = _detect_face_box(img)
        except Exception:
            face_box = None
        img = _smart_crop(img, size, face_box)
        img = ImageEnhance.Contrast(img).enhance(1.12)
        img = ImageEnhance.Color(img).enhance(1.15)

    headline = None
    if ai_background_ok:
        try:
            headline = _generate_viral_headline(title_text)
        except Exception:
            headline = None
    if headline is None:
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
