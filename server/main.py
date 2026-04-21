# main.py - Punto de entrada del servidor FastAPI
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from database import init_db, async_session
from models import Alumno, Usuario, Terminal, Sesion
from auth_service import hashear_password
from api.endpoints import router as api_router
from core.websocket_manager import manager

# Configurar logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("control")

# ── API SGA UNASAM ───────────────────────────────────────────────────
_SGA_BASE = os.getenv("SGA_API_URL", "https://sga.unasam.edu.pe/integracion/api/biblioteca/matriculados")
_SGA_TIMEOUT = float(os.getenv("SGA_TIMEOUT_SECONDS", "6"))


async def consultar_sga(dni: str) -> Optional[dict]:
    """Consulta la API SGA UNASAM. Retorna {codigo, nombres, apellidos, escuela} o None."""
    url = f"{_SGA_BASE}/{dni}"
    logger.info(f"[SGA] GET {url}")
    try:
        async with httpx.AsyncClient(timeout=_SGA_TIMEOUT, verify=False) as client:
            resp = await client.get(url)

        logger.info(f"[SGA] HTTP {resp.status_code} para DNI={dni}")
        if resp.status_code != 200:
            return None

        data = resp.json()
        alumno_data = data.get("alumno") if isinstance(data, dict) else None
        if not alumno_data:
            logger.warning(f"[SGA] Respuesta sin campo 'alumno': {data}")
            return None

        # Nombres: el JSON viene en MAYÚSCULAS; .title() maneja unicode (Ñ, Á, É…)
        nombres   = str(alumno_data.get("nombres", "")).strip().title()
        ape_pat   = str(alumno_data.get("apellido_paterno", "")).strip().title()
        ape_mat   = str(alumno_data.get("apellido_materno", "")).strip().title()
        apellidos = f"{ape_pat} {ape_mat}".strip()

        escuela_data = data.get("escuela") or {}
        escuela = str(escuela_data.get("nombre", "") if isinstance(escuela_data, dict) else escuela_data).strip().title()

        facultad_data = data.get("facultad") or {}
        facultad = str(facultad_data.get("nombre", "") if isinstance(facultad_data, dict) else facultad_data).strip().title()

        if not nombres or not apellidos:
            logger.warning(f"[SGA] Campos de nombre vacíos en: {alumno_data}")
            return None

        logger.info(f"[SGA] Alumno encontrado: {nombres} {apellidos} | DNI={dni}")
        return {
            "codigo":    dni,       # DNI es el identificador en nuestra DB
            "nombres":   nombres,
            "apellidos": apellidos,
            "escuela":   escuela,
            "facultad":  facultad,
        }

    except httpx.TimeoutException:
        logger.warning(f"[SGA] Timeout para DNI={dni}")
        return None
    except Exception as exc:
        logger.error(f"[SGA] Error: {exc}")
        return None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Eventos de inicio y cierre del servidor."""
    # Inicio: crear tablas y usuario admin por defecto
    await init_db()
    logger.info("Base de datos inicializada")

    # Crear usuario admin si no existe
    async with async_session() as db:
        result = await db.execute(select(Usuario).where(Usuario.username == "admin"))
        if not result.scalar_one_or_none():
            admin = Usuario(
                username="admin",
                hashed_password=hashear_password("admin123"),
                nombre_completo="Administrador",
                rol="admin"
            )
            db.add(admin)
            await db.commit()
            logger.info("Usuario admin creado (admin/admin123)")

    yield
    logger.info("Servidor detenido")


# Crear aplicación
app = FastAPI(
    title="Control Biblioteca UNASAM",
    description="Sistema de bloqueo de terminales y gestión centralizada",
    version="1.0.0",
    lifespan=lifespan
)

# CORS para panel admin (restringir origenes en produccion via env)
import os as _os
_cors_origins = _os.getenv("CORS_ORIGINS", "http://localhost,http://127.0.0.1").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# Registrar rutas API
app.include_router(api_router)

# Servir archivos estáticos del panel admin
import os
admin_path = os.path.join(os.path.dirname(__file__), "..", "admin")
if os.path.exists(admin_path):
    app.mount("/admin", StaticFiles(directory=admin_path, html=True), name="admin")


# ── WebSocket para terminales ───────────────────────────────────────

@app.websocket("/ws/terminal/{terminal_ip}")
async def websocket_terminal(websocket: WebSocket, terminal_ip: str):
    """Conexión WebSocket persistente con cada terminal cliente."""

    # Usar IP como identificador inicial (se actualiza si llega hello con hostname)
    terminal_id = terminal_ip

    await manager.conectar(terminal_id, websocket, ip=terminal_ip)
    logger.info(f"[WS] Terminal conectada: {terminal_id}")

    # ── Registro automático: INSERT si no existe, UPDATE si ya existe ─
    async with async_session() as db:
        res = await db.execute(select(Terminal).where(Terminal.ip == terminal_ip))
        terminal_db = res.scalar_one_or_none()
        if terminal_db:
            terminal_db.estado = "bloqueado"
            terminal_db.ultima_conexion = datetime.utcnow()
            logger.info(f"[WS] Terminal conocida actualizada: {terminal_ip}")
        else:
            terminal_db = Terminal(
                nombre=f"Terminal-{terminal_ip}",
                ip=terminal_ip,
                estado="bloqueado",
                ultima_conexion=datetime.utcnow()
            )
            db.add(terminal_db)
            logger.info(f"[WS] Nueva terminal registrada en DB: {terminal_ip}")
        await db.commit()

    # Notificar panel admin para que refresque la lista
    await manager.notificar_admins()

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception:
                await websocket.send_json({"tipo": "error", "motivo": "Mensaje JSON inválido"})
                continue

            tipo = data.get("tipo", "")
            logger.info(f"[WS] {terminal_id} → tipo={tipo!r}")

            if tipo == "heartbeat":
                await websocket.send_json({"tipo": "heartbeat_ack"})

            elif tipo == "hello":
                # ── Identificación dinámica por nombre de máquina ──
                hostname = str(data.get("hostname", "")).strip()
                if not hostname:
                    await websocket.send_json({"tipo": "error", "motivo": "hostname vacío en hello"})
                    continue

                old_id = terminal_id
                terminal_id = hostname
                manager.actualizar_id(old_id, terminal_id, ip=terminal_ip)
                logger.info(f"[WS] Terminal re-identificada: {old_id} → {terminal_id} (hostname={hostname})")

                # Actualizar DB: buscar por nombre o crear
                async with async_session() as db:
                    res = await db.execute(select(Terminal).where(Terminal.nombre == hostname))
                    t = res.scalar_one_or_none()
                    if t:
                        # Terminal conocida por nombre — actualizar IP si cambió
                        t.ip = terminal_ip
                        t.estado = "bloqueado"
                        t.ultima_conexion = datetime.utcnow()
                        logger.info(f"[WS] Terminal '{hostname}' actualizada con IP={terminal_ip}")
                    else:
                        # Buscar si existe por IP (registro previo sin hostname)
                        res2 = await db.execute(select(Terminal).where(Terminal.ip == terminal_ip))
                        t2 = res2.scalar_one_or_none()
                        if t2:
                            t2.nombre = hostname
                            t2.estado = "bloqueado"
                            t2.ultima_conexion = datetime.utcnow()
                            logger.info(f"[WS] Terminal IP={terminal_ip} renombrada a '{hostname}'")
                        else:
                            new_t = Terminal(
                                nombre=hostname,
                                ip=terminal_ip,
                                estado="bloqueado",
                                ultima_conexion=datetime.utcnow()
                            )
                            db.add(new_t)
                            logger.info(f"[WS] Nueva terminal '{hostname}' registrada con IP={terminal_ip}")
                    await db.commit()

                await websocket.send_json({"tipo": "hello_ack", "hostname": hostname})
                await manager.notificar_admins()

            elif tipo == "login_request":
                codigo = str(data.get("codigo", "")).strip().upper()
                razon = str(data.get("razon", "")).strip()
                logger.info(f"[WS] {terminal_id} login_request: codigo={codigo!r} razon={razon!r}")

                if not codigo or not codigo.isdigit() or len(codigo) != 8:
                    logger.warning(f"[WS] {terminal_id} DNI con formato invalido: {codigo!r}")
                    await websocket.send_json({"tipo": "login_rechazado", "motivo": "El DNI debe tener exactamente 8 digitos"})
                    continue

                async with async_session() as db:
                    logger.info(f"[WS] {terminal_id} buscando alumno {codigo} en DB...")
                    res_a = await db.execute(select(Alumno).where(Alumno.codigo == codigo))
                    alumno = res_a.scalar_one_or_none()

                    # ── Fallback SGA: si no está en DB, consultamos la API externa ──
                    if alumno is None:
                        logger.info(f"[WS] {terminal_id} alumno {codigo} no en DB → consultando SGA...")
                        await websocket.send_json({"tipo": "info", "motivo": "Verificando en sistema universitario..."})
                        sga = await consultar_sga(codigo)
                        if sga:
                            logger.info(f"[SGA] Registrando nuevo alumno: {sga['nombres']} {sga['apellidos']}")
                            alumno = Alumno(
                                codigo    = sga["codigo"],
                                nombres   = sga["nombres"],
                                apellidos = sga["apellidos"],
                                escuela   = sga["escuela"],
                                facultad  = sga.get("facultad", ""),
                                habilitado= True,
                            )
                            db.add(alumno)
                            await db.flush()   # obtener alumno.id sin cerrar la sesión
                            logger.info(f"[SGA] Alumno {alumno.codigo} registrado en DB con id={alumno.id}")
                        else:
                            logger.warning(f"[WS] {terminal_id} alumno {codigo} no encontrado en DB ni en SGA")
                            await websocket.send_json({"tipo": "login_rechazado", "motivo": "Alumno no registrado en el sistema"})
                            continue

                    if not alumno.habilitado:
                        logger.warning(f"[WS] {terminal_id} alumno {codigo} no habilitado")
                        await websocket.send_json({"tipo": "login_rechazado", "motivo": "Alumno no habilitado para usar la biblioteca"})
                        continue

                    logger.info(f"[WS] {terminal_id} alumno OK: {alumno.nombres} {alumno.apellidos}")

                    # Buscar terminal en DB por nombre (hostname) o por IP como fallback
                    res_t = await db.execute(select(Terminal).where(Terminal.nombre == terminal_id))
                    t = res_t.scalar_one_or_none()
                    if not t:
                        res_t2 = await db.execute(select(Terminal).where(Terminal.ip == terminal_ip))
                        t = res_t2.scalar_one_or_none()

                    if t:
                        sesion = Sesion(
                            alumno_id = alumno.id,
                            terminal_id = t.id,
                            razon_uso = razon or None,
                            dni       = alumno.codigo,
                            facultad  = alumno.facultad or "",
                            escuela   = alumno.escuela or "",
                            fecha_uso = datetime.utcnow().date(),
                        )
                        t.estado = "activo"
                        db.add(sesion)
                    await db.commit()
                    logger.info(f"[WS] {terminal_id} sesión registrada en DB (razon={razon!r})")

                    logger.info(f"[WS] {terminal_id} enviando 'desbloquear' al kiosco...")
                    await manager.desbloquear_terminal(terminal_id, {
                        "codigo":    alumno.codigo,
                        "nombres":   alumno.nombres,
                        "apellidos": alumno.apellidos,
                    })
                    logger.info(f"[WS] {terminal_id} respuesta enviada OK")

                await manager.notificar_admins()

            elif tipo == "logout":
                logger.info(f"[WS] {terminal_id} logout recibido")
                async with async_session() as db:
                    # Buscar terminal por nombre o IP
                    res_t = await db.execute(select(Terminal).where(Terminal.nombre == terminal_id))
                    t = res_t.scalar_one_or_none()
                    if not t:
                        res_t2 = await db.execute(select(Terminal).where(Terminal.ip == terminal_ip))
                        t = res_t2.scalar_one_or_none()
                    if t:
                        res_s = await db.execute(
                            select(Sesion).where(Sesion.terminal_id == t.id, Sesion.activa == True)
                        )
                        sesion = res_s.scalar_one_or_none()
                        if sesion:
                            sesion.fin = datetime.utcnow()
                            sesion.activa = False
                            sesion.motivo_cierre = "logout"
                            await db.commit()
                            logger.info(f"[WS] {terminal_id} sesión cerrada en DB")
                await manager.bloquear_terminal(terminal_id)
                await manager.notificar_admins()

    except WebSocketDisconnect:
        logger.info(f"[WS] Terminal desconectada: {terminal_id}")
        manager.desconectar(terminal_id)
        async with async_session() as db:
            # Buscar por nombre o IP
            res = await db.execute(select(Terminal).where(Terminal.nombre == terminal_id))
            t = res.scalar_one_or_none()
            if not t:
                res2 = await db.execute(select(Terminal).where(Terminal.ip == terminal_ip))
                t = res2.scalar_one_or_none()
            if t:
                t.estado = "offline"
                await db.commit()
        await manager.notificar_admins()
    except Exception as exc:
        logger.error(f"[WS] Error inesperado en {terminal_id}: {exc}", exc_info=True)
        manager.desconectar(terminal_id)


# ── WebSocket para panel admin ──────────────────────────────────────

@app.websocket("/ws/admin")
async def websocket_admin(websocket: WebSocket):
    """WebSocket bidireccional: recibe comandos del admin y envía push de estado."""
    await manager.conectar_admin(websocket)
    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception:
                await websocket.send_json({"tipo": "error", "motivo": "JSON inválido"})
                continue

            tipo = data.get("tipo")

            if tipo == "get_status":
                await manager._enviar_estado(websocket)

            elif tipo == "bloquear_terminal":
                target = str(data.get("ip", "")).strip()
                if not target:
                    await websocket.send_json({"tipo": "error", "motivo": "Identificador de terminal requerido"})
                    continue
                # target puede ser hostname o IP — buscar en conexiones
                tid = target
                if tid not in manager.conexiones_activas:
                    # buscar por IP en el mapeo inverso
                    for k, v in manager.terminal_ips.items():
                        if v == target:
                            tid = k
                            break
                ok = await manager.bloquear_terminal(tid)
                async with async_session() as db:
                    res = await db.execute(select(Terminal).where(Terminal.nombre == tid))
                    t = res.scalar_one_or_none()
                    if not t:
                        res2 = await db.execute(select(Terminal).where(Terminal.ip == target))
                        t = res2.scalar_one_or_none()
                    if t:
                        t.estado = "bloqueado"
                        await db.commit()
                msg = f"Terminal {tid} bloqueada" if ok else f"Terminal {tid} no conectada"
                await websocket.send_json({"tipo": "ok", "mensaje": msg})
                await manager.notificar_admins()

            elif tipo == "desbloquear_terminal":
                target = str(data.get("ip", "")).strip()
                codigo = str(data.get("codigo", "")).strip().upper()
                if not target or not codigo or not codigo.isalnum() or len(codigo) > 20:
                    await websocket.send_json({"tipo": "error", "motivo": "Identificador o código inválido"})
                    continue
                # Resolver terminal_id
                tid = target
                if tid not in manager.conexiones_activas:
                    for k, v in manager.terminal_ips.items():
                        if v == target:
                            tid = k
                            break
                async with async_session() as db:
                    from models import Alumno, Sesion
                    from datetime import datetime as dt
                    res_a = await db.execute(select(Alumno).where(Alumno.codigo == codigo))
                    alumno = res_a.scalar_one_or_none()
                    if not alumno or not alumno.habilitado:
                        await websocket.send_json({"tipo": "error", "motivo": "Código inválido o alumno no habilitado"})
                        continue
                    ok = await manager.desbloquear_terminal(tid, {
                        "codigo": alumno.codigo,
                        "nombres": alumno.nombres,
                        "apellidos": alumno.apellidos
                    })
                    if ok:
                        res_t = await db.execute(select(Terminal).where(Terminal.nombre == tid))
                        terminal_db = res_t.scalar_one_or_none()
                        if not terminal_db:
                            res_t2 = await db.execute(select(Terminal).where(Terminal.ip == target))
                            terminal_db = res_t2.scalar_one_or_none()
                        if terminal_db:
                            sesion = Sesion(alumno_id=alumno.id, terminal_id=terminal_db.id)
                            terminal_db.estado = "activo"
                            db.add(sesion)
                            await db.commit()
                        await websocket.send_json({"tipo": "ok", "mensaje": f"Terminal {tid} desbloqueada para {alumno.nombres}"})
                    else:
                        await websocket.send_json({"tipo": "error", "motivo": f"Terminal {tid} no conectada"})
                await manager.notificar_admins()

            elif tipo == "remote_command":
                action = str(data.get("action", "")).strip()
                target = str(data.get("ip", "")).strip()
                if action not in ("shutdown",) or not target:
                    await websocket.send_json({"tipo": "error", "motivo": "Comando remoto inválido"})
                    continue
                # Resolver terminal_id desde IP
                tid = target
                if tid not in manager.conexiones_activas:
                    for k, v in manager.terminal_ips.items():
                        if v == target:
                            tid = k
                            break
                ok = await manager.enviar_comando(tid, {"tipo": "remote_command", "action": action})
                if ok:
                    await websocket.send_json({"tipo": "ok", "mensaje": f"Comando '{action}' enviado a {tid}"})
                else:
                    await websocket.send_json({"tipo": "error", "motivo": f"Terminal {tid} no conectada"})

            elif tipo == "bloquear_todas":
                async with async_session() as db:
                    for tid in list(manager.conexiones_activas):
                        ip = manager.terminal_ips.get(tid, tid)
                        res = await db.execute(select(Terminal).where(Terminal.nombre == tid))
                        t = res.scalar_one_or_none()
                        if not t:
                            res2 = await db.execute(select(Terminal).where(Terminal.ip == ip))
                            t = res2.scalar_one_or_none()
                        if t:
                            t.estado = "bloqueado"
                    await db.commit()
                await websocket.send_json({"tipo": "ok", "mensaje": "Todas las terminales bloqueadas"})
                await manager.notificar_admins()

            logger.info(f"[WS-Admin] comando: {tipo}")

    except WebSocketDisconnect:
        manager.desconectar_admin(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
