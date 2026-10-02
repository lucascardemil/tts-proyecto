# Gaming Clip Render with GPU

## Overview
`gaming_clip.py` ahora usa aceleración GPU via h264_nvenc para renderizar clips de Medal.

## Key Changes
- Codec cambiado de `h264` (CPU) a `h264_nvenc` (GPU)
- Extension de salida: `.m4v` temporal → renombrado a `.mp4` (evita validación de Remotion)
- Instagram REELS requiere headers `Content-Length` + `X-Entity-Length` en rupload

## Commands
```bash
# Render clip manualmente
python -c "
import gaming_clip
props = json.load(open('video/public/gclip_<id>/props.json'))
gaming_clip.render_clip(props, Path('video/public/gclip_<id>/props.json'), Path('video/out/gclip_<id>.mp4'))
"
```

## Publish Flow
1. Facebook: subir video con protocolo resumible (`graph-video.facebook.com`)
2. Instagram: crear container REELS → subir a rupload → publicar

## Notes
- Cloudflare tunnel no necesario para videos (solo para imágenes en IG)
- GPU usage: ~40-80% during render vs CPU-only before
