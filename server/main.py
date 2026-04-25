# main.py - Punto de entrada del servidor FastAPI
import logging
import os
import socket
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
import asyncio

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete
from database import init_db, async_session
from models import Alumno, Usuario, Terminal, Sesion
from auth_service import hashear_password
from api.endpoints import router as api_router
from core.websocket_manager import manager

# Configurar logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("control")

# ── Detección de IP Real ───────────────────────────────────────────

def obtener_ip_local():
    """Obtiene la IP real de la interfaz activa (no 127.0.0.1)."""
    try:
        # Conectar a un socket remoto (8.8.8.8:80) sin enviar datos
        # Esto obtiene la IP que el SO usaría para alcanzar esa red
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        logger.info(f"[IP] IP Local detectada: {ip}")
        return ip
    except Exception as e:
        logger.warning(f"[IP] No se pudo detectar IP (usando fallback): {e}")
        # Fallback: intentar obtener hostname + localhost
        try:
            return socket.gethostbyname(socket.gethostname())
        except:
            return "127.0.0.1"

_IP_LOCAL = obtener_ip_local()

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

        # Extraer código de matrícula y DNI como campos DISTINTOS
        codigo_matricula = str(alumno_data.get("codigo", "")).strip()  # ej: "161.2502.614"
        dni_real         = str(data.get("dni", dni)).strip()           # ej: "71926257"
        if not codigo_matricula:
            codigo_matricula = dni_real  # fallback si el SGA no devuelve código

        logger.info(f"[SGA] Alumno: {nombres} {apellidos} | código={codigo_matricula} | DNI={dni_real}")
        return {
            "codigo":    codigo_matricula,
            "dni":       dni_real,
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


async def _migrar_columnas():
    """Migraciones seguras: agrega columnas nuevas si no existen."""
    from database import engine
    from sqlalchemy import text
    migraciones = [
        ("ALTER TABLE alumnos   ADD COLUMN dni        VARCHAR(20)",  "alumnos.dni"),
        ("ALTER TABLE sesiones  ADD COLUMN confirmada INTEGER DEFAULT 0", "sesiones.confirmada"),
    ]
    async with engine.begin() as conn:
        for sql, nombre in migraciones:
            try:
                await conn.execute(text(sql))
                logger.info(f"[DB] Columna '{nombre}' agregada")
            except Exception:
                pass  # ya existe


async def _limpiar_sesiones_fantasma():
    """Cancela sesiones que llevan más de 10s sin confirmación del cliente."""
    from sqlalchemy import text
    while True:
        await asyncio.sleep(10)
        try:
            async with async_session() as db:
                res = await db.execute(
                    text("SELECT id, terminal_id FROM sesiones WHERE activa=1 AND confirmada=0 AND (julianday('now') - julianday(inicio)) * 86400 > 10")
                )
                fantasmas = res.fetchall()
                for row in fantasmas:
                    sesion_id, terminal_id_db = row
                    res_s = await db.execute(select(Sesion).where(Sesion.id == sesion_id))
                    s = res_s.scalar_one_or_none()
                    if s:
                        s.activa = False
                        s.motivo_cierre = "sin_confirmacion"
                        s.fin = datetime.now().replace(tzinfo=None)
                        # Revertir terminal a bloqueado via ORM
                        res_t = await db.execute(select(Terminal).where(Terminal.id == s.terminal_id))
                        t = res_t.scalar_one_or_none()
                        if t:
                            t.estado = "bloqueado"
                        logger.warning(f"[FANTASMA] Sesión #{sesion_id} cancelada por falta de confirmación")
                if fantasmas:
                    await db.commit()
                    await manager.notificar_admins()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[FANTASMA] Error en limpieza: {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Eventos de inicio y cierre del servidor."""
    await init_db()
    await _migrar_columnas()
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

    tarea_limpieza = asyncio.create_task(_limpiar_sesiones_fantasma())
    yield
    tarea_limpieza.cancel()
    logger.info("Servidor detenido")


# Crear aplicación
app = FastAPI(
    title="Control Biblioteca UNASAM",
    description="Sistema de bloqueo de terminales y gestión centralizada",
    version="1.0.0",
    lifespan=lifespan
)

# CORS para panel admin (restringir origenes en produccion via env)
_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost,http://127.0.0.1").split(",")
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
admin_path = os.path.join(os.path.dirname(__file__), "..", "admin")
if os.path.exists(admin_path):
    app.mount("/admin", StaticFiles(directory=admin_path, html=True), name="admin")


# ── Endpoint de información del servidor ───────────────────────────

@app.get("/api/server-info")
async def server_info():
    """Devuelve información del servidor: IP local y puerto."""
    return {
        "ip": _IP_LOCAL,
        "port": 8000,
        "ws_url": f"ws://{_IP_LOCAL}:8000/ws/admin",
        "timestamp": datetime.utcnow().isoformat()
    }


# ── Endpoint de limpieza y mantenimiento ───────────────────────────

@app.post("/api/limpiar-todo")
async def limpiar_todo():
    """Limpia todas las sesiones, resetea terminales y desconecta todo."""
    try:
        # Desconectar todas las conexiones WebSocket en memoria
        await manager.desconectar_todo()
        logger.info("[LIMPIEZA] Todas las conexiones WebSocket desconectadas")

        # Limpiar BD: sesiones y terminales
        async with async_session() as db:
            # Borrar todas las sesiones
            await db.execute(delete(Sesion))
            await db.commit()
            logger.info("[LIMPIEZA] Todas las sesiones eliminadas")

            # Resetear estado de terminales
            result = await db.execute(select(Terminal))
            for terminal in result.scalars().all():
                terminal.estado = "offline"
                terminal.ultima_conexion = None
            await db.commit()
            logger.info("[LIMPIEZA] Todas las terminales reseteadas a estado 'offline'")

        return {"estado": "ok", "mensaje": "Sistema limpiado completamente"}
    except Exception as e:
        logger.error(f"[LIMPIEZA] Error durante limpieza: {e}")
        return {"estado": "error", "mensaje": str(e)}, 500


# ── Helpers internos WebSocket ─────────────────────────────────────

async def _buscar_terminal(db, nombre: str, ip: str):
    """Busca Terminal por nombre primero, luego por IP como fallback."""
    res = await db.execute(select(Terminal).where(Terminal.nombre == nombre))
    t = res.scalar_one_or_none()
    if not t:
        res2 = await db.execute(select(Terminal).where(Terminal.ip == ip))
        t = res2.scalar_one_or_none()
    return t


def _cerrar_sesion(sesion: Sesion, motivo: str):
    """Marca una sesión como cerrada con la hora actual del servidor."""
    ahora = datetime.now().replace(tzinfo=None)
    sesion.hora_salida   = ahora
    sesion.fin           = ahora
    sesion.activa        = False
    sesion.motivo_cierre = motivo
    return ahora


# ── WebSocket para terminales ───────────────────────────────────────

@app.websocket("/ws/terminal/{terminal_ip}")
async def websocket_terminal(websocket: WebSocket, terminal_ip: str):
    """Conexión WebSocket persistente con cada terminal cliente."""

    # Usar IP como identificador inicial (se actualiza si llega hello con hostname)
    terminal_id = terminal_ip

    await manager.conectar(terminal_id, websocket, ip=terminal_ip)
    logger.info(f"[WS] Terminal conectada: {terminal_id}")
    await manager.notificar_evento(f"Terminal '{terminal_id}' conectada desde {terminal_ip}")
    await manager.enviar_log("activity", f"Terminal '{terminal_id}' conectada desde {terminal_ip}")

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
                data = await asyncio.wait_for(websocket.receive_json(), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning(f"[WS] Timeout 60s sin mensajes de {terminal_id}")
                break
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.debug(f"[WS] Error recibiendo JSON: {e}")
                try:
                    await websocket.send_json({"tipo": "error", "motivo": "Mensaje JSON inválido"})
                except:
                    pass
                continue

            tipo = data.get("tipo", "")
            if not tipo:
                tipo = data.get("type", "") # Soporte para "type" en lugar de "tipo"
            
            logger.info(f"[WS] {terminal_id} → tipo={tipo!r}")

            if tipo == "error_report":
                msg = data.get("message", "Error sin detalle")
                logger.error(f"[WS-CLIENT-ERROR] {terminal_id}: {msg}")
                await manager.enviar_log("error", f"PC: {terminal_id} - {msg}")
                continue

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

                # Sincronizar DB: nombre (hostname) ↔ IP de forma atómica
                async with async_session() as db:
                    # Buscar registro canónico por hostname
                    res = await db.execute(select(Terminal).where(Terminal.nombre == hostname))
                    t = res.scalar_one_or_none()

                    # Buscar registro por IP (puede ser un temporal "Terminal-{ip}" o el mismo)
                    res2 = await db.execute(select(Terminal).where(Terminal.ip == terminal_ip))
                    t_by_ip = res2.scalar_one_or_none()

                    if t:
                        # Ya existe por nombre — actualizar IP y limpiar duplicado por IP si es distinto
                        t.ip = terminal_ip
                        t.estado = "bloqueado"
                        t.ultima_conexion = datetime.utcnow()
                        if t_by_ip and t_by_ip.id != t.id:
                            # Eliminar el registro temporal creado por IP antes del hello
                            await db.delete(t_by_ip)
                            logger.info(f"[WS] Registro temporal '{t_by_ip.nombre}' eliminado (duplicado de '{hostname}')")
                        logger.info(f"[WS] Terminal '{hostname}' actualizada con IP={terminal_ip}")
                    elif t_by_ip:
                        # Existe por IP — actualizar nombre al hostname real
                        t_by_ip.nombre = hostname
                        t_by_ip.estado = "bloqueado"
                        t_by_ip.ultima_conexion = datetime.utcnow()
                        logger.info(f"[WS] Terminal IP={terminal_ip} sincronizada con nombre '{hostname}'")
                    else:
                        # Nueva terminal — registrar con datos completos
                        db.add(Terminal(
                            nombre=hostname,
                            ip=terminal_ip,
                            estado="bloqueado",
                            ultima_conexion=datetime.utcnow()
                        ))
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
                    logger.info(f"[WS] {terminal_id} buscando alumno DNI={codigo} en DB...")
                    # Buscar por DNI primero, luego por código de matrícula como fallback
                    res_a = await db.execute(select(Alumno).where(Alumno.dni == codigo))
                    alumno = res_a.scalar_one_or_none()
                    if alumno is None:
                        res_a2 = await db.execute(select(Alumno).where(Alumno.codigo == codigo))
                        alumno = res_a2.scalar_one_or_none()

                    # ── Fallback SGA: si no está en DB, consultamos la API externa ──
                    if alumno is None:
                        logger.info(f"[WS] {terminal_id} alumno DNI={codigo} no en DB → consultando SGA...")
                        await websocket.send_json({"tipo": "info", "motivo": "Verificando en sistema universitario..."})
                        sga = await consultar_sga(codigo)
                        if sga:
                            logger.info(f"[SGA] Registrando: {sga['nombres']} {sga['apellidos']} | código={sga['codigo']} | DNI={sga['dni']}")
                            alumno = Alumno(
                                codigo    = sga["codigo"],
                                dni       = sga["dni"],
                                nombres   = sga["nombres"],
                                apellidos = sga["apellidos"],
                                escuela   = sga["escuela"],
                                facultad  = sga.get("facultad", ""),
                                habilitado= True,
                            )
                            db.add(alumno)
                            await db.flush()
                            logger.info(f"[SGA] Alumno registrado: código={alumno.codigo} DNI={alumno.dni} id={alumno.id}")
                        else:
                            logger.warning(f"[WS] {terminal_id} alumno DNI={codigo} no encontrado en DB ni en SGA")
                            await websocket.send_json({"tipo": "login_rechazado", "motivo": "Alumno no registrado en el sistema"})
                            continue

                    if not alumno.habilitado:
                        logger.warning(f"[WS] {terminal_id} alumno DNI={codigo} no habilitado")
                        await websocket.send_json({"tipo": "login_rechazado", "motivo": "Alumno no habilitado para usar la biblioteca"})
                        continue

                    logger.info(f"[WS] {terminal_id} alumno OK: {alumno.nombres} {alumno.apellidos} | código={alumno.codigo} | DNI={alumno.dni}")

                    t = await _buscar_terminal(db, terminal_id, terminal_ip)

                    if t:
                        sesion = Sesion(
                            alumno_id   = alumno.id,
                            terminal_id = t.id,
                            razon_uso   = razon or None,
                            dni         = alumno.dni or alumno.codigo,
                            facultad    = alumno.facultad or "",
                            escuela     = alumno.escuela or "",
                            fecha_uso   = datetime.now().date(),
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
                    await manager.notificar_evento(f"🟢 ENTRADA: {alumno.nombres} {alumno.apellidos} en {terminal_id}", "login")
                    await manager.enviar_log("activity", f"👤 Acceso: {alumno.nombres} {alumno.apellidos} en {terminal_id}")

                await manager.notificar_admins()

            elif tipo == "unlock_confirmed":
                # El cliente C# confirmó que el desbloqueo fue exitoso
                async with async_session() as db:
                    t = await _buscar_terminal(db, terminal_id, terminal_ip)
                    if t:
                        res_s = await db.execute(
                            select(Sesion).where(Sesion.terminal_id == t.id, Sesion.activa == True, Sesion.confirmada == False)
                        )
                        sesion = res_s.scalar_one_or_none()
                        if sesion:
                            sesion.confirmada = True
                            await db.commit()
                            logger.info(f"[WS] Sesión #{sesion.id} confirmada por {terminal_id}")
                            await manager.notificar_evento(f"✅ Desbloqueo confirmado en {terminal_id}", "login")
                await manager.notificar_admins()

            elif tipo == "logout":
                logger.info(f"[WS] {terminal_id} logout recibido")
                async with async_session() as db:
                    t = await _buscar_terminal(db, terminal_id, terminal_ip)
                    if t:
                        res_s = await db.execute(
                            select(Sesion).where(Sesion.terminal_id == t.id, Sesion.activa == True)
                        )
                        sesion = res_s.scalar_one_or_none()
                        if sesion:
                            ahora_logout = _cerrar_sesion(sesion, "logout")
                            await db.commit()
                            logger.info(f"[WS] {terminal_id} sesión cerrada: {ahora_logout.strftime('%I:%M:%S %p')}")
                            await manager.notificar_evento(f"SALIDA: Terminal {terminal_id} (manual logout)", "logout")
                await manager.bloquear_terminal(terminal_id)
                await manager.notificar_admins()

    except WebSocketDisconnect:
        logger.info(f"[WS] Terminal desconectada (WebSocketDisconnect): {terminal_id}")
    except Exception as exc:
        logger.error(f"[WS] Error inesperado en {terminal_id}: {exc}", exc_info=True)
    finally:
        # Limpieza garantizada: se ejecuta en desconexión normal, timeout, apagado o error
        manager.desconectar(terminal_id)
        try:
            async with async_session() as db:
                t = await _buscar_terminal(db, terminal_id, terminal_ip)
                if t:
                    t.estado = "offline"
                    res_s = await db.execute(
                        select(Sesion).where(Sesion.terminal_id == t.id, Sesion.activa == True)
                    )
                    sesion = res_s.scalar_one_or_none()
                    if sesion:
                        ahora = _cerrar_sesion(sesion, "desconexion_red")
                        logger.info(f"[WS] Sesión cerrada por desconexión en {terminal_id}: {ahora.strftime('%I:%M:%S %p')}")
                    await db.commit()
            await manager.notificar_evento(f"⚠️ Terminal '{terminal_id}' perdió conexión", "offline")
            await manager.notificar_admins()
        except Exception as cleanup_exc:
            logger.error(f"[WS] Error en cleanup de {terminal_id}: {cleanup_exc}")


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
                    for k, v in manager.terminal_ips.items():
                        if v == target:
                            tid = k
                            break
                ok = await manager.bloquear_terminal(tid)
                # Acción atómica: bloquear terminal + cerrar sesión activa en una transacción
                async with async_session() as db:
                    res = await db.execute(select(Terminal).where(Terminal.nombre == tid))
                    t = res.scalar_one_or_none()
                    if not t:
                        res2 = await db.execute(select(Terminal).where(Terminal.ip == target))
                        t = res2.scalar_one_or_none()
                    if t:
                        t.estado = "bloqueado"
                        res_s = await db.execute(
                            select(Sesion).where(Sesion.terminal_id == t.id, Sesion.activa == True)
                        )
                        sesion_activa = res_s.scalar_one_or_none()
                        if sesion_activa:
                            ahora_bloqueo = datetime.now().replace(tzinfo=None)
                            sesion_activa.hora_salida   = ahora_bloqueo
                            sesion_activa.fin           = ahora_bloqueo
                            sesion_activa.activa        = False
                            sesion_activa.motivo_cierre = "bloqueo_admin"
                            logger.info(f"[WS-Admin] Sesión cerrada por bloqueo admin en {tid}: {ahora_bloqueo.strftime('%I:%M:%S %p')}")
                        await db.commit()
                msg = f"Terminal {tid} bloqueada" if ok else f"Terminal {tid} no conectada (BD actualizada)"
                await websocket.send_json({"tipo": "ok", "mensaje": msg})
                await manager.notificar_admins()

            elif tipo == "desbloquear_terminal":
                target    = str(data.get("ip", "")).strip()
                dni_param = str(data.get("dni", "") or data.get("codigo", "")).strip()
                razon_uso = str(data.get("razon_uso", "")).strip() or None
                if not target or not dni_param:
                    await websocket.send_json({"tipo": "error", "motivo": "Identificador o DNI inválido"})
                    continue
                # Resolver terminal_id desde IP o nombre
                tid = target
                if tid not in manager.conexiones_activas:
                    for k, v in manager.terminal_ips.items():
                        if v == target:
                            tid = k
                            break
                async with async_session() as db:
                    # PASO 1: Buscar alumno por DNI, luego por código de matrícula
                    res_a = await db.execute(select(Alumno).where(Alumno.dni == dni_param))
                    alumno = res_a.scalar_one_or_none()
                    if alumno is None:
                        res_a2 = await db.execute(select(Alumno).where(Alumno.codigo == dni_param))
                        alumno = res_a2.scalar_one_or_none()

                    # PASO 1b: Fallback a la API SGA si no está en BD local
                    if alumno is None:
                        logger.info(f"[WS-Admin] DNI={dni_param} no en BD local → consultando SGA...")
                        await websocket.send_json({"tipo": "info", "motivo": f"Verificando DNI {dni_param} en la UNASAM..."})
                        sga = await consultar_sga(dni_param)
                        if sga:
                            alumno = Alumno(
                                codigo    = sga["codigo"],
                                dni       = sga["dni"],
                                nombres   = sga["nombres"],
                                apellidos = sga["apellidos"],
                                escuela   = sga["escuela"],
                                facultad  = sga.get("facultad", ""),
                                habilitado= True,
                            )
                            db.add(alumno)
                            await db.flush()
                            logger.info(f"[WS-Admin] Alumno registrado desde SGA: {alumno.nombres} {alumno.apellidos} | código={alumno.codigo}")
                        else:
                            await websocket.send_json({"tipo": "error", "motivo": f"Error: El DNI {dni_param} no existe en el sistema de la UNASAM"})
                            continue

                    # PASO 2: Validar habilitación
                    if alumno is None:
                        await websocket.send_json({"tipo": "error", "motivo": f"Error: El DNI {dni_param} no existe en el sistema de la UNASAM"})
                        continue
                    if not alumno.habilitado:
                        await websocket.send_json({"tipo": "error", "motivo": f"Alumno '{alumno.nombres} {alumno.apellidos}' no está habilitado"})
                        continue
                    # PASO 3: Verificar terminal conectada ANTES de crear sesión
                    ok = await manager.desbloquear_terminal(tid, {
                        "codigo": alumno.codigo,
                        "nombres": alumno.nombres,
                        "apellidos": alumno.apellidos
                    })
                    if not ok:
                        await websocket.send_json({"tipo": "error", "motivo": f"Terminal '{tid}' no está conectada"})
                        continue
                    # PASO 4: Solo crear sesión si el comando WS llegó a la terminal
                    res_t = await db.execute(select(Terminal).where(Terminal.nombre == tid))
                    terminal_db = res_t.scalar_one_or_none()
                    if not terminal_db:
                        res_t2 = await db.execute(select(Terminal).where(Terminal.ip == target))
                        terminal_db = res_t2.scalar_one_or_none()
                    if terminal_db:
                        sesion = Sesion(
                            alumno_id   = alumno.id,
                            terminal_id = terminal_db.id,
                            dni         = alumno.dni or alumno.codigo,
                            facultad    = alumno.facultad or "",
                            escuela     = alumno.escuela or "",
                            fecha_uso   = datetime.now().date(),
                            razon_uso   = razon_uso,
                            confirmada  = False,  # pendiente hasta unlock_confirmed
                        )
                        terminal_db.estado = "activo"
                        db.add(sesion)
                        await db.commit()
                        logger.info(f"[WS-Admin] Sesión creada id={sesion.id} para {alumno.nombres}, esperando confirmación")
                    await websocket.send_json({"tipo": "ok", "mensaje": f"Terminal {tid} desbloqueada para {alumno.nombres} {alumno.apellidos}"})
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
                ahora_bloqueo_todas = datetime.now().replace(tzinfo=None)
                # IPs y nombres de terminales con conexión WS activa en este momento
                ids_conectados = set(manager.conexiones_activas.keys())
                ips_conectadas = set(manager.terminal_ips.values())

                async with async_session() as db:
                    # Cerrar solo las sesiones de terminales actualmente conectadas
                    res_todas = await db.execute(select(Sesion).where(Sesion.activa == True))
                    sesiones_activas = res_todas.scalars().all()
                    cerradas = 0
                    for sesion_activa in sesiones_activas:
                        # Verificar que la terminal de esta sesión esté conectada
                        res_t = await db.execute(select(Terminal).where(Terminal.id == sesion_activa.terminal_id))
                        t_sesion = res_t.scalar_one_or_none()
                        if t_sesion and (t_sesion.nombre in ids_conectados or t_sesion.ip in ips_conectadas):
                            sesion_activa.hora_salida   = ahora_bloqueo_todas
                            sesion_activa.fin           = ahora_bloqueo_todas
                            sesion_activa.activa        = False
                            sesion_activa.motivo_cierre = "bloqueo_admin"
                            cerradas += 1
                    logger.info(f"[WS-Admin] bloqueo_todas: {cerradas} sesión(es) cerrada(s) (solo conectadas)")

                    # Marcar como bloqueadas SOLO las terminales con WS activo
                    res_terms = await db.execute(select(Terminal))
                    for t in res_terms.scalars().all():
                        if t.nombre in ids_conectados or t.ip in ips_conectadas:
                            t.estado = "bloqueado"
                        # Las offline/desconectadas conservan su estado actual
                    await db.commit()

                # Enviar comando "bloquear" solo a los kioscos conectados
                await manager.bloquear_todas()
                await manager.notificar_evento(f"🔒 BLOQUEO GLOBAL: {cerradas} sesión(es) cerrada(s) ({len(ids_conectados)} terminal(es) conectada(s))", "warning")
                await websocket.send_json({"tipo": "ok", "mensaje": f"Terminales conectadas bloqueadas ({cerradas} sesión(es) cerrada(s))"})
                await manager.notificar_admins()

            logger.info(f"[WS-Admin] comando: {tipo}")

    except WebSocketDisconnect:
        manager.desconectar_admin(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
