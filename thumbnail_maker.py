"""
Generación automática de miniaturas (thumbnails) para YouTube a partir de una
escena del video + texto superpuesto. Usa Pillow (ya es dependencia del
proyecto para el manejo de imágenes/EXIF).
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

THUMBNAIL_SIZE = (1280, 720)
MAX_BYTES = 2 * 1024 * 1024  # límite de YouTube para thumbnails
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}


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


def _load_font(size: int):
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list:
    words = text.split()
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:3]  # como mucho 3 líneas, para no tapar toda la imagen


def generate_thumbnail(scene_image_path: str, title_text: str, out_path: str) -> str:
    """
    Toma una escena del video, la recorta a 16:9 y le superpone el título con
    sombra para que se lea bien en miniatura.

    Args:
        scene_image_path: ruta a la imagen de escena a usar de fondo.
        title_text: texto a superponer (se acorta si es muy largo).
        out_path: ruta donde guardar el thumbnail (.jpg).

    Returns:
        La ruta del archivo generado (out_path).
    """
    img = _open_scene_image(scene_image_path)
    img = ImageOps.fit(img, THUMBNAIL_SIZE, method=Image.LANCZOS)

    # Oscurece la mitad inferior para que el texto siempre sea legible,
    # sin importar cómo sea la imagen de fondo.
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    band_top = int(THUMBNAIL_SIZE[1] * 0.6)
    draw_overlay.rectangle([0, band_top, THUMBNAIL_SIZE[0], THUMBNAIL_SIZE[1]], fill=(0, 0, 0, 160))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(img)
    font = _load_font(72)
    text = (title_text or "").strip()[:90] or "Historia Real"
    lines = _wrap_text(draw, text, font, max_width=THUMBNAIL_SIZE[0] - 80)

    line_height = font.size + 12
    total_height = line_height * len(lines)
    y = THUMBNAIL_SIZE[1] - total_height - 40

    for line in lines:
        x = 40
        # Sombra para contraste sobre cualquier fondo
        draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=(255, 255, 255))
        y += line_height

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    quality = 90
    img.save(out, "JPEG", quality=quality)
    while out.stat().st_size > MAX_BYTES and quality > 40:
        quality -= 10
        img.save(out, "JPEG", quality=quality)

    return str(out)
