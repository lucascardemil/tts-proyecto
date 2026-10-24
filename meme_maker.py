"""
Posts de imagen "meme nativo": una captura de juego (generada por IA) con
texto superior + remate inferior superpuestos, en blanco Anton con contorno
negro grueso (Workflow JugadasEpicasVideojuegos, seccion 2).

El texto se dibuja aca, no lo genera el modelo de imagen: la IA deforma las
letras, y el meme depende de que se lea en 2 segundos desde el celular.

Se renderiza en 4:5 (Instagram y Facebook usan la misma imagen): se recorta
al centro la imagen base y el texto se dibuja DESPUES del recorte para que
nunca quede cortado. El 1:1 se descarto: su recorte cortaba el HUD.
"""

from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path(__file__).parent / "fonts" / "Anton-Regular.ttf"

# (ancho, alto) finales.
SIZE_IG = (1080, 1350)  # 4:5
SIZE_SQUARE = (1080, 1080)  # 1:1 (anuncios disenados por la IA)

MARGIN_X = 0.06  # fraccion del ancho
MARGIN_Y = 0.055  # fraccion del alto (el workflow pide ~80px de 1350)
MAX_BLOCK_H = 0.18  # cada bloque de texto ocupa como mucho esta fraccion del alto
START_SIZE = 0.085  # tamano de fuente inicial, fraccion del ancho
MIN_SIZE = 0.045
LINE_GAP = 1.08
STROKE = 0.085  # grosor del contorno, fraccion del tamano de fuente (8px a ~95px)
SCRIM_ALPHA = 0.62  # opacidad maxima del degradado oscuro tras el texto
SCRIM_FADE = 0.05  # fraccion del alto en que el degradado se desvanece bajo el texto
BORDER_MAX_LUMA = 30  # una fila/columna con todos sus pixeles por debajo de esto es borde negro
BORDER_MIN_FRAC = 0.01  # recortes menores a esto se ignoran (ruido)
BORDER_MAX_FRAC = 0.35  # si el borde ocuparia mas que esto de un eje, es una escena oscura: no se recorta


def _font(px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), px)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list:
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _fit_block(draw: ImageDraw.ImageDraw, text: str, size: tuple):
    """Devuelve (font, lines) con la fuente mas grande que deja el texto en
    el ancho util y en MAX_BLOCK_H del alto."""
    w, h = size
    max_width = int(w * (1 - 2 * MARGIN_X))
    px = int(w * START_SIZE)
    floor = int(w * MIN_SIZE)
    while True:
        font = _font(px)
        lines = _wrap(draw, text, font, max_width)
        widest = max((draw.textlength(line, font=font) for line in lines), default=0)
        block_h = len(lines) * px * LINE_GAP
        if (widest <= max_width and block_h <= h * MAX_BLOCK_H) or px <= floor:
            return font, lines
        px -= 2


def _dark_run(luma: Image.Image, horizontal: bool, from_end: bool) -> int:
    """Cantidad de filas (horizontal) o columnas consecutivas, desde un
    extremo, cuyo pixel mas claro es <= BORDER_MAX_LUMA."""
    w, h = luma.size
    length = h if horizontal else w
    count = 0
    for i in range(length):
        pos = length - 1 - i if from_end else i
        box = (0, pos, w, pos + 1) if horizontal else (pos, 0, pos + 1, h)
        if luma.crop(box).getextrema()[1] > BORDER_MAX_LUMA:
            break
        count += 1
    return count


def _trim_dark_borders(img: Image.Image) -> Image.Image:
    """Saca los bordes negros que a veces trae la imagen generada (relleno
    hasta la proporcion pedida): sin esto quedan como una franja muerta en
    el recorte final. Una escena simplemente oscura no se toca: los bordes
    se recortan solo si son una franja acotada y de negro casi puro."""
    w, h = img.size
    luma = img.convert("L")
    top, bottom = (_dark_run(luma, True, end) for end in (False, True))
    left, right = (_dark_run(luma, False, end) for end in (False, True))

    def _ok(a: int, b: int, length: int) -> bool:
        return a + b < length * BORDER_MAX_FRAC

    def _keep(n: int, length: int) -> int:
        return n if n >= length * BORDER_MIN_FRAC else 0

    if not _ok(top, bottom, h):
        top = bottom = 0
    if not _ok(left, right, w):
        left = right = 0
    top, bottom, left, right = _keep(top, h), _keep(bottom, h), _keep(left, w), _keep(right, w)
    if not (top or bottom or left or right):
        return img
    return img.crop((left, top, w - right, h - bottom))


def _crop_to(img: Image.Image, size: tuple) -> Image.Image:
    """Recorta al centro a la proporcion de `size` y escala a ese tamano."""
    tw, th = size
    w, h = img.size
    target = tw / th
    if w / h > target:  # mas ancha: recorta los costados
        new_w = int(h * target)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:  # mas alta: recorta arriba y abajo
        new_h = int(w / target)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    return img.resize(size, Image.LANCZOS)


def _darken_band(img: Image.Image, band_h: int, at_top: bool) -> None:
    """Oscurece `band_h` px desde el borde superior/inferior: opacidad
    SCRIM_ALPHA hasta el final del texto y luego un desvanecido de SCRIM_FADE.
    Asi el texto se lee aunque debajo haya HUD u objetos claros."""
    w, h = img.size
    fade = max(int(h * SCRIM_FADE), 1)
    total = min(band_h + fade, h)
    mask = Image.new("L", (1, total))
    solid = int(255 * SCRIM_ALPHA)
    for i in range(total):  # i = distancia al borde
        alpha = solid if i < band_h else int(solid * (1 - (i - band_h) / fade))
        mask.putpixel((0, i), max(alpha, 0))
    mask = mask.resize((w, total))
    if not at_top:
        mask = mask.transpose(Image.FLIP_TOP_BOTTOM)
    y0 = 0 if at_top else h - total
    img.paste(Image.new("RGB", (w, total), (0, 0, 0)), (0, y0), mask)


def _draw_text(img: Image.Image, text: str, at_top: bool, fill: tuple = (255, 255, 255)) -> None:
    text = (text or "").strip().upper()
    if not text:
        return
    w, h = img.size
    draw = ImageDraw.Draw(img)
    font, lines = _fit_block(draw, text, img.size)
    px = font.size
    stroke = max(int(px * STROKE), 3)
    block_h = len(lines) * px * LINE_GAP
    margin = int(h * MARGIN_Y)
    _darken_band(img, int(margin + block_h + margin * 0.5), at_top)
    y = margin if at_top else h - margin - block_h
    for line in lines:
        draw.text(
            (w / 2, y), line, font=font, fill=fill,
            stroke_width=stroke, stroke_fill=(0, 0, 0), anchor="ma",
        )
        y += px * LINE_GAP


def render_meme(
    base_path: Path, top: str, bottom: Optional[str], out_path: Path, size: tuple,
    fill: tuple = (255, 255, 255),
) -> Path:
    """Escribe en `out_path` (JPEG) la imagen base recortada a `size` con el
    texto superior/inferior encima (`fill` = color del texto; contorno negro)."""
    with Image.open(base_path) as base:
        img = _crop_to(_trim_dark_borders(base.convert("RGB")), size)
    _draw_text(img, top, at_top=True, fill=fill)
    _draw_text(img, bottom or "", at_top=False, fill=fill)
    img.save(out_path, "JPEG", quality=92)
    return out_path


# ── Tarjeta editorial (posts de Niño selectivo y Macramé) ─────────────────────
# Foto limpia arriba y un panel de color de marca abajo con el titular y el remate: el texto nunca
# tapa la cara ni las manos. Georgia Bold/Italic del sistema (o Cambria) y, si no hay, Anton.

CARD_PHOTO_FRAC = 0.65  # fraccion del alto que ocupa la foto
CARD_ACCENT_H = 10  # franja de color entre la foto y el panel
CARD_PAD_X = 0.08
_SERIF_BOLD = ("C:/Windows/Fonts/georgiab.ttf", "C:/Windows/Fonts/cambriab.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")
_SERIF_ITALIC = ("C:/Windows/Fonts/georgiai.ttf", "C:/Windows/Fonts/cambriai.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf")


def _serif(candidates: tuple, px: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    return _font(px)


def fit_post(base_path: Path, out_path: Path, size: tuple, trim: bool = True) -> Path:
    """Imagen ya diseñada (con su texto) -> JPEG recortado al centro a `size`, sin bordes negros.
    Con `trim=False` no se quitan los bordes oscuros (fondo negro intencional):
    solo se recorta a la proporcion de `size`."""
    with Image.open(base_path) as base:
        img = base.convert("RGB")
        _crop_to(_trim_dark_borders(img) if trim else img, size).save(out_path, "JPEG", quality=92)
    return out_path


def sentence_case(text: str) -> str:
    return _sentence_case(text)


def _sentence_case(text: str) -> str:
    """"NO ES MAÑA" -> "No es maña": en una tarjeta sobria el titular no grita."""
    text = (text or "").strip()
    if text.isupper():
        text = text.lower()
    return text[:1].upper() + text[1:]


def _fit_serif(draw, text: str, candidates: tuple, max_width: int, max_height: int, start: int, floor: int, gap: float):
    px = start
    while True:
        font = _serif(candidates, px)
        lines = _wrap(draw, text, font, max_width)
        widest = max((draw.textlength(line, font=font) for line in lines), default=0)
        if (widest <= max_width and len(lines) * px * gap <= max_height) or px <= floor:
            return font, lines
        px -= 2


def render_card(
    base_path: Path, headline: str, subline: Optional[str], out_path: Path, size: tuple,
    panel: tuple = (251, 243, 228), ink: tuple = (59, 42, 32), accent: tuple = (214, 106, 79),
) -> Path:
    """Escribe en `out_path` (JPEG) una tarjeta `size`: foto arriba, franja de acento y panel de color
    con el titular (serif en negrita) y el remate (cursiva, color de acento)."""
    w, h = size
    photo_h = int(h * CARD_PHOTO_FRAC)
    with Image.open(base_path) as base:
        photo = _crop_to(_trim_dark_borders(base.convert("RGB")), (w, photo_h))
    img = Image.new("RGB", size, panel)
    img.paste(photo, (0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, photo_h, w, photo_h + CARD_ACCENT_H), fill=accent)

    top = photo_h + CARD_ACCENT_H
    area_h = h - top
    pad = int(h * 0.04)
    max_w = int(w * (1 - 2 * CARD_PAD_X))
    head = _sentence_case(headline)
    sub = (subline or "").strip().rstrip(".")
    sub_h_budget = int(area_h * 0.30) if sub else 0
    head_font, head_lines = _fit_serif(
        draw, head, _SERIF_BOLD, max_w, area_h - 2 * pad - sub_h_budget, int(w * 0.082), int(w * 0.05), 1.18,
    )
    head_h = len(head_lines) * head_font.size * 1.18
    sub_font = sub_lines = None
    sub_h = 0
    if sub:
        sub_font, sub_lines = _fit_serif(draw, sub, _SERIF_ITALIC, max_w, sub_h_budget, int(w * 0.05), int(w * 0.034), 1.25)
        sub_h = len(sub_lines) * sub_font.size * 1.25
    gap = int(h * 0.02) if sub else 0
    y = top + (area_h - (head_h + gap + sub_h)) / 2  # bloque centrado en el panel
    for line in head_lines:
        draw.text((w / 2, y), line, font=head_font, fill=ink, anchor="ma")
        y += head_font.size * 1.18
    y += gap
    for line in sub_lines or []:
        draw.text((w / 2, y), line, font=sub_font, fill=accent, anchor="ma")
        y += sub_font.size * 1.25
    img.save(out_path, "JPEG", quality=92)
    return out_path


# ---- identidad visual de Everyday Explained ---------------------------------------------------------------------------
# Los generadores de imagen (WanGP/Flux, Flow) escriben mal el texto dentro de la imagen ("EXPLOKE"), asi que la IA solo
# dibuja la ilustracion y TODO el texto (titular, explicacion, logotipo, numeracion) se compone aqui, con ortografia exacta.
INFO_NAVY = (11, 30, 59)  # #0B1E3B
INFO_BLUE = (0, 138, 255)  # #008AFF
INFO_YELLOW = (255, 214, 0)  # #FFD600
INFO_WHITE = (255, 255, 255)
INFO_MARGIN_FRAC = 0.05
EVERYDAY_TAGLINE = "Big questions. Simple answers."
_SANS_BOLD = ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _fit_text(draw, text: str, font_for, max_width: int, max_height: int, start: int, floor: int, gap: float, max_lines: int):
    """(font, lineas) con el texto mas grande que cabe en `max_width` x `max_height` y `max_lines` lineas."""
    px = start
    while True:
        font = font_for(px)
        lines = _wrap(draw, text, font, max_width)
        if (len(lines) <= max_lines and len(lines) * px * gap <= max_height) or px <= floor:
            return font, lines
        px -= 2


def _draw_lines(draw, lines: list, font, fill: tuple, x: float, y: float, gap: float, anchor: str = "la") -> float:
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill, anchor=anchor)
        y += font.size * gap
    return y


def render_everyday_card(art_path: Path, headline: str, body: str, out_path: Path, size: tuple = SIZE_IG, *,
                         tag_right: str = "", label_top: str = "", label_bottom: str = "", footer: str = "",
                         footer_fill: tuple = INFO_YELLOW) -> Path:
    """Tarjeta 4:5 de la marca: logotipo y numeracion arriba, etiqueta + titular (Anton, mayusculas), la ilustracion
    (esquinas redondeadas) y, debajo, etiqueta + explicacion y un pie. La ilustracion ocupa todo el alto que sobra, asi
    que sin explicacion (diapositiva de gancho) sale mas grande. Todo el texto lo dibuja Pillow: ortografia exacta."""
    w, h = size
    m = int(w * INFO_MARGIN_FRAC)
    max_w = w - 2 * m
    img = Image.new("RGB", size, INFO_NAVY)
    draw = ImageDraw.Draw(img)

    # cabecera: logotipo (EVERYDAY amarillo + EXPLAINED blanco) y numeracion
    logo_font = _font(int(w * 0.042))
    draw.text((m, m * 0.8), "EVERYDAY", font=logo_font, fill=INFO_YELLOW, anchor="la")
    x = m + draw.textlength("EVERYDAY ", font=logo_font)
    draw.text((x, m * 0.8), "EXPLAINED", font=logo_font, fill=INFO_WHITE, anchor="la")
    tag_font = _serif(_SANS_BOLD, int(w * 0.03))
    if tag_right:
        draw.text((w - m, m * 0.8 + logo_font.size * 0.12), tag_right, font=tag_font, fill=INFO_BLUE, anchor="ra")
    y = m * 0.8 + logo_font.size * 1.55
    draw.rectangle((m, y, m + int(w * 0.12), y + 5), fill=INFO_BLUE)
    y += 22

    label_font = _serif(_SANS_BOLD, int(w * 0.028))
    if label_top:
        draw.text((m, y), label_top.upper(), font=label_font, fill=INFO_BLUE, anchor="la")
        y += label_font.size * 1.5
    title = (headline or "").strip().upper()
    head_font, head_lines = _fit_text(draw, title, _font, max_w, int(h * 0.25), int(w * 0.105), int(w * 0.058), 1.08, 3)
    y = _draw_lines(draw, head_lines, head_font, INFO_WHITE, m, y, 1.08)
    art_top = y + int(h * 0.022)

    # bloque inferior (se mide primero para saber cuanto alto le queda a la ilustracion)
    text = (body or "").strip()
    gap = 1.28
    body_font = body_lines = None
    block_h = 0.0
    if text:
        body_font, body_lines = _fit_text(draw, text, lambda px: _serif(_SANS_BOLD, px), max_w, int(h * 0.19),
                                          int(w * 0.046), int(w * 0.03), gap, 6)
        block_h = len(body_lines) * body_font.size * gap + (label_font.size * 1.5 if label_bottom else 0) + 14
    foot_font = _font(int(w * 0.036))
    foot_h = foot_font.size * 1.4 if footer else 0
    bottom_top = h - m * 0.7 - foot_h - block_h
    art_h = int(bottom_top - art_top - 10)
    art_w = max_w
    with Image.open(art_path) as art:
        art = _crop_to(art.convert("RGB"), (art_w, max(art_h, int(h * 0.2))))
    mask = Image.new("L", art.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, art.size[0] - 1, art.size[1] - 1), radius=int(w * 0.025), fill=255)
    img.paste(art, (m, int(art_top)), mask)

    y = int(art_top) + art.size[1] + 14
    if text:
        if label_bottom:
            draw.text((m, y), label_bottom.upper(), font=label_font, fill=INFO_BLUE, anchor="la")
            y += label_font.size * 1.5
        _draw_lines(draw, body_lines, body_font, INFO_WHITE, m, y, gap)
    if footer:
        draw.text((w / 2 if not text else m, h - m * 0.7), footer, font=foot_font, fill=footer_fill,
                  anchor="ms" if not text else "ls")
    img.save(out_path, "JPEG", quality=94)
    return out_path


CAROUSEL_LABELS = ("The hook", "The observation", "The science", "The detail", "The takeaway")
CAROUSEL_FOLLOW = "Follow Everyday Explained for more."


def render_carousel_slide(art_path: Path, title: str, text: str, out_path: Path, index: int, total: int = 5,
                          size: tuple = SIZE_IG) -> Path:
    """Formato B: diapositiva `index` (1..total) del carrusel. La 1 es el gancho (solo titular e ilustracion); la ultima
    cierra con la llamada a seguir la pagina."""
    label = CAROUSEL_LABELS[min(index, len(CAROUSEL_LABELS)) - 1] if total == len(CAROUSEL_LABELS) else f"Part {index}"
    footer = "SWIPE \u2192" if index == 1 else CAROUSEL_FOLLOW if index == total else ""
    return render_everyday_card(
        art_path, title, "" if index == 1 else text, out_path, size, tag_right=f"{index:02d} / {total:02d}",
        label_top=label, footer=footer,
    )
