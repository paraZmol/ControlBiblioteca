import logging
from datetime import datetime
from fastapi import WebSocket
from typing import Dict, List

logger = logging.getLogger("control.ws")


class ConnectionManager:
    """Gestiona conexiones de terminales (kioscos) y paneles admin."""

    def __init__(self):
        self.conexiones_activas: Dict[str, WebSocket] = {}
        self.terminal_ips: Dict[str, str] = {}
        self._admins: List[WebSocket] = []

    # ── Terminales ────────────────────────────────────────────────────

    async def conectar(self, terminal_id: str, websocket: WebSocket, ip: str = ""):
        await websocket.accept()
        self.conexiones_activas[terminal_id] = websocket
        if ip:
            self.terminal_ips[terminal_id] = ip
        logger.info(f"Terminal conectada: {terminal_id} (ip={ip})")
        await self.notificar_admins()

    def actualizar_id(self, old_id: str, new_id: str, ip: str = ""):
        """Cambia la clave de una conexion activa (de IP a hostname)."""
        ws = self.conexiones_activas.pop(old_id, None)
        if ws:
            self.conexiones_activas[new_id] = ws
        self.terminal_ips.pop(old_id, None)
        if ip:
            self.terminal_ips[new_id] = ip
        logger.info(f"Terminal re-identificada: {old_id} → {new_id}")

    def desconectar(self, terminal_id: str):
        self.conexiones_activas.pop(terminal_id, None)
        self.terminal_ips.pop(terminal_id, None)
        logger.info(f"Terminal desconectada: {terminal_id}")

    async def enviar_comando(self, terminal_ip: str, comando: dict) -> bool:
        ws = self.conexiones_activas.get(terminal_ip)
        if ws:
            await ws.send_json(comando)
            logger.info(f"Comando {comando.get('tipo')} → {terminal_ip}")
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

    async def bloquear_todas(self):
        for ip in list(self.conexiones_activas):
            await self.bloquear_terminal(ip)

    async def forzar_cierre_sesion(self, terminal_id: str):
        """Envía forzar_cierre_sesion a una terminal específica: vuelve al login."""
        await self.enviar_comando(terminal_id, {
            "tipo": "forzar_cierre_sesion",
            "motivo": "sesion_desplazada",
            "timestamp": datetime.utcnow().isoformat()
        })

    async def forzar_cierre_sesion_todas(self):
        """Envía forzar_cierre_sesion a todas las terminales: vuelven al login sin bloquear."""
        for ip in list(self.conexiones_activas):
            await self.enviar_comando(ip, {
                "tipo": "forzar_cierre_sesion",
                "timestamp": datetime.utcnow().isoformat()
            })

    async def desconectar_todo(self):
        """Cierra todas las conexiones de terminales."""
        for tid, ws in list(self.conexiones_activas.items()):
            try:
                await ws.close()
            except Exception:
                pass
            self.desconectar(tid)
        await self.notificar_admins()

    async def broadcast(self, mensaje: dict):
        muertos = []
        for ip, ws in self.conexiones_activas.items():
            try:
                await ws.send_json(mensaje)
            except Exception:
                muertos.append(ip)
        for ip in muertos:
            self.desconectar(ip)

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
            pass
        logger.info("Panel admin desconectado")

    async def _broadcast_admins(self, payload: dict):
        """Envía payload a todos los admins conectados, limpiando los caídos."""
        muertos = []
        for ws in self._admins:
            try:
                await ws.send_json(payload)
            except Exception:
                muertos.append(ws)
        for ws in muertos:
            self.desconectar_admin(ws)

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

    def _estado_actual(self) -> dict:
        return {
            "tipo": "status_update",
            "terminales": list(self.conexiones_activas.keys()),
            "total": len(self.conexiones_activas)
        }

    async def _enviar_estado(self, ws: WebSocket):
        await ws.send_json(self._estado_actual())


manager = ConnectionManager()
