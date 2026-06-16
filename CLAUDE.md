# Guia del proyecto — Control Biblioteca UNASAM

> Este archivo esta intencionalmente acotado al **diseno / UI del panel de administracion
> web** (pedido explicito). No documenta el resto del sistema; para eso ver `README.md` y
> `docs/`.

## Diseno y UI del panel admin (`admin/`)

El diseno es un **requisito de primera clase**, no un retoque final. Antes de tocar
cualquier interfaz del panel — `admin/index.html`, `admin/static/css/style.css`,
`admin/static/js/app.js` — sea crear, redisenar, revisar, criticar o mejorar, actua como
**arquitecto de UI/UX** siguiendo la skill `diseno-panel`
(`.claude/skills/diseno-panel/SKILL.md`). Invocala con `/diseno-panel` o aplica sus
principios directamente. (Esto NO aplica al cliente kiosco WPF: es otra superficie.)

Restricciones DURAS (no negociables):

- **Sin emojis.** Nunca. Iconografia solo con Phosphor (`ph ph-*`) o simbolos ASCII.
- **Interfaz en espanol**, con tono consistente.
- **Los tokens CSS de `style.css` (`:root` / `html:not(.dark)`) son la fuente de verdad**
  del color, los radios y la tipografia. No hardcodear hex sueltos; usar `var(--a)`,
  `var(--ok)`, `var(--sur)`, `var(--t1)`, etc. El acento es **indigo (hue 260)**, NO verde
  (el verde, hue 145, es el estado OK / online).
- **Soportar modo claro y oscuro** en todo lo nuevo.
- **Debe degradar con dignidad sin internet** (el servidor corre en LAN). Hoy Tailwind y los
  iconos Phosphor se cargan por CDN: es deuda conocida (deuda #1 en la skill). No agregar mas
  dependencias externas de presentacion.
- **Roles fail-closed:** lo solo-superadmin se oculta por CSS por defecto y el JS lo revela.
- **Accesibilidad minima AA:** `label for=`, `aria-label` en botones solo-icono, foco
  visible, contraste recalculado al tocar colores.

Sistema de diseno completo, backlog priorizado de deuda y checklist de verificacion: la
skill `diseno-panel` y sus archivos de apoyo (`referencia-sistema-diseno.md`,
`deuda-de-diseno.md`, `checklist-revision.md`).
