# Checklist de revision — antes de dar por terminado un cambio de UI del panel

Pasar esta lista sobre cualquier diseno nuevo o modificado del panel admin. No es burocracia:
cada item corresponde a una forma real en que el panel se rompe o confunde al operador.

## Tokens y consistencia

- [ ] Usa `var(--...)` para color/radio/sombra; sin hex sueltos nuevos.
- [ ] El acento (`--a`, indigo) se usa solo para el elemento de accion principal, no para
      todo. El verde (`--ok`) es solo estado online/exito.
- [ ] Reusa un componente existente (`.btn`, `.input`, `.tabla`, `.modal-*`, `.stat-card`,
      `.terminal-card`...) antes de crear una variante nueva. Si creaste una, justifica por
      que no servia una existente.
- [ ] Sin emojis. Iconos via Phosphor (`ph ph-*`).

## Modo claro y oscuro

- [ ] Probado en oscuro (defecto) Y en claro (`html` sin `.dark`).
- [ ] El texto mantiene contraste en ambos (recordar que `--t3` se invierte entre modos).
- [ ] Nada hardcodeado en un solo modo (un color que solo se ve bien en dark).

## Estados de la interfaz

- [ ] Estado **cargando** (skeleton o "Cargando..."), no un salto en blanco.
- [ ] Estado **vacio** con mensaje (no una tabla muda cuando no hay datos).
- [ ] Estado **error** VISIBLE para el operador (no solo en consola/log); idealmente con
      que hacer.
- [ ] Estados **hover**, **disabled** y **foco visible por teclado** definidos.
- [ ] Acciones que tardan (WebSocket/fetch) muestran progreso y deshabilitan el control
      mientras corren.

## Accion de operador segura

- [ ] Las acciones destructivas (bloquear, apagar, limpiar, eliminar, importar masivo)
      **confirman antes** de ejecutar.
- [ ] Hay feedback tras la accion, con duracion segun criticidad (error/destructiva mas
      larga que un "ok").
- [ ] Si la accion depende del WebSocket, se valida la conexion **antes** de ofrecerla, y el
      timeout da un mensaje explicito (no cierra el modal en silencio).

## Roles

- [ ] Lo solo-superadmin se oculta por CSS por defecto (fail-closed) y el JS revela; un admin
      nunca ve un control que no le corresponde, ni siquiera por un instante.
- [ ] Al ocultar elementos por rol no quedan huecos: lo que queda reflowa correctamente.

## Responsive

- [ ] Se ve bien en el ancho tipico del encargado y al achicar la ventana.
- [ ] Las tablas densas hacen scroll horizontal limpio o caen a vista de cards en <=768px;
      no se desbordan ni se enciman.
- [ ] La grilla de terminales no queda con columnas estrujadas en pantallas chicas.

## Accesibilidad (minimo AA)

- [ ] Cada input tiene `<label for>` (no solo placeholder).
- [ ] Cada boton solo-icono tiene `aria-label`.
- [ ] Modales con `role="dialog"`/`aria-modal`; mensajes de error con `role="alert"`.
- [ ] Areas clicables >=36-44px.
- [ ] Contraste de texto recalculado si tocaste colores (no asumir).

## Microcopy (espanol)

- [ ] Tono consistente con el resto del panel.
- [ ] Sin jerga tecnica filtrada al usuario (evitar "backdoor", "BD", "endpoint" en textos
      visibles); explicar conceptos propios del sistema cuando hagan falta.
- [ ] Los mensajes de error dicen que paso y, si se puede, que hacer.

## Resiliencia / no regresion

- [ ] No agregaste dependencias de CDN nuevas (CSS, fuentes, iconos, scripts de
      presentacion).
- [ ] El cambio no rompe otras secciones que comparten la clase/estilo que tocaste.
- [ ] Diff acotado y reversible; nada de reescritura masiva sin acordarlo.
