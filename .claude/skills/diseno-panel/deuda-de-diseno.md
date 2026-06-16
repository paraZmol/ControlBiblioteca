# Deuda de diseno — Panel Admin (backlog priorizado)

Backlog de la auditoria 2026-06-11, ordenado por severidad. **Tratar como punto de
partida, no como verdad final:** la evidencia con numeros de linea de `app.js`/`index.html`
puede haber rotado (ambos archivos estan modificados); cuando un item cite un simbolo
(`mostrarConfirmacion`, `bloquearTerminal`, `_notifTimer`, etc.) confirmar con grep antes de
actuar. Las cifras de contraste son estimadas: recalcular con herramienta sobre los valores
OKLch reales antes de publicarlas. `style.css` esta limpio respecto a HEAD, asi que sus
lineas son fiables.

Severidad: critical > high > med > low. Esfuerzo: S (chico) / M (medio) / L (grande).

## Critica

1. **Dependencia de CDN externo (Tailwind + Phosphor) — punto unico de fallo en LAN sin
   internet.** [S de impacto, M de esfuerzo]
   - Evidencia: `index.html` `<head>` carga `cdn.tailwindcss.com` y
     `unpkg.com/@phosphor-icons/web`.
   - Fix: compilar Tailwind a CSS estatico local (build/PostCSS) y servir Phosphor como
     webfont/SVG local en `/static/`. Sin internet hoy se pierden TODAS las utilidades
     Tailwind y TODOS los iconos.

## Alta

2. **Ocultamiento solo-superadmin solo por JS, sin fallback CSS (fail-open).** [S]
   - Evidencia: `.bd-solo-superadmin` se usa en `index.html` pero tiene 0 reglas en
     `style.css` (verificado). La ocultacion depende de `app.js`.
   - Fix: agregar `.bd-solo-superadmin { display: none !important; }` por defecto en CSS; el
     JS REVELA el elemento solo si es superadmin (fail-closed).

3. **Sin etiquetas, ARIA ni roles — accesibilidad WCAG A incumplida.** [M]
   - Evidencia: en `index.html`, 0 `for=`, 0 `aria-label`, 0 `role=` (verificado). Inputs de
     login solo con placeholder; botones solo-icono.
   - Fix: `label for=` en todos los inputs; `aria-label` en botones solo-icono;
     `role="dialog"`/`aria-modal` en modales; `role="tab"/"tablist"` en tabs; `role="alert"`
     + `aria-live` en `.error`.

4. **Sin sistema unificado de inputs/tablas — duplicacion masiva de clases.** [L]
   - Evidencia: `.input-filtro`, `.hist-select`, `.act-filter-input`, `.modal-input` con las
     mismas props; tablas reinventadas (`.tabla`, `.hc-*`, render inline de incidencias).
   - Fix: crear `.input` base + variantes y `.table` base (`.table__row`/`__cell`) reusable
     en historial / incidencias / bans / auditoria.

5. **Jerarquia visual confusa: los elementos compiten sin niveles de enfasis.** [M]
   - Evidencia: en Monitoreo el hero, los KPIs, alertas y eventos no se distinguen con
     claridad; en Base de Datos los botones nuevo/exportar/importar van al mismo peso; las
     3 subtabs de Incidencias no tienen una principal.
   - Fix: definir 3 niveles (primary/secondary/tertiary); usar `--a` solo en el CTA
     principal; jerarquizar Hero > KPIs > listas laterales.

6. **Toast de duracion fija para toda accion; sin feedback diferenciado ni progreso.** [M]
   - Evidencia: la notificacion usa un timeout fijo (~4000ms) para todos los tipos
     (`mostrarNotificacion` / `_notifTimer` en `app.js`); las operaciones WS no muestran
     spinner; el timeout cierra el modal en silencio.
   - Fix: duracion por criticidad (error/destructiva >=6s, ok 4s, info 3s); spinner
     "Procesando..." en acciones WS; mensaje explicito al expirar el timeout.

7. **Confirmaciones destructivas incompletas y sin pre-validacion de WebSocket.** [M]
   - Evidencia: importar maestro/personal y eliminar terminal fantasma no confirman antes de
     ejecutar; los handlers validan el estado del socket DESPUES del click. (Verificar
     `mostrarConfirmacion` y los handlers actuales por grep.)
   - Fix: `mostrarConfirmacion()` antes de importaciones/eliminaciones (copiar el patron de
     `bloquearTerminal`); pre-validar el estado del WS en cada handler antes de abrir el
     modal.

8. **Tablas densas y grilla de PCs sin responsividad por debajo de 768px.** [M]
   - Evidencia: tabla de incidencias de 9 columnas con `whitespace-nowrap`/`min-w-max`;
     `terminales-grid` con `minmax(...)` que no baja a 1 columna en pantallas muy chicas.
   - Fix: `@media (max-width:768px)` -> tablas densas a vista de cards (la vista `.hc-*` ya
     existe); `terminales-grid` a 1 columna en <=480px.

## Media

9. **Login visualmente desconectado del sistema** (paleta cian hue ~200 hardcodeada +
   glassmorphism). [M] — Fix: reemplazar el hue 200 por `var(--a)` indigo, alinear espaciado
   y radios al panel, y agregar `background-color: var(--bg)` de fallback antes de la imagen
   de fondo.

10. **Subtabs y badges manejados con toggles Tailwind ad-hoc** en vez de las clases CSS
    existentes. [M] — Fix: usar `.subtab-btn.active` (un solo toggle) y una familia
    `.badge-*` reutilizable en todas las tablas.

11. **Estado de terminal fragmentado** (opacidad, punto y badge en clases inconexas). [S] —
    Fix: unificar en `.terminal-card[data-state=offline|activo|bloqueado]` que controle
    opacidad + punto + badge juntos.

12. **Hit targets por debajo de 44px** en controles secundarios (botones de zoom, de tabla,
    chips, checkboxes ~16px). [S] — Fix: subir el area clicable a >=36-44px via
    min-width/min-height o padding, manteniendo el visual.

13. **Contraste insuficiente** en texto secundario/muted y en texto de acento sobre fondos
    tenues (`--t3` y `--a` sobre `--a-dim`, label de login claro). [M] — Fix: subir
    `--t2`/`--t3`; crear un `--a-text` mas oscuro para texto sobre `--a-dim`; aclarar el
    label del login. (Recalcular ratios reales antes de fijar valores.)

14. **Duplicacion de estilos inline vs CSS** (anchos, grids y font-size sueltos en el HTML).
    [M] — Fix: extraer a clases utilitarias propias o tokens.

15. **Estado de carga inconsistente y sin manejo de error visible en tablas async.** [M] —
    Evidencia: algunas tablas muestran "Cargando...", otras no; en error la tabla puede
    quedar vacia (p. ej. auditoria solo loguea). Fix: skeleton/loader reutilizable; en
    `catch` setear una fila de error; deshabilitar la seccion durante el fetch.

## Baja

16. **Escala de radios incompleta** (20/24px hardcodeados). [S] — Fix: agregar `--r-lg:20px`
    y `--r-xl:24px` y reemplazar los valores sueltos.

17. **Sin escala de espaciado en variables** (hardcodeado + Tailwind en paralelo). [M] —
    Fix: definir `--sp-1..6` alineado a la escala Tailwind y reemplazar.

18. **Logica duplicada en paginacion y en `addLog`** (clase CSS derivada por regex del
    mensaje). [S] — Fix: `renderPaginacion(...)` unica; `addLog(category, message,
    semanticClass)` explicito.

19. **Imagen de fondo del login sin fallback de color** ni optimizacion. [S] — Fix:
    `background-color` previo + verificar peso (WebP).

20. **CSS no organizado para produccion** (media queries al final, colores sueltos, sin
    minificar). [M] — Fix: extraer colores a variables, agrupar media por componente,
    minificar en build, `contain: layout style` en cards.
