# Identidad oficial del GAM Sipe Sipe

Fuentes proporcionadas por el usuario el 15/10/2026. No se generaron ni reinterpretaron símbolos. Solo se recortaron márgenes transparentes y se redujo el tamaño manteniendo proporción. Los recortes compactos conservan el escudo completo de las composiciones verticales.

| Asset runtime | Original | Uso |
|---|---|---|
| sipe_sipe_horizontal.png | escudo_sipe_sipe_horizontal.png | Login y Dashboard amplios |
| sipe_sipe_vertical.png | escudo_sipe_sipe_vertical.png | Variante vertical disponible, no usada para evitar un logo demasiado alto |
| sipe_sipe_horizontal_white.png | aplicaciones_digitales_02.png | Sidebar oscuro y drawer móvil |
| sipe_sipe_vertical_white.png | aplicaciones_digitales_04.png | Variante vertical blanca disponible |
| sipe_sipe_crest.png | escudo_sipe_sipe_vertical.png | Login/Dashboard compactos, PDF EDIE y reportes, favicon/PWA |
| sipe_sipe_crest_white.png | aplicaciones_digitales_04.png | Variante compacta blanca disponible |

No se usan filtros de color en runtime. La composición y los colores internos del escudo se conservan. Las aplicaciones digitales 03/05 fueron revisadas; se prefieren las composiciones a color para fondos claros y 02/04 para fondos oscuros. Los textos de gestión no se incluyen en el bundle.

Verde del wordmark horizontal: **#08813A**. Tokens UI en lib/core/theme/ui_colors.dart; tokens PDF/XLSX en backend/app/core/branding.py. Fondos blanco/neutros, verde tenue #EDF7F0 y sidebar #10251A. Blanco sobre el verde principal supera contraste AA (4.5:1). No se añaden gradientes ni dark mode.

Los ocho originales y pubspec previo del usuario están preservados fuera de la aplicación en C:/dev/repo_backups/branding_originales_20261015; manifest_sha256.json y runtime_provenance.json permiten auditar origen y dimensiones. El backend incluye su PNG dentro de app/assets/branding para que COPY app del Dockerfile lo empaquete sin depender del filesystem Flutter.
