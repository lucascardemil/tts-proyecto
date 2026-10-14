# three.js (vendorizado)

- Paquete: three@0.185.1, bajado con `npm pack` (MIT License, © three.js authors).
- Archivos: `three.module.min.js` (+ `three.core.min.js`, que el primero importa), tal cual salen de `build/`.
- Se carga solo desde `static/office3d.js`. Para actualizar: reemplazar los archivos y revisar la API usada
  (Scene, PerspectiveCamera, WebGLRenderer, LineSegments, EdgesGeometry, CanvasTexture, Sprite, Raycaster).
- Nota: three@0.186.x ya no publica los `.min.js` en el paquete npm; 0.185.1 es la última versión que los trae.
