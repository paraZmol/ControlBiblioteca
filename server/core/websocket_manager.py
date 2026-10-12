import asyncio
import logging
import time
from datetime import datetime
from fastapi import WebSocket
from typing import Dict, Iterable, List

# Ventana mínima entre eventos de foco aceptados por terminal (segundos). El
# cliente honesto emite cada 30s; esto solo frena ráfagas de un cliente falso.
_FOCO_MIN_INTERVALO = 5.0

# Tiempo máximo por envío. Con cientos de PCs y varios paneles, un socket lento
# (TCP lleno, red caída sin FIN) no puede congelar a los demás: se le corta.
_SEND_TIMEOUT = 2.0

# Códigos de cierre propios (rango 4000-4999 reservado a la aplicación).
CIERRE_REEMPLAZADO = 4009   # la misma PC abrió otra conexión
CIERRE_LENTO = 4010         # el receptor no consumió a tiempo

logger = logging.getLogger("control.ws")


async def _enviar(ws: WebSocket, payload: dict) -> None:
    await asyncio.wait_for(ws.send_json(payload), timeout=_SEND_TIMEOUT)


async def _cerrar_silencioso(ws: WebSocket, code: int, reason: str) -> None:
    """Cierra sin propagar errores ni bloquear (el socket puede estar muerto)."""
    try:
        await asyncio.wait_for(ws.close(code=code, reason=reason), timeout=_SEND_TIMEOUT)
    except Exception:
        pass


class ConnectionManager:
    """Gestiona conexiones de terminales (kioscos) y paneles admin.

    Estado en memoria: el servidor debe correr en UN solo proceso (nunca
    uvicorn --workers N), o cada worker vería solo parte de las PCs."""

    def __init__(self):
        self.conexiones_activas: Dict[str, WebSocket] = {}
        self.terminal_ips: Dict[str, str] = {}
        self._admins: List[WebSocket] = []
        # Programa en foco por terminal (nombre_red -> exe). Estado EFÍMERO en
        # memoria: qué mira el alumno AHORA, para pintarlo en vivo en el mapa.
        # No se persiste. Se limpia al cerrarse la sesión o caer la terminal.
        self.foco_terminales: Dict[str, str] = {}
        # Marca de tiempo del último evento de foco aceptado por terminal, para
        # descartar ráfagas (rate-limit anti-DoS, M1).
        self._foco_last_ts: Dict[str, float] = {}
        # Tareas de cierre en segundo plano (se guarda la referencia para que el
        # GC no las cancele a medio camino).
        self._tareas_cierre: set = set()

    def _cerrar_en_fondo(self, ws: WebSocket, code: int, reason: str) -> None:
        tarea = asyncio.ensure_future(_cerrar_silencioso(ws, code, reason))
        self._tareas_cierre.add(tarea)
        tarea.add_done_callback(self._tareas_cierre.discard)

    # ── Terminales ────────────────────────────────────────────────────

    async def conectar(self, terminal_id: str, websocket: WebSocket, ip: str = ""):
        await websocket.accept()
        # Clave provisional (IP). NO se cierra un socket previo con la misma IP:
        # detrás de NAT varias PCs comparten IP hasta que mandan su 'hello'.
        self.conexiones_activas[terminal_id] = websocket
        if ip:
            self.terminal_ips[terminal_id] = ip
        logger.info(f"Terminal conectada: {terminal_id} (ip={ip})")
        await self.notificar_admins()

    async def actualizar_id(self, old_id: str, new_id: str, ip: str = "",
                            websocket: WebSocket = None):
        """Cambia la clave de una conexión activa (de IP a hostname).

        Si ya había OTRA conexión con ese hostname (la PC reconectó y el socket
        viejo sigue a medio morir, o alguien suplanta el hostname), el socket
        viejo se cierra explícitamente con 4009. Su handler verá en su finally
        que el manager ya apunta a la conexión nueva y no cerrará la sesión ni
        marcará la PC offline."""
        ws = websocket if websocket is not None else self.conexiones_activas.get(old_id)
        if ws is None:
            return
        # Solo se quita la clave vieja si es ESTE socket: con NAT, otra PC pudo
        # ocupar la misma clave IP entretanto.
        if self.conexiones_activas.get(old_id) is ws:
            self.conexiones_activas.pop(old_id, None)
            self.terminal_ips.pop(old_id, None)
        previo = self.conexiones_activas.get(new_id)
        self.conexiones_activas[new_id] = ws
        if ip:
            self.terminal_ips[new_id] = ip
        if previo is not None and previo is not ws:
            logger.warning(f"Terminal {new_id}: conexión previa reemplazada por una nueva (cierre 4009)")
            self._cerrar_en_fondo(previo, CIERRE_REEMPLAZADO, "reemplazado")
        # Migrar el foco a la clave nueva; si no, queda huérfano bajo la clave
        # vieja para siempre (A1). El próximo tick del cliente lo re-emitirá.
        if old_id != new_id:
            foco = self.foco_terminales.pop(old_id, None)
            if foco is not None:
                self.foco_terminales[new_id] = foco
        logger.info(f"Terminal re-identificada: {old_id} -> {new_id}")

    def desconectar(self, terminal_id: str, websocket: WebSocket = None):
        # Con websocket dado, solo se quita si la clave sigue apuntando a él
        # (no sacar de la lista a una conexión nueva de la misma PC).
        if websocket is not None and self.conexiones_activas.get(terminal_id) is not websocket:
            return
        self.conexiones_activas.pop(terminal_id, None)
        self.terminal_ips.pop(terminal_id, None)
        # Limpiar el foco en la FUENTE: todo camino de desconexión pasa por aquí
        # (finally del handler, broadcast a sockets muertos, desconectar_todo),
        # así el foco nunca queda pegado mostrando a un alumno que ya se fue (A2).
        self.foco_terminales.pop(terminal_id, None)
        self._foco_last_ts.pop(terminal_id, None)
        self._foco_last_ts.pop(f"__conn__{terminal_id}", None)
        logger.info(f"Terminal desconectada: {terminal_id}")

    async def enviar_comando(self, terminal_ip: str, comando: dict) -> bool:
        ws = self.conexiones_activas.get(terminal_ip)
        if ws:
            try:
                await _enviar(ws, comando)
            except Exception as e:
                # Socket lento o muerto: su handler hará la limpieza al caer.
                logger.warning(f"Comando {comando.get('tipo')} -> {terminal_ip} falló: {type(e).__name__}")
                return False
            logger.info(f"Comando {comando.get('tipo')} -> {terminal_ip}")
            return True
        logger.warning(f"Terminal {terminal_ip} no conectada")
        return False

    async def bloquear_terminal(self, terminal_ip: str) -> bool:
        return await self.enviar_comando(terminal_ip, {
            "tipo": "bloquear",
            "timestamp": datetime.utcnow().isoformat()
        })

    async def desbloquear_terminal(self, terminal_ip: str, alumno: dict) -> bool:
        return await self.enviar_comando(terminal_ip, {
            "tipo": "desbloquear",
            "alumno": alumno,
            "timestamp": datetime.utcnow().isoformat()
        })

    async def _enviar_a_terminales(self, ids: Iterable[str], mensaje: dict) -> int:
        """Envío masivo EN PARALELO con timeout por PC. Devuelve cuántas lo
        recibieron. Las que fallan se desconectan del manager y se cierran."""
        destinos = [(tid, self.conexiones_activas.get(tid)) for tid in ids]
        destinos = [(tid, ws) for tid, ws in destinos if ws is not None]
        if not destinos:
            return 0
        resultados = await asyncio.gather(
            *(_enviar(ws, mensaje) for _, ws in destinos), return_exceptions=True)
        entregados = 0
        for (tid, ws), r in zip(destinos, resultados):
            if isinstance(r, BaseException):
                logger.warning(f"Envío masivo '{mensaje.get('tipo')}' a {tid} falló ({type(r).__name__}); se desconecta")
                self.desconectar(tid, ws)
                self._cerrar_en_fondo(ws, CIERRE_LENTO, "envio fallido")
            else:
                entregados += 1
        return entregados

    async def bloquear_todas(self, ids: Iterable[str] = None) -> int:
        objetivo = list(self.conexiones_activas) if ids is None else list(ids)
        return await self._enviar_a_terminales(objetivo, {
            "tipo": "bloquear",
            "timestamp": datetime.utcnow().isoformat()
        })

    async def forzar_cierre_sesion(self, terminal_id: str):
        """Envía forzar_cierre_sesion a una terminal específica: vuelve al login."""
        await self.enviar_comando(terminal_id, {
            "tipo": "forzar_cierre_sesion",
            "motivo": "sesion_desplazada",
            "timestamp": datetime.utcnow().isoformat()
        })

    async def forzar_cierre_sesion_todas(self):
        """Envía forzar_cierre_sesion a todas las terminales: vuelven al login sin bloquear."""
        await self._enviar_a_terminales(list(self.conexiones_activas), {
            "tipo": "forzar_cierre_sesion",
            "timestamp": datetime.utcnow().isoformat()
        })

    async def desconectar_todo(self):
        """Cierra todas las conexiones de terminales."""
        items = list(self.conexiones_activas.items())
        await asyncio.gather(*(_cerrar_silencioso(ws, 1000, "limpieza") for _, ws in items))
        for tid, ws in items:
            self.desconectar(tid, ws)
        await self.notificar_admins()

    async def broadcast(self, mensaje: dict) -> int:
        """Difunde a todas las terminales conectadas. Devuelve a cuántas llegó
        realmente (las caídas se descuentan), para que el llamador pueda decidir
        si marcar un mensaje como 'enviado' o reintentar en el próximo ciclo."""
        # Copia (G-6): el dict vivo puede mutar durante los awaits.
        return await self._enviar_a_terminales(list(self.conexiones_activas), mensaje)

    # ── Panel Admin ───────────────────────────────────────────────────

    async def conectar_admin(self, websocket: WebSocket):
        await websocket.accept()
        self._admins.append(websocket)
        logger.info("Panel admin conectado")
        await self._enviar_estado(websocket)

    def desconectar_admin(self, websocket: WebSocket):
        try:
            self._admins.remove(websocket)
        except ValueError:
            return
        logger.info("Panel admin desconectado")

    async def _broadcast_admins(self, payload: dict):
        """Envía payload a todos los admins EN PARALELO, con timeout por admin.
        Un panel lento o colgado se saca de la lista y se cierra: no puede
        frenar el handler de la terminal que originó el evento."""
        admins = list(self._admins)   # copia (G-6)
        if not admins:
            return
        resultados = await asyncio.gather(
            *(_enviar(ws, payload) for ws in admins), return_exceptions=True)
        for ws, r in zip(admins, resultados):
            if isinstance(r, BaseException):
                logger.warning(f"Panel admin lento/caído ({type(r).__name__}); se desconecta")
                self.desconectar_admin(ws)
                self._cerrar_en_fondo(ws, CIERRE_LENTO, "panel lento")

    async def notificar_admins(self):
        if not self._admins:
            return
        await self._broadcast_admins(self._estado_actual())

    async def notificar_evento(self, mensaje: str, nivel: str = "info"):
        """Envía un mensaje de evento a todos los paneles admin."""
        await self._broadcast_admins({
            "tipo": "evento_log",
            "mensaje": mensaje,
            "nivel": nivel,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        })

    async def enviar_log(self, category: str, message: str):
        """Envía un log al panel admin: activity o error."""
        await self._broadcast_admins({
            "tipo": "evento_log",
            "mensaje": message,
            "nivel": category,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        })

    # ── Programa en foco (estado en vivo del mapa) ────────────────────

    async def set_foco(self, nombre_terminal: str, exe: str):
        """Registra el programa en foco de una terminal y lo difunde a los
        admins. Llamar solo con exe ya validado (app conocida).

        Devuelve False si el evento se descartó (rate-limit o sin cambio), True
        si se difundió. Protege contra un cliente falso que spamee foco (M1):
        cada evento aceptado hace un broadcast a TODOS los admins."""
        # Dedup: si el foco no cambió, no re-difundir (evita fan-out inútil).
        if self.foco_terminales.get(nombre_terminal) == exe:
            return False
        # Rate-limit: descartar si llegó demasiado pronto tras el anterior.
        ahora = time.monotonic()
        ultimo = self._foco_last_ts.get(nombre_terminal, 0.0)
        if ahora - ultimo < _FOCO_MIN_INTERVALO:
            return False
        self._foco_last_ts[nombre_terminal] = ahora
        self.foco_terminales[nombre_terminal] = exe
        await self._broadcast_admins({
            "tipo": "foco",
            "nombre_terminal": nombre_terminal,
            "proceso_exe": exe,
        })
        return True

    def foco_permitido_por_conn(self, terminal_id: str) -> bool:
        """Pre-barrera barata por conexión (terminal_id), para cortar ráfagas de
        foco ANTES de abrir sesión de DB (M1). Registra el timestamp de forma
        optimista: dos eventos casi simultáneos se colapsan a uno. El gate fino
        por nombre_red (dedup + límite) sigue en set_foco."""
        ahora = time.monotonic()
        clave = f"__conn__{terminal_id}"
        if ahora - self._foco_last_ts.get(clave, 0.0) < _FOCO_MIN_INTERVALO:
            return False
        self._foco_last_ts[clave] = ahora
        return True

    async def limpiar_foco(self, nombre_terminal: str):
        """Olvida el foco de una terminal (fin de sesión / desconexión) y avisa
        al panel para que borre la línea de la tarjeta."""
        if nombre_terminal in self.foco_terminales:
            self.foco_terminales.pop(nombre_terminal, None)
            await self._broadcast_admins({
                "tipo": "foco",
                "nombre_terminal": nombre_terminal,
                "proceso_exe": "",
            })

    def _estado_actual(self) -> dict:
        return {
            "tipo": "status_update",
            "terminales": list(self.conexiones_activas.keys()),
            "total": len(self.conexiones_activas),
            # Foco vigente, para que un admin recién conectado pinte el estado
            # actual sin esperar al próximo cambio de foco.
            "focos": dict(self.foco_terminales),
        }

    async def _enviar_estado(self, ws: WebSocket):
        await _enviar(ws, self._estado_actual())


manager = ConnectionManager()
