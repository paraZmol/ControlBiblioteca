# Sistema de Diseno — Panel Admin Control Biblioteca UNASAM

Referencia fundamentada en el codigo real (auditoria 2026-06-11). El nucleo del diseno
son **CSS custom properties** definidas en `admin/static/css/style.css` (bloque `:root`
para dark, `html:not(.dark)` para light). Tailwind aporta utilidades secundarias en el
markup; **los tokens propios son la fuente de verdad**. Tema oscuro por defecto
(`<html lang="es" class="dark">`). Iconografia 100% Phosphor (`ph ph-*`); sin emojis.

> Aclaracion clave: el acento NO es verde. Es **indigo, hue 260**. El verde (hue 145) es
> el color de estado OK / online, no el acento.

`style.css` no esta modificado respecto a HEAD, asi que sus numeros de linea son fiables.
`index.html` y `app.js` SI estan modificados: sus offsets pueden variar — confirmar con
grep antes de citar.

## Tokens de color (style.css:8-106)

| Token | Dark | Light | Uso |
|---|---|---|---|
| `--bg`   | `#09090b` (zinc-950) | `#f0f0f2` | Fondo base |
| `--sur`  | `#18181b` | `#ffffff` | Superficie primaria (cards) |
| `--sur2` | `#1c1c1f` | `#f5f5f6` | Superficie secundaria (inputs, thead) |
| `--sur3` | `#27272a` | `#ebebed` | Superficie terciaria |
| `--bdr`  | `rgba(255,255,255,0.07)` | `rgba(0,0,0,0.10)` | Borde sutil |
| `--bdr2` | `rgba(255,255,255,0.14)` | `rgba(0,0,0,0.18)` | Borde marcado |
| `--t1`   | `#f4f4f5` | `#18181b` | Texto primario |
| `--t2`   | `#a1a1aa` | `#52525b` | Texto secundario |
| `--t3`   | `#52525b` | `#a1a1aa` | Texto muted (se invierte entre modos) |

**Acento (indigo, hue 260):** `--a` = `oklch(0.62 0.22 260)` dark / `oklch(0.52 0.22 260)`
light. Hue declarado en `--ah: 260`. Derivadas: `--a-dim` (alpha 0.12 dark / 0.10 light)
para fondos tenues, `--a-bdr` (alpha 0.30 dark / 0.25 light) para bordes.

**Estados (OKLch, cada uno con variantes `-dim` y `-bdr`):**

- **OK / exito / online:** `--ok` = `oklch(0.72 0.17 145)` dark / `oklch(0.55 0.17 145)`
  light — verde, hue 145.
- **ERROR / grave / peligro:** `--err` = `oklch(0.60 0.22 20)` dark / `oklch(0.50 0.22 20)`
  light — rojo, hue 20.
- **WARNING / alerta / bloqueado:** `--wrn` = `oklch(0.78 0.18 65)` dark /
  `oklch(0.60 0.18 65)` light — dorado, hue 65.

OKLch permite recolorizacion perceptual: cambiar un hue propaga a todas sus dependencias.
Hay alias legacy mantenidos (`--accent`, `--text-primary`, `--border`, etc.) para
compatibilidad — al escribir codigo nuevo preferir los tokens base (`--a`, `--t1`, `--bdr`).

**Color fuera de sistema (deuda):** el LOGIN usa una paleta cian/teal hardcodeada
**hue ~200** (`oklch(0.67 0.16 200)` y un gradiente cian) que no corresponde al indigo del
panel. Algunas sub-pestanas usan tints Tailwind ad-hoc. Ver `deuda-de-diseno.md`.

## Tipografia (style.css:12-13)

- Texto: `--font: 'Plus Jakarta Sans', 'Segoe UI', system-ui, sans-serif`
- Mono: `--mono: 'JetBrains Mono', 'Consolas', monospace`
- **Sin CDN ni @font-face de Google Fonts**: si las fuentes preferidas no estan instaladas,
  cae a fuentes de sistema (Segoe UI / Consolas). Robusto offline; no introducir un CDN de
  fuentes para "arreglarlo".
- Jerarquia aproximada (verificar tamanos exactos en el estado actual antes de tratarlos
  como tokens): titulos grandes ~28px (login) / ~22px (secciones); body ~13px; labels
  uppercase ~10.5-11px. Pesos 700-800 en headings, 600 enfasis, 500 normal.

## Espaciado

- **No hay escala base en variables.** Conviven valores hardcodeados en CSS
  (28/24/20/16/14/12/10px) con la escala de Tailwind (`gap-*`, `p-*`, `px-*`, `py-*`) en el
  markup. Cambiar el ritmo hoy obliga a tocar ambos. Mejora futura: definir `--sp-1..6`.

## Radios (style.css:9-11)

- `--r: 13px` (defecto), `--r-sm: 8px` (inputs), `--r-xs: 6px` (badges/botones chicos).
- **No existe `--r-lg`/`--r-xl`**: cards bento (20px), app-window (20px), login-card (24px),
  boton login (12px) estan hardcodeados. Mejora futura: agregar `--r-lg: 20px`,
  `--r-xl: 24px`.

## Sombras y efectos

- `--sha`: `0 2px 8px rgba(0,0,0,.40), 0 0 0 1px rgba(255,255,255,.04)` (dark) /
  `0 1px 6px rgba(0,0,0,.08), 0 0 0 1px rgba(0,0,0,.06)` (light).
- Glassmorphism en login (`backdrop-filter: blur(16px)`), sidebar y bento-cards
  translucidos. Usar con mesura: el glass de fondo translucido cuesta repintado.

## Componentes (estado actual)

Hay una capa real de componentes en CSS (no es todo utilidades Tailwind sueltas):

- **Layout "ventana flotante":** `.dashboard` fija a pantalla completa con `.app-window`
  (radius 20px) centrada; `.sidebar` fijo de 240px + `.app-main-area`. Drawer movil con
  hamburguesa a <=768px.
- **Navegacion:** `.nav-item` / `.nav-item-active` (con barra de acento a la izquierda) y
  `.subtab-btn` / `.subtab-btn.active` para sub-pestanas. (Algunas subtabs hoy se togglean
  con utilidades Tailwind en vez de la clase `.active` — ver deuda.)
- **Tarjetas de estadistica:** `.stat-card` (+ variantes `.stat-ok`, `.stat-wrn`) con franja
  de acento y numero grande.
- **Tarjetas de terminal:** `.terminal-card` con punto de estado (`.tc-status-dot`), badge
  (`.terminal-estado` / `.estado-activo|bloqueado|offline`) y botones de accion
  (`.btn-card-bloquear|desbloquear|apagar|eliminar-terminal`). El estado hoy esta repartido
  en clases inconexas — ver deuda.
- **Tablas:** `.tabla` (+ `.tabla-scroll-wrap`, `thead th` sticky, `.th-sortable`,
  `.status-pill`, `.cell-pc`, `.cell-tag`, `.empty-msg`). Existe ademas una vista de cards
  (`.hc-*`) usada en historial — reutilizable para responsive.
- **Modales:** `.modal-overlay` + `.modal-content` (+ `.modal-bloqueo-content`),
  `.modal-input`, `.modal-select`, botones `.btn-cancelar` / `.btn-confirmar` /
  `.btn-confirmar-rojo`.
- **Consolas de log:** `.console-box` / `.console-header` / `.console-item` con clases
  semanticas (`.ok`, `.error`, `.warning`, `.info`, `.muted`).
- **Paginacion:** `.pag-btn` / `.pag-btn-activo` / `.pag-dots`.
- **Filtros/toolbar:** `.input-filtro`, `.select-filtro`, `.btn-periodo`,
  `.btn-finalizar|limpiar|bloquear-todas`.

Objetivo de consistencia: estos patrones existen pero estan duplicados/divergentes entre
secciones. Al tocar una seccion, converger hacia primitivos unicos (`.btn`, `.input`,
`.badge`, `.table`, `.card-section`) en vez de sumar otra variante.

## Resiliencia offline (critico)

Entrega de assets en `index.html` (`<head>`):

- **Tailwind por CDN:** `https://cdn.tailwindcss.com` (compilador JIT en navegador) + config
  inline (`darkMode:'class'`, `zinc.950='#09090b'`).
- **Phosphor por CDN:** `https://unpkg.com/@phosphor-icons/web` (97 usos `ph ph-*` en el
  HTML).
- **Fuentes:** sin CDN (fallback de sistema).

Que pasa sin internet (LAN aislada):

- **Sobrevive:** `style.css`, todas las CSS variables y las clases locales (`.sidebar`,
  `.bento-card`, `.terminal-card`, tablas, login, etc.). El login es mayormente CSS propio.
- **Se rompe:** todas las utilidades Tailwind del markup (`flex`, `grid`, `gap-*`, `p-*`,
  `rounded-*`, `inline-flex`, `items-center`...) dejan de aplicarse -> layouts rotos donde
  dependen de Tailwind; y **todos los iconos Phosphor desaparecen** -> botones solo-icono
  quedan ciegos.

Direccion de arreglo (deuda #1): compilar Tailwind a un `.css` estatico local y servir
Phosphor como webfont/SVG local en `/static/`. Hasta entonces, no sumar dependencias de CDN.
