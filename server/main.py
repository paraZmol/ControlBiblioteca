# main.py - Punto de entrada del servidor FastAPI
import logging
import os
import socket
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
import asyncio

import hashlib
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete
from database import init_db, async_session
from models import AlumnoMaestro, Usuario, Terminal, Sesion, Facultad, Escuela
from auth_service import hashear_password, obtener_usuario_actual
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


# ── Funciones helper para get_or_create ────────────────────────────────

async def get_or_create_facultad(db, nombre: str) -> Optional[Facultad]:
    """Busca una Facultad por nombre, si no existe la crea."""
    if not nombre or not nombre.strip():
        return None
    
    nombre_limpio = nombre.strip()
    res = await db.execute(select(Facultad).where(Facultad.nombre == nombre_limpio))
    facultad = res.scalar_one_or_none()
    
    if not facultad:
        facultad = Facultad(nombre=nombre_limpio)
        db.add(facultad)
        await db.flush()
        logger.info(f"[SGA] Nueva Facultad creada: {nombre_limpio}")
    
    return facultad


async def get_or_create_escuela(db, nombre: str, facultad: Facultad) -> Optional[Escuela]:
    """Busca una Escuela por nombre y facultad, si no existe la crea."""
    if not nombre or not nombre.strip() or not facultad:
        return None
    
    nombre_limpio = nombre.strip()
    res = await db.execute(
        select(Escuela).where(
            (Escuela.nombre == nombre_limpio) & (Escuela.id_facultad == facultad.id)
        )
    )
    escuela = res.scalar_one_or_none()
    
    if not escuela:
        escuela = Escuela(nombre=nombre_limpio, id_facultad=facultad.id)
        db.add(escuela)
        await db.flush()
        logger.info(f"[SGA] Nueva Escuela creada: {nombre_limpio} (Facultad: {facultad.nombre})")
    
    return escuela


async def consultar_sga(dni: str) -> Optional[dict]:
    """Consulta la API SGA UNASAM. Retorna {codigo, nombres, apellidos, escuela, facultad} o None."""
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
    """Migra el esquema para agregar columnas de fuerza bruta si faltan."""
    from sqlalchemy import text
    try:
        async with async_session() as db:
            try:
                await db.execute(text("ALTER TABLE usuarios ADD COLUMN intentos_fallidos INT DEFAULT 0"))
            except Exception: pass
            try:
                await db.execute(text("ALTER TABLE usuarios ADD COLUMN bloqueado_hasta DATETIME NULL"))
            except Exception: pass
            try:
                await db.execute(text("ALTER TABLE terminales ADD COLUMN intentos_fallidos INT DEFAULT 0"))
            except Exception: pass
            try:
                await db.execute(text("ALTER TABLE terminales ADD COLUMN bloqueada_hasta DATETIME NULL"))
            except Exception: pass
            try:
                await db.execute(text("ALTER TABLE catalogo_motivos ADD COLUMN activo BOOLEAN DEFAULT TRUE"))
            except Exception: pass
            await db.commit()
    except Exception as e:
        logger.error(f"Error en migraciones: {e}")


async def _limpiar_sesiones_fantasma():
    """Cancela sesiones que llevan más de 10s sin confirmación del cliente."""
    from sqlalchemy import text
    while True:
        await asyncio.sleep(10)
        try:
            async with async_session() as db:
                from sqlalchemy import text as _text
                db_url = os.getenv("DATABASE_URL", "")
                if "sqlite" in db_url:
                    query = _text("SELECT id, id_terminal FROM sesiones WHERE estado='activa' AND confirmada=0 AND (strftime('%s','now') - strftime('%s', hora_entrada)) > 10")
                else:
                    query = _text("SELECT id, id_terminal FROM sesiones WHERE estado='activa' AND confirmada=0 AND TIMESTAMPDIFF(SECOND, hora_entrada, NOW()) > 10")
                res = await db.execute(query)
                fantasmas = res.fetchall()
                for row in fantasmas:
                    sesion_id, terminal_id_db = row
                    res_s = await db.execute(select(Sesion).where(Sesion.id == sesion_id))
                    s = res_s.scalar_one_or_none()
                    if s:
                        s.activa = False
                        s.motivo_cierre = "sin_confirmacion"
                        s.hora_salida = datetime.now().replace(tzinfo=None)
                        res_t = await db.execute(select(Terminal).where(Terminal.id == s.id_terminal))
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


async def _limpiar_sesiones_arranque():
    """Limpia las sesiones que quedaron abiertas por cortes de luz (Startup Cleaner)."""
    async with async_session() as db:
        res = await db.execute(select(Sesion).where(Sesion.estado == 'activa'))
        sesiones = res.scalars().all()
        cerradas = 0
        for s in sesiones:
            # Si no hay hora de salida ni confirmación o si simplemente quedó colgada
            s.estado = 'cerrada'
            s.hora_salida = s.hora_entrada
            s.motivo_cierre = 'cierre_apagón'
            cerradas += 1
        if cerradas > 0:
            await db.commit()
            logger.warning(f"[STARTUP] Se cerraron {cerradas} sesiones fantasma que quedaron abiertas por un apagón.")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Eventos de inicio y cierre del servidor."""
    await init_db()
    await _migrar_columnas()
    logger.info("Base de datos inicializada")
    await _limpiar_sesiones_arranque()

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


# ── Endpoint de configuración de roles ────────────────────────────────

@app.get("/api/config/nivel2-hash")
async def nivel2_hash(admin: Usuario = Depends(obtener_usuario_actual)):
    """Devuelve el SHA-256 de la contraseña Nivel 2 (nunca la clave en claro).
    Requiere autenticación JWT válida.
    """
    raw = os.getenv("PASS_NIVEL2", "")
    if not raw:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="PASS_NIVEL2 no configurada en el servidor")
    h = hashlib.sha256(raw.encode()).hexdigest()
    return {"hash": h}


# ── Endpoint de limpieza y mantenimiento ───────────────────────────

@app.post("/api/limpiar-todo")
async def limpiar_todo(
    admin: Usuario = Depends(obtener_usuario_actual)
):
    """Limpia todas las sesiones, resetea terminales y desconecta todo.
    
    Solo administradores pueden ejecutar esta operación.
    Requiere autenticación JWT válida con rol='admin'.
    """
    if admin.rol != "admin":
        logger.warning(f"[SEGURIDAD] Usuario '{admin.username}' ({admin.rol}) intentó ejecutar LIMPIAR-TODO sin autorización")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo administradores pueden ejecutar esta operación")
    
    try:
        logger.warning(f"[LIMPIEZA] Iniciado por administrador '{admin.username}' desde {admin.username}")
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

        logger.warning(f"[LIMPIEZA] Operación completada exitosamente por admin '{admin.username}'")
        return {"estado": "ok", "mensaje": "Sistema limpiado completamente", "ejecutado_por": admin.username}
    except Exception as e:
        logger.error(f"[LIMPIEZA] Error durante limpieza: {e}")
        return {"estado": "error", "mensaje": str(e)}, 500


# ── Helpers internos WebSocket ─────────────────────────────────────

async def _buscar_terminal(db, nombre: str, ip: str):
    """Busca Terminal por nombre_red primero, luego por IP como fallback."""
    res = await db.execute(select(Terminal).where(Terminal.nombre_red == nombre))
    t = res.scalar_one_or_none()
    if not t:
        res2 = await db.execute(select(Terminal).where(Terminal.ip == ip))
        t = res2.scalar_one_or_none()
    return t


def _cerrar_sesion(sesion: Sesion, motivo: str):
    """Marca una sesión como cerrada con la hora actual del servidor."""
    ahora = datetime.now().replace(tzinfo=None)
    sesion.hora_salida   = ahora
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
                nombre_red=f"Terminal-{terminal_ip}",
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
                    res = await db.execute(select(Terminal).where(Terminal.nombre_red == hostname))
                    t = res.scalar_one_or_none()

                    res2 = await db.execute(select(Terminal).where(Terminal.ip == terminal_ip))
                    t_by_ip = res2.scalar_one_or_none()

                    if t:
                        t.ip = terminal_ip
                        t.estado = "bloqueado"
                        t.ultima_conexion = datetime.utcnow()
                        if t_by_ip and t_by_ip.id != t.id:
                            await db.delete(t_by_ip)
                            logger.info(f"[WS] Registro temporal '{t_by_ip.nombre_red}' eliminado (duplicado de '{hostname}')")
                        logger.info(f"[WS] Terminal '{hostname}' actualizada con IP={terminal_ip}")
                    elif t_by_ip:
                        t_by_ip.nombre_red = hostname
                        t_by_ip.estado = "bloqueado"
                        t_by_ip.ultima_conexion = datetime.utcnow()
                        logger.info(f"[WS] Terminal IP={terminal_ip} sincronizada con nombre '{hostname}'")
                    else:
                        db.add(Terminal(
                            nombre_red=hostname,
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
                motivo_id = data.get("motivo_id")
                if motivo_id is not None:
                    try:
                        motivo_id = int(motivo_id)
                    except ValueError:
                        motivo_id = None
                logger.info(f"[WS] {terminal_id} login_request: codigo={codigo!r} razon={razon!r} motivo_id={motivo_id}")

                async with async_session() as db:
                    from datetime import timedelta
                    t = await _buscar_terminal(db, terminal_id, terminal_ip)
                    
                    if t and t.bloqueada_hasta and t.bloqueada_hasta > datetime.now():
                        faltan = int((t.bloqueada_hasta - datetime.now()).total_seconds() / 60) + 1
                        await websocket.send_json({"tipo": "login_rechazado", "motivo": f"Terminal bloqueada por seguridad. Intente de nuevo en {faltan} minutos"})
                        continue

                    if not codigo or not codigo.isdigit() or len(codigo) != 8:
                        if t:
                            t.intentos_fallidos += 1
                            if t.intentos_fallidos >= 3:
                                t.bloqueada_hasta = datetime.now() + timedelta(minutes=5)
                            await db.commit()
                        logger.warning(f"[WS] {terminal_id} DNI con formato invalido: {codigo!r}")
                        await websocket.send_json({"tipo": "login_rechazado", "motivo": "El DNI debe tener exactamente 8 digitos"})
                        continue

                    # ── Capa 1: alumnos_maestro (fuente primaria, respuesta instantánea) ──
                    res_m = await db.execute(select(AlumnoMaestro).where(AlumnoMaestro.dni == codigo))
                    maestro = res_m.scalar_one_or_none()

                    if maestro:
                        partes = maestro.nombre.split()
                        if len(partes) >= 3:
                            nombres_m   = " ".join(partes[:len(partes)-2])
                            apellidos_m = " ".join(partes[len(partes)-2:])
                        elif len(partes) == 2:
                            nombres_m, apellidos_m = partes[0], partes[1]
                        else:
                            nombres_m, apellidos_m = maestro.nombre, ""
                        datos_alumno = {
                            "codigo":    maestro.codigo or codigo,
                            "dni":       maestro.dni,
                            "nombres":   nombres_m,
                            "apellidos": apellidos_m,
                        }
                        logger.info(f"[MAESTRO] Alumno encontrado: {maestro.nombre} | DNI={codigo}")
                    else:
                        # ── Solo BD local — sin SGA ──
                        if t:
                            t.intentos_fallidos += 1
                            if t.intentos_fallidos >= 3:
                                t.bloqueada_hasta = datetime.now() + timedelta(minutes=5)
                            await db.commit()
                        logger.warning(f"[WS] {terminal_id} DNI={codigo} no en maestro — acceso denegado")
                        await websocket.send_json({"tipo": "login_rechazado", "motivo": "Usuario no registrado. Acerquese al modulo para tramitar su carnet de biblioteca"})
                        continue

                    # Obtener registro maestro confirmado para FK de sesión
                    res_fk = await db.execute(select(AlumnoMaestro).where(AlumnoMaestro.dni == datos_alumno["dni"]))
                    alumno = res_fk.scalar_one_or_none()
                    if alumno is None:
                        alumno = AlumnoMaestro(
                            dni=datos_alumno["dni"],
                            nombre=f"{datos_alumno['nombres']} {datos_alumno['apellidos']}",
                            codigo=datos_alumno["codigo"],
                        )
                        db.add(alumno)
                        await db.flush()

                    logger.info(f"[WS] {terminal_id} alumno OK: {datos_alumno['nombres']} {datos_alumno['apellidos']} | DNI={codigo}")

                    # ── Sesión única: cerrar sesión activa previa del mismo alumno ──
                    res_dup = await db.execute(
                        select(Sesion).where(
                            Sesion.dni_alumno == alumno.dni,
                            Sesion.estado     == "activa",
                        )
                    )
                    sesiones_previas = res_dup.scalars().all()
                    for sp in sesiones_previas:
                        _cerrar_sesion(sp, "desplazado_por_nuevo_login")
                        # Notificar la terminal anterior que fue desplazada
                        res_t_prev = await db.execute(select(Terminal).where(Terminal.id == sp.id_terminal))
                        t_prev = res_t_prev.scalar_one_or_none()
                        if t_prev:
                            t_prev.estado = "bloqueado"
                            await manager.forzar_cierre_sesion(t_prev.nombre_red)
                            logger.warning(f"[WS] Sesión duplicada cerrada: alumno {alumno.dni} en {t_prev.nombre_red}")

                    t = await _buscar_terminal(db, terminal_id, terminal_ip)

                    if t:
                        t.intentos_fallidos = 0
                        t.bloqueada_hasta = None
                        sesion = Sesion(
                            dni_alumno  = alumno.dni,
                            id_terminal = t.id,
                            razon_uso   = razon or None,
                            motivo_id   = motivo_id,
                            fecha_uso   = datetime.now().date(),
                        )
                        t.estado = "activo"
                        db.add(sesion)
                    await db.commit()
                    logger.info(f"[WS] {terminal_id} sesión registrada en DB (razon={razon!r})")

                    nombre_display = f"{datos_alumno['nombres']} {datos_alumno['apellidos']}"
                    logger.info(f"[WS] {terminal_id} enviando 'desbloquear' al kiosco...")
                    await manager.desbloquear_terminal(terminal_id, {
                        "codigo":    datos_alumno["codigo"],
                        "nombres":   datos_alumno["nombres"],
                        "apellidos": datos_alumno["apellidos"],
                    })
                    logger.info(f"[WS] {terminal_id} respuesta enviada OK")
                    await manager.notificar_evento(f"🟢 ENTRADA: {nombre_display} en {terminal_id}", "login")
                    await manager.enviar_log("activity", f"👤 Acceso: {nombre_display} en {terminal_id}")

                await manager.notificar_admins()

            elif tipo == "unlock_confirmed":
                async with async_session() as db:
                    t = await _buscar_terminal(db, terminal_id, terminal_ip)
                    if t:
                        res_s = await db.execute(
                            select(Sesion).where(Sesion.id_terminal == t.id, Sesion.estado == "activa", Sesion.confirmada == False)
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
                            select(Sesion).where(Sesion.id_terminal == t.id, Sesion.estado == "activa")
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
                        select(Sesion).where(Sesion.id_terminal == t.id, Sesion.estado == "activa")
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
                    res = await db.execute(select(Terminal).where(Terminal.nombre_red == tid))
                    t = res.scalar_one_or_none()
                    if not t:
                        res2 = await db.execute(select(Terminal).where(Terminal.ip == target))
                        t = res2.scalar_one_or_none()
                    if t:
                        t.estado = "bloqueado"
                        res_s = await db.execute(
                            select(Sesion).where(Sesion.id_terminal == t.id, Sesion.estado == "activa")
                        )
                        sesion_activa = res_s.scalar_one_or_none()
                        if sesion_activa:
                            ahora_bloqueo = datetime.now().replace(tzinfo=None)
                            sesion_activa.hora_salida   = ahora_bloqueo
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
                    res_a = await db.execute(select(AlumnoMaestro).where(AlumnoMaestro.dni == dni_param))
                    alumno = res_a.scalar_one_or_none()
                    if alumno is None:
                        res_a2 = await db.execute(select(AlumnoMaestro).where(AlumnoMaestro.codigo == dni_param))
                        alumno = res_a2.scalar_one_or_none()

                    if alumno is None:
                        logger.warning(f"[WS-Admin] DNI={dni_param} no en maestro — acceso denegado")
                        await websocket.send_json({"tipo": "error", "motivo": f"El DNI {dni_param} no esta registrado en la base de datos local"})
                        continue

                    if alumno is None:
                        await websocket.send_json({"tipo": "error", "motivo": f"Error: El DNI {dni_param} no existe"})
                        continue

                    partes = alumno.nombre.split()
                    nombres_a   = " ".join(partes[:max(1, len(partes)-2)])
                    apellidos_a = " ".join(partes[max(1, len(partes)-2):])

                    ok = await manager.desbloquear_terminal(tid, {
                        "codigo":    alumno.codigo,
                        "nombres":   nombres_a,
                        "apellidos": apellidos_a,
                    })
                    if not ok:
                        await websocket.send_json({"tipo": "error", "motivo": f"Terminal '{tid}' no está conectada"})
                        continue

                    res_t = await db.execute(select(Terminal).where(Terminal.nombre_red == tid))
                    terminal_db = res_t.scalar_one_or_none()
                    if not terminal_db:
                        res_t2 = await db.execute(select(Terminal).where(Terminal.ip == target))
                        terminal_db = res_t2.scalar_one_or_none()
                    if terminal_db:
                        sesion = Sesion(
                            dni_alumno  = alumno.dni,
                            id_terminal = terminal_db.id,
                            fecha_uso   = datetime.now().date(),
                            razon_uso   = razon_uso,
                            confirmada  = False,
                        )
                        terminal_db.estado = "activo"
                        db.add(sesion)
                        await db.commit()
                        logger.info(f"[WS-Admin] Sesión creada id={sesion.id} para {alumno.nombre}")
                    await websocket.send_json({"tipo": "ok", "mensaje": f"Terminal {tid} desbloqueada para {alumno.nombre}"})
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
                    res_todas = await db.execute(select(Sesion).where(Sesion.estado == "activa"))
                    sesiones_activas = res_todas.scalars().all()
                    cerradas = 0
                    for sesion_activa in sesiones_activas:
                        res_t = await db.execute(select(Terminal).where(Terminal.id == sesion_activa.id_terminal))
                        t_sesion = res_t.scalar_one_or_none()
                        if t_sesion and (t_sesion.nombre_red in ids_conectados or t_sesion.ip in ips_conectadas):
                            sesion_activa.hora_salida   = ahora_bloqueo_todas
                            sesion_activa.activa        = False
                            sesion_activa.motivo_cierre = "bloqueo_admin"
                            cerradas += 1
                    logger.info(f"[WS-Admin] bloqueo_todas: {cerradas} sesión(es) cerrada(s) (solo conectadas)")

                    res_terms = await db.execute(select(Terminal))
                    for t in res_terms.scalars().all():
                        if t.nombre_red in ids_conectados or t.ip in ips_conectadas:
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
    import uvicorn, json as _json
    _host = "0.0.0.0"
    _port = 8000
    try:
        _cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
        with open(_cfg_path, encoding="utf-8") as _f:
            _cfg = _json.load(_f)
        _port = int(_cfg.get("network", {}).get("port", 8000))
    except Exception:
        pass
    uvicorn.run("main:app", host=_host, port=_port, reload=False)
