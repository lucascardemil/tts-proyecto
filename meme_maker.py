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
