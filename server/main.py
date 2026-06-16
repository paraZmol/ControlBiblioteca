# main.py - Punto de entrada del servidor FastAPI
import logging
import os
import re
import socket
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
import asyncio

import hashlib
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete
from database import init_db, async_session
from models import AlumnoMaestro, Usuario, Terminal, Sesion, Facultad, Escuela, Ban, PersonalUniversidad, ActividadLog, Sospecha, MensajeProgramado
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from pydantic import BaseModel as _BaseModel, Field as _Field
from auth_service import hashear_password, obtener_usuario_actual
from api.endpoints import router as api_router
from core.websocket_manager import manager
from core.auditoria import registrar_auditoria
from core import rate_limit

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
# Verificación SSL al consultar el SGA. True por defecto (seguro). Solo se
# desactiva si SGA_SSL_VERIFY es explícitamente "false"/"0"/"no" en .env.
_SGA_VERIFY_SSL = os.getenv("SGA_SSL_VERIFY", "true").strip().lower() not in ("false", "0", "no")

# Contraseña inicial para los usuarios por defecto (solo se usa al crearlos por
# primera vez). Se lee de .env; si no está, cae a un valor temporal y se avisa.
# NUNCA se registra en logs en claro. Cambiar tras el primer login.
_DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "").strip() or "admin123"


# ── B-8: enmascarado de PII para los logs ──────────────────────────────
# Evita escribir DNIs y nombres completos en claro en los logs operativos.
# Para auditoría puntual se puede desactivar con LOG_PII_CLARO=true en .env.
_LOG_PII_CLARO = os.getenv("LOG_PII_CLARO", "false").strip().lower() in ("true", "1", "yes")

def _mask_dni(dni) -> str:
    """71396514 -> ****6514. Conserva los últimos 4 para poder correlacionar."""
    s = str(dni or "")
    if _LOG_PII_CLARO:
        return s
    if len(s) <= 4:
        return "****"
    return "****" + s[-4:]

def _mask_nombre(nombre) -> str:
    """'Juan Carlos Pérez' -> 'J.C.P.'. Solo iniciales."""
    s = str(nombre or "").strip()
    if _LOG_PII_CLARO:
        return s
    if not s:
        return "—"
    iniciales = ".".join(p[0].upper() for p in s.split() if p)
    return (iniciales + ".") if iniciales else "—"


# B-9: complejidad mínima de contraseñas de los usuarios admin/superadmin.
# Devuelve None si es válida, o un mensaje de error describiendo qué falta.
def _validar_complejidad_password(pwd: str) -> Optional[str]:
    if not pwd or len(pwd) < 8:
        return "La contraseña debe tener al menos 8 caracteres"
    if not any(c.isupper() for c in pwd):
        return "La contraseña debe incluir al menos una letra mayúscula"
    if not any(c.isdigit() for c in pwd):
        return "La contraseña debe incluir al menos un número"
    return None


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
        # Verificación SSL ACTIVA por defecto (evita MITM que altere datos del
        # alumno). El certificado del SGA UNASAM es válido. Solo en caso de un
        # problema puntual de certificado se puede desactivar con
        # SGA_SSL_VERIFY=false en .env — NO recomendado en producción.
        async with httpx.AsyncClient(timeout=_SGA_TIMEOUT, verify=_SGA_VERIFY_SSL) as client:
            resp = await client.get(url)

        logger.info(f"[SGA] HTTP {resp.status_code} para DNI={_mask_dni(dni)}")
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

        logger.info(f"[SGA] Alumno: {_mask_nombre(nombres + ' ' + apellidos)} | DNI={_mask_dni(dni_real)}")
        return {
            "codigo":    codigo_matricula,
            "dni":       dni_real,
            "nombres":   nombres,
            "apellidos": apellidos,
            "escuela":   escuela,
            "facultad":  facultad,
        }

    except httpx.TimeoutException:
        logger.warning(f"[SGA] Timeout para DNI={_mask_dni(dni)}")
        return None
    except Exception as exc:
        logger.error(f"[SGA] Error: {exc}")
        return None


async def _scheduler_mensajes():
    """Comprueba periódicamente si hay mensajes programados que enviar.

    BUG-MSG (corregido): antes comparaba la hora con IGUALDAD EXACTA de minuto
    (hora_envio == "HH:MM") y dormía 60s al inicio de cada vuelta. Como cada
    iteración tarda 60s + el tiempo de la query, el reloj del scheduler derivaba
    y el minuto programado podía no compararse NUNCA → el mensaje no se enviaba.
    Ahora:
      - Tick cada 20s (sin desfase acumulado relevante).
      - Dispara cuando la hora programada YA LLEGÓ ese día (hora_envio <= ahora),
        no solo en el minuto exacto.
      - Solo marca un mensaje como enviado si llegó al menos a UNA terminal
        (broadcast devuelve la cantidad de entregas). Si no hay nadie conectado,
        reintenta en el próximo ciclo en vez de perderse.
    """
    while True:
        try:
            ahora = datetime.now()
            hora_actual = ahora.strftime("%H:%M")
            hoy = ahora.date()
            async with async_session() as db:
                # Duración del aviso en el kiosco (configurable; default 60s)
                from models import ConfiguracionKiosco
                _resc = await db.execute(select(ConfiguracionKiosco).limit(1))
                _cfg = _resc.scalar_one_or_none()
                duracion_seg = (_cfg.mensaje_duracion_seg if _cfg and _cfg.mensaje_duracion_seg else 60)

                # ── Cierre diario: activo, no enviado hoy, ya llegó la hora ──
                res_c = await db.execute(
                    select(MensajeProgramado).where(
                        MensajeProgramado.tipo == "cierre",
                        MensajeProgramado.activo == True,
                        MensajeProgramado.hora_envio <= hora_actual,
                    )
                )
                for msg in res_c.scalars().all():
                    ultima = msg.fecha_envio
                    ya_enviado_hoy = ultima is not None and ultima.date() >= hoy
                    if ya_enviado_hoy:
                        continue
                    entregados = await manager.broadcast({
                        "tipo": "mensaje_broadcast",
                        "mensaje": msg.mensaje,
                        "origen": "cierre",
                        "duracion_seg": duracion_seg,
                    })
                    if entregados > 0:
                        msg.fecha_envio = ahora
                        logger.info(f"[MSG] Cierre enviado a {entregados} terminal(es): {msg.mensaje!r}")
                    else:
                        logger.info(f"[MSG] Cierre pendiente (0 terminales conectadas): {msg.mensaje!r}")

                # ── Extras: no enviados, ya llegó su fecha/hora programada ──
                res_e = await db.execute(
                    select(MensajeProgramado).where(
                        MensajeProgramado.tipo == "extra",
                        MensajeProgramado.activo == True,
                        MensajeProgramado.enviado == False,
                        MensajeProgramado.hora_envio <= hora_actual,
                    )
                )
                for msg in res_e.scalars().all():
                    # fecha_envio en un extra = fecha PROGRAMADA (no "última vez").
                    # Sin fecha => se envía hoy. Con fecha => solo cuando ya llegó ese día.
                    fecha_prog = msg.fecha_envio.date() if msg.fecha_envio else hoy
                    if fecha_prog > hoy:
                        continue  # programado para más adelante
                    entregados = await manager.broadcast({
                        "tipo": "mensaje_broadcast",
                        "mensaje": msg.mensaje,
                        "origen": "extra",
                        "duracion_seg": duracion_seg,
                    })
                    if entregados > 0:
                        msg.enviado = True
                        logger.info(f"[MSG] Extra enviado a {entregados} terminal(es): {msg.mensaje!r}")
                    else:
                        logger.info(f"[MSG] Extra pendiente (0 terminales conectadas): {msg.mensaje!r}")

                await db.commit()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[SCHEDULER] Error: {e}")

        try:
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            break


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
            try:
                await db.execute(text("ALTER TABLE configuracion_kiosco ADD COLUMN mensaje_duracion_seg INT DEFAULT 60"))
            except Exception: pass
            # Tabla mensajes_programados
            try:
                await db.execute(text("""
                    CREATE TABLE IF NOT EXISTS mensajes_programados (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        mensaje VARCHAR(500) NOT NULL,
                        hora_envio VARCHAR(5) NOT NULL,
                        tipo VARCHAR(20) NOT NULL DEFAULT 'extra',
                        activo BOOLEAN DEFAULT TRUE,
                        enviado BOOLEAN DEFAULT FALSE,
                        fecha_envio DATETIME NULL
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """))
            except Exception: pass
            # Tabla sospechas
            try:
                await db.execute(text("""
                    CREATE TABLE IF NOT EXISTS sospechas (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        dni_alumno VARCHAR(8) NOT NULL,
                        nombre_alumno VARCHAR(200) NOT NULL,
                        tipo VARCHAR(50) NOT NULL,
                        detalle VARCHAR(600) NOT NULL,
                        fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
                        estado VARCHAR(20) DEFAULT 'pendiente',
                        revisado_por VARCHAR(50) NULL,
                        fecha_revision DATETIME NULL,
                        INDEX idx_sospecha_dni (dni_alumno),
                        FOREIGN KEY (dni_alumno) REFERENCES alumnos_maestro(dni) ON DELETE CASCADE
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """))
            except Exception: pass
            # Columna proceso_exe en actividad_logs (para "ignorar" desde el panel)
            try:
                await db.execute(text("ALTER TABLE actividad_logs ADD COLUMN proceso_exe VARCHAR(150) NULL"))
            except Exception: pass
            # Tabla procesos_ignorados (lista negra editable)
            try:
                await db.execute(text("""
                    CREATE TABLE IF NOT EXISTS procesos_ignorados (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        nombre_exe VARCHAR(150) NOT NULL UNIQUE,
                        agregado_por VARCHAR(100) NULL,
                        fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_proc_ign_exe (nombre_exe)
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """))
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
        # Cerrar sesiones activas colgadas
        res = await db.execute(select(Sesion).where(Sesion.estado == 'activa'))
        sesiones = res.scalars().all()
        cerradas = 0
        for s in sesiones:
            s.estado = 'cerrada'
            s.hora_salida = s.hora_entrada
            s.motivo_cierre = 'cierre_apagón'
            cerradas += 1
        if cerradas > 0:
            logger.warning(f"[STARTUP] Se cerraron {cerradas} sesiones fantasma que quedaron abiertas por un apagón.")

        # Poner TODAS las terminales en offline al arrancar — ninguna puede estar
        # "bloqueada" o "disponible" sin haber establecido primero una nueva conexión WS.
        # Esto evita terminales fantasma que quedaron con estado != offline tras crash.
        res_t = await db.execute(select(Terminal))
        terminales_all = res_t.scalars().all()
        for t in terminales_all:
            t.estado = "offline"
        await db.commit()
        if terminales_all:
            logger.info(f"[STARTUP] {len(terminales_all)} terminal(es) reseteada(s) a 'offline'.")


# Retención de logs de actividad (días). Eventos NORMALES más viejos que esto
# se respaldan a Excel y se borran. Los SOSPECHOSOS NO se tocan (evidencia).
RETENCION_NORMALES_DIAS = 365
# Carpeta donde se guardan los respaldos Excel antes de borrar.
RESPALDOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "respaldos_actividad")


async def _purgar_logs_actividad():
    """Tarea diaria: respalda a Excel y borra los logs NORMALES vencidos.

    Conserva siempre los sospechosos. Antes de borrar, exporta los registros
    a un .xlsx con fecha, para no perder nada históricamente."""
    from datetime import timedelta
    import openpyxl

    while True:
        try:
            await asyncio.sleep(24 * 3600)  # una vez al día

            corte = datetime.now() - timedelta(days=RETENCION_NORMALES_DIAS)
            async with async_session() as db:
                # Solo eventos NORMALES vencidos. Los sospechosos se conservan.
                res = await db.execute(
                    select(ActividadLog).where(
                        ActividadLog.nivel != "sospechoso",
                        ActividadLog.fecha_hora < corte,
                    ).order_by(ActividadLog.fecha_hora)
                )
                vencidos = res.scalars().all()
                if not vencidos:
                    logger.info("[PURGA] No hay logs de actividad vencidos.")
                    continue

                # 1. Respaldar a Excel ANTES de borrar.
                os.makedirs(RESPALDOS_DIR, exist_ok=True)
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.title = "Actividad purgada"
                ws.append(["ID", "Fecha/Hora", "PC", "Alumno", "DNI",
                           "Tipo", "Descripción", "Detalle", "Proceso", "Nivel"])
                for r in vencidos:
                    ws.append([
                        r.id,
                        r.fecha_hora.strftime("%Y-%m-%d %H:%M:%S") if r.fecha_hora else "",
                        r.nombre_terminal, r.nombre_alumno, r.dni_alumno,
                        r.tipo, r.descripcion, r.detalle or "", r.proceso_exe or "", r.nivel,
                    ])
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                ruta = os.path.join(RESPALDOS_DIR, f"actividad_purgada_{stamp}.xlsx")
                wb.save(ruta)

                # 2. Borrar de la tabla.
                ids = [r.id for r in vencidos]
                await db.execute(delete(ActividadLog).where(ActividadLog.id.in_(ids)))
                await db.commit()
                logger.info(f"[PURGA] {len(ids)} log(s) normal(es) respaldado(s) en '{ruta}' y borrado(s). Sospechosos conservados.")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[PURGA] Error en purga de actividad: {e}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Eventos de inicio y cierre del servidor."""
    await init_db()
    await _migrar_columnas()
    logger.info("Base de datos inicializada")
    await _limpiar_sesiones_arranque()

    # Garantizar usuarios por defecto con username correcto según rol
    async with async_session() as db:
        # Superadmin: si existe con username 'admin', renombrarlo a 'superadmin'
        res_sa = await db.execute(select(Usuario).where(Usuario.rol == "superadmin"))
        usuario_sa = res_sa.scalar_one_or_none()
        if usuario_sa:
            if usuario_sa.username != "superadmin":
                usuario_sa.username = "superadmin"
                await db.commit()
                logger.info(f"Usuario superadmin renombrado a 'superadmin'")
        else:
            db.add(Usuario(
                username="superadmin",
                hashed_password=hashear_password(_DEFAULT_ADMIN_PASSWORD),
                nombre_completo="Super Administrador",
                rol="superadmin"
            ))
            await db.commit()
            # NO registrar la contraseña en el log (A-5). Avisar que se use la
            # de .env y se cambie tras el primer inicio.
            logger.warning(
                "Usuario 'superadmin' creado con la contraseña inicial por defecto. "
                "Inicie sesión y CÁMBIELA cuanto antes (configure DEFAULT_ADMIN_PASSWORD en .env)."
            )

        # Admin: crear si no existe con rol 'admin'
        res_a = await db.execute(select(Usuario).where(Usuario.rol == "admin"))
        if not res_a.scalar_one_or_none():
            db.add(Usuario(
                username="admin",
                hashed_password=hashear_password(_DEFAULT_ADMIN_PASSWORD),
                nombre_completo="Administrador",
                rol="admin"
            ))
            await db.commit()
            logger.warning(
                "Usuario 'admin' creado con la contraseña inicial por defecto. "
                "Inicie sesión y CÁMBIELA cuanto antes."
            )

    tarea_limpieza   = asyncio.create_task(_limpiar_sesiones_fantasma())
    tarea_scheduler  = asyncio.create_task(_scheduler_mensajes())
    tarea_purga      = asyncio.create_task(_purgar_logs_actividad())
    yield
    tarea_limpieza.cancel()
    tarea_scheduler.cancel()
    tarea_purga.cancel()
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


# B-4: Headers de seguridad HTTP en todas las respuestas.
# NO se incluye HSTS porque el sistema corre en HTTP (red local, sin TLS):
# HSTS forzaría HTTPS y rompería el acceso. Tampoco una CSP estricta, porque el
# panel carga Tailwind/Phosphor desde CDN y se romperían los estilos.
@app.middleware("http")
async def agregar_headers_seguridad(request, call_next):
    response = await call_next(request)
    # Evita que el panel se embeba en un <iframe> de otro sitio (clickjacking).
    response.headers["X-Frame-Options"] = "DENY"
    # Evita que el navegador "adivine" tipos MIME (MIME sniffing).
    response.headers["X-Content-Type-Options"] = "nosniff"
    # No filtrar la URL completa como referer hacia otros orígenes.
    response.headers["Referrer-Policy"] = "same-origin"
    # Limitar APIs sensibles del navegador que el panel no usa.
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response

# Registrar rutas API
app.include_router(api_router)

# Servir archivos estáticos del panel admin
admin_path = os.path.join(os.path.dirname(__file__), "..", "admin")
if os.path.exists(admin_path):
    app.mount("/admin", StaticFiles(directory=admin_path, html=True), name="admin")


# ── Endpoint de información del servidor ───────────────────────────

@app.get("/api/server-info")
async def server_info(_: Usuario = Depends(obtener_usuario_actual)):
    """Devuelve información del servidor: IP local y puerto.
    F-2: requiere autenticación — el panel lo llama tras el login, y el cliente
    kiosco no lo usa, así que exigir token no rompe ningún flujo."""
    return {
        "ip": _IP_LOCAL,
        "port": 8000,
        "ws_url": f"ws://{_IP_LOCAL}:8000/ws/admin",
        "timestamp": datetime.now().isoformat()
    }


# ── Endpoint de configuración de roles ────────────────────────────────

@app.get("/api/config/nivel2-hash")
async def nivel2_hash(
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db),
):
    """Devuelve el SHA-256 de la contraseña Nivel 2 desde ajustes_sistema."""
    if admin.rol != "superadmin":
        raise HTTPException(status_code=403, detail="Solo superadmin puede ver este recurso")
    from sqlalchemy import text as _text
    res = await db.execute(_text("SELECT valor FROM ajustes_sistema WHERE clave='pass_nivel2_hash'"))
    row = res.fetchone()
    if row:
        return {"hash": row[0]}
    # fallback al .env si aún no se migró
    raw = os.getenv("PASS_NIVEL2", "")
    if not raw:
        raise HTTPException(status_code=503, detail="Contraseña Nivel 2 no configurada")
    return {"hash": hashlib.sha256(raw.encode()).hexdigest()}


class _ActualizarUsuario(_BaseModel):
    rol_objetivo: str        # 'admin' | 'superadmin'
    nuevo_username: str = ""
    nueva_password: str = ""


@app.put("/api/config/usuario")
async def actualizar_usuario(
    datos: _ActualizarUsuario,
    superadmin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db),
):
    """Solo superadmin puede editar usuarios. Admin: username+password. Superadmin: solo password."""
    if superadmin.rol != "superadmin":
        raise HTTPException(status_code=403, detail="Solo superadmin puede editar usuarios")
    if datos.rol_objetivo not in ("admin", "superadmin"):
        raise HTTPException(status_code=400, detail="rol_objetivo inválido")

    res = await db.execute(select(Usuario).where(Usuario.rol == datos.rol_objetivo))
    usuario = res.scalar_one_or_none()
    if not usuario:
        raise HTTPException(status_code=404, detail=f"No se encontró usuario con rol '{datos.rol_objetivo}'")

    # Cambio de username solo para admin
    if datos.nuevo_username:
        if datos.rol_objetivo == "superadmin":
            raise HTTPException(status_code=400, detail="No se puede cambiar el username del superadmin")
        if len(datos.nuevo_username) < 3:
            raise HTTPException(status_code=422, detail="El username debe tener al menos 3 caracteres")
        existe = await db.execute(select(Usuario).where(Usuario.username == datos.nuevo_username))
        if existe.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Ese username ya está en uso")
        usuario.username = datos.nuevo_username

    # Cambio de contraseña para ambos roles
    if datos.nueva_password:
        error_pwd = _validar_complejidad_password(datos.nueva_password)
        if error_pwd:
            raise HTTPException(status_code=422, detail=error_pwd)
        usuario.hashed_password = hashear_password(datos.nueva_password)

    if not datos.nuevo_username and not datos.nueva_password:
        raise HTTPException(status_code=400, detail="Nada que actualizar")

    # Auditoría SIN registrar la contraseña, solo el hecho del cambio
    cambios = []
    if datos.nuevo_username: cambios.append("username")
    if datos.nueva_password: cambios.append("contraseña")
    await registrar_auditoria(superadmin.username, "editar_usuario", rol=superadmin.rol,
                              objetivo=f"usuario rol={datos.rol_objetivo}",
                              detalle="cambió " + " y ".join(cambios), db=db)
    await db.commit()
    return {"mensaje": "Usuario actualizado correctamente"}


class _ConfiguracionKioscoReq(_BaseModel):
    backdoor_modifiers: int
    backdoor_key: int
    backdoor_pin: str = _Field(..., min_length=4, max_length=50)   # F-6: no aceptar PIN vacío/trivial

class _ConfiguracionOfflineReq(_BaseModel):
    offline_modifiers: int
    offline_key: int
    offline_pin: str = _Field(..., min_length=4, max_length=50)    # F-6

@app.get("/api/config/backdoor")
async def obtener_config_backdoor(
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db)
):
    if admin.rol != "superadmin":
        raise HTTPException(status_code=403, detail="Solo el superadmin puede ver la configuración de mantenimiento.")
    from models import ConfiguracionKiosco
    res = await db.execute(select(ConfiguracionKiosco).limit(1))
    cfg = res.scalar_one_or_none()
    if not cfg:
        cfg = ConfiguracionKiosco()
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    return {
        "backdoor_modifiers": cfg.backdoor_modifiers,
        "backdoor_key": cfg.backdoor_key,
        "backdoor_pin": cfg.backdoor_pin
    }

@app.put("/api/config/backdoor")
async def actualizar_config_backdoor(
    datos: _ConfiguracionKioscoReq,
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db)
):
    if admin.rol != "superadmin":
        raise HTTPException(status_code=403, detail="Solo el superadmin puede modificar la configuración de mantenimiento.")
    from models import ConfiguracionKiosco
    res = await db.execute(select(ConfiguracionKiosco).limit(1))
    cfg = res.scalar_one_or_none()
    if not cfg:
        cfg = ConfiguracionKiosco()
        db.add(cfg)
    cfg.backdoor_modifiers = datos.backdoor_modifiers
    cfg.backdoor_key = datos.backdoor_key
    cfg.backdoor_pin = datos.backdoor_pin
    await registrar_auditoria(admin.username, "cambiar_config_backdoor", rol=admin.rol,
                              objetivo="config kiosco", detalle="actualizó PIN/atajo de mantenimiento", db=db)
    await db.commit()
    await manager.broadcast({
        "tipo": "config_backdoor_update",
        "backdoor_modifiers": cfg.backdoor_modifiers,
        "backdoor_key": cfg.backdoor_key,
        "backdoor_pin": cfg.backdoor_pin
    })
    return {"mensaje": "Configuración de mantenimiento actualizada correctamente"}


@app.get("/api/config/offline-pin")
async def obtener_config_offline(
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db)
):
    from models import ConfiguracionKiosco
    res = await db.execute(select(ConfiguracionKiosco).limit(1))
    cfg = res.scalar_one_or_none()
    if not cfg:
        cfg = ConfiguracionKiosco()
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    return {
        "offline_modifiers": cfg.offline_modifiers,
        "offline_key": cfg.offline_key,
        "offline_pin": cfg.offline_pin
    }

@app.put("/api/config/offline-pin")
async def actualizar_config_offline(
    datos: _ConfiguracionOfflineReq,
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db)
):
    from models import ConfiguracionKiosco
    res = await db.execute(select(ConfiguracionKiosco).limit(1))
    cfg = res.scalar_one_or_none()
    if not cfg:
        cfg = ConfiguracionKiosco()
        db.add(cfg)
    # Validar que el PIN offline no sea igual al PIN de mantenimiento
    if datos.offline_pin == cfg.backdoor_pin:
        raise HTTPException(status_code=422, detail="El PIN offline no puede ser igual al PIN de mantenimiento.")
    cfg.offline_modifiers = datos.offline_modifiers
    cfg.offline_key = datos.offline_key
    cfg.offline_pin = datos.offline_pin
    await registrar_auditoria(admin.username, "cambiar_config_offline", rol=admin.rol,
                              objetivo="config kiosco", detalle="actualizó PIN/atajo offline", db=db)
    await db.commit()
    await manager.broadcast({
        "tipo": "config_offline_update",
        "offline_modifiers": cfg.offline_modifiers,
        "offline_key": cfg.offline_key,
        "offline_pin": cfg.offline_pin
    })
    return {"mensaje": "Configuración offline actualizada correctamente"}


# ── Endpoint de limpieza y mantenimiento ───────────────────────────

@app.post("/api/limpiar-todo")
async def limpiar_todo(
    admin: Usuario = Depends(obtener_usuario_actual)
):
    """Limpia todas las sesiones, resetea terminales y desconecta todo.
    
    Solo administradores pueden ejecutar esta operación.
    Requiere autenticación JWT válida con rol='admin'.
    """
    if admin.rol != "superadmin":
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
            await registrar_auditoria(admin.username, "limpiar_todo", rol=admin.rol,
                                      objetivo="todo el sistema",
                                      detalle="borró sesiones y reseteó terminales", db=db)
            await db.commit()
            logger.info("[LIMPIEZA] Todas las terminales reseteadas a estado 'offline'")

        logger.warning(f"[LIMPIEZA] Operación completada exitosamente por admin '{admin.username}'")
        return {"estado": "ok", "mensaje": "Sistema limpiado completamente", "ejecutado_por": admin.username}
    except Exception as e:
        logger.error(f"[LIMPIEZA] Error durante limpieza: {e}")
        # G-10: devolver un status 500 real (antes la tupla daba 200 con cuerpo array)
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"estado": "error", "mensaje": "Error durante la limpieza. Revise los logs del servidor."})


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

    # ── B-2 / E-1: anti-suplantación de terminal ──
    # La IP del path ({terminal_ip}) la controla el cliente y NO es confiable:
    # cualquiera en la red podría poner la IP de otra PC. Usamos como identidad
    # la IP REAL de la conexión TCP (websocket.client.host), que no se puede
    # falsificar en una conexión ya establecida. La IP del path se conserva solo
    # para diagnóstico (algunas PCs multi-interfaz reportan una IP distinta a la
    # de su ruta real al servidor — por eso NO exigimos que coincidan, solo lo
    # registramos). La identidad canónica definitiva sigue siendo el hostname
    # que llega en el mensaje 'hello'.
    ip_path = terminal_ip
    ip_real = (websocket.client.host if websocket.client else "") or terminal_ip
    if ip_real != ip_path:
        logger.info(f"[WS] Terminal: IP path='{ip_path}' difiere de IP real='{ip_real}' (multi-interfaz). Se usa la real.")
    terminal_ip = ip_real  # identidad de confianza para todo el handler

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
            
            logger.info(f"[WS] {terminal_id} -> tipo={tipo!r}")

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

                cliente_desbloqueado = bool(data.get("desbloqueado", False))
                cliente_modo_offline = bool(data.get("modo_offline", False))

                old_id = terminal_id
                terminal_id = hostname
                manager.actualizar_id(old_id, terminal_id, ip=terminal_ip)
                logger.info(f"[WS] Terminal re-identificada: {old_id} -> {terminal_id} (hostname={hostname}, desbloqueado={cliente_desbloqueado})")

                # Sincronizar DB: nombre (hostname) ↔ IP de forma atómica
                async with async_session() as db:
                    # Buscar registro canónico por hostname
                    res = await db.execute(select(Terminal).where(Terminal.nombre_red == hostname))
                    t = res.scalar_one_or_none()

                    res2 = await db.execute(select(Terminal).where(Terminal.ip == terminal_ip))
                    t_by_ip = res2.scalar_one_or_none()

                    # En modo offline el cliente ya tiene sesión activa — no pisar estado.
                    # Terminal usa "activo" (masculino); "activa" es de Sesion.
                    estado_inicial = "activo" if cliente_modo_offline else "bloqueado"

                    if t:
                        t.ip = terminal_ip
                        if not cliente_modo_offline:
                            t.estado = estado_inicial
                        t.ultima_conexion = datetime.utcnow()
                        if t_by_ip and t_by_ip.id != t.id:
                            await db.delete(t_by_ip)
                            logger.info(f"[WS] Registro temporal '{t_by_ip.nombre_red}' eliminado (duplicado de '{hostname}')")
                        logger.info(f"[WS] Terminal '{hostname}' actualizada con IP={terminal_ip}")
                    elif t_by_ip:
                        t_by_ip.nombre_red = hostname
                        if not cliente_modo_offline:
                            t_by_ip.estado = estado_inicial
                        t_by_ip.ultima_conexion = datetime.utcnow()
                        logger.info(f"[WS] Terminal IP={terminal_ip} sincronizada con nombre '{hostname}'")
                    else:
                        db.add(Terminal(
                            nombre_red=hostname,
                            ip=terminal_ip,
                            estado=estado_inicial,
                            ultima_conexion=datetime.utcnow()
                        ))
                        logger.info(f"[WS] Nueva terminal '{hostname}' registrada con IP={terminal_ip}")
                    await db.commit()

                    from models import ConfiguracionKiosco
                    res_cfg = await db.execute(select(ConfiguracionKiosco).limit(1))
                    cfg = res_cfg.scalar_one_or_none()
                    if not cfg:
                        cfg = ConfiguracionKiosco()
                        db.add(cfg)
                        await db.commit()
                        await db.refresh(cfg)
                    backdoor_modifiers = cfg.backdoor_modifiers
                    backdoor_key = cfg.backdoor_key
                    backdoor_pin = cfg.backdoor_pin
                    offline_modifiers = cfg.offline_modifiers
                    offline_key = cfg.offline_key
                    offline_pin = cfg.offline_pin

                await websocket.send_json({
                    "tipo": "hello_ack",
                    "hostname": hostname,
                    "backdoor_modifiers": backdoor_modifiers,
                    "backdoor_key": backdoor_key,
                    "backdoor_pin": backdoor_pin,
                    "offline_modifiers": offline_modifiers,
                    "offline_key": offline_key,
                    "offline_pin": offline_pin
                })

                # ── Si el cliente dice estar desbloqueado, verificar que haya sesión activa ──
                # En modo offline el cliente maneja su propia sincronización — no forzar bloqueo
                if cliente_desbloqueado and not cliente_modo_offline:
                    async with async_session() as db:
                        res_t = await db.execute(select(Terminal).where(Terminal.nombre_red == hostname))
                        t_check = res_t.scalar_one_or_none()
                        sesion_valida = False
                        if t_check:
                            res_s = await db.execute(
                                select(Sesion).where(
                                    Sesion.id_terminal == t_check.id,
                                    Sesion.estado == "activa"
                                )
                            )
                            sesion_valida = res_s.scalar_one_or_none() is not None
                        if not sesion_valida:
                            logger.warning(f"[WS] {hostname} reporta desbloqueado pero sin sesión activa — forzando bloqueo")
                            await manager.bloquear_terminal(terminal_id)

                await manager.notificar_admins()

            elif tipo == "login_request":
                codigo = str(data.get("codigo", "")).strip().upper()
                razon = str(data.get("razon", "")).strip()
                motivo_id = data.get("motivo_id")
                if motivo_id is not None:
                    try:
                        motivo_id = int(motivo_id)
                        if motivo_id <= 0:
                            motivo_id = None
                    except ValueError:
                        motivo_id = None
                logger.info(f"[WS] {terminal_id} login_request: codigo={_mask_dni(codigo)} razon={razon!r} motivo_id={motivo_id}")

                try:
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
                            logger.warning(f"[WS] {terminal_id} DNI con formato invalido: {_mask_dni(codigo)}")
                            await websocket.send_json({"tipo": "login_rechazado", "motivo": "El DNI debe tener exactamente 8 digitos"})
                            continue

                        # ── Verificar ban activo ──
                        res_ban = await db.execute(
                            select(Ban).where(
                                Ban.dni_alumno == codigo,
                                (Ban.fecha_fin == None) | (Ban.fecha_fin > datetime.now())
                            )
                        )
                        ban_activo = res_ban.scalar_one_or_none()
                        if ban_activo:
                            motivo_ban = ban_activo.motivo or "Acceso restringido"
                            fecha_fin_ban = ban_activo.fecha_fin.strftime("%d/%m/%Y") if ban_activo.fecha_fin else "indefinido"
                            logger.warning(f"[WS] {terminal_id} DNI={_mask_dni(codigo)} BANEADO — {motivo_ban}")
                            await websocket.send_json({"tipo": "login_rechazado", "motivo": f"Acceso denegado. {motivo_ban}. Expira: {fecha_fin_ban}"})
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
                            logger.info(f"[MAESTRO] Alumno encontrado: {_mask_nombre(maestro.nombre)} | DNI={_mask_dni(codigo)}")
                        else:
                            # ── Solo BD local — sin SGA ──
                            if t:
                                t.intentos_fallidos += 1
                                if t.intentos_fallidos >= 3:
                                    t.bloqueada_hasta = datetime.now() + timedelta(minutes=5)
                                await db.commit()
                            logger.warning(f"[WS] {terminal_id} DNI={_mask_dni(codigo)} no en maestro — acceso denegado")
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

                        logger.info(f"[WS] {terminal_id} alumno OK: {_mask_nombre(datos_alumno['nombres'] + ' ' + datos_alumno['apellidos'])} | DNI={_mask_dni(codigo)}")

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
                                logger.warning(f"[WS] Sesión duplicada cerrada: alumno {_mask_dni(alumno.dni)} en {t_prev.nombre_red}")

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
                        await manager.notificar_evento(f"ENTRADA: {nombre_display} en {terminal_id}", "login")
                        await manager.enviar_log("activity", f"Acceso: {nombre_display} en {terminal_id}")

                    await manager.notificar_admins()

                except Exception as _exc_login:
                    logger.error(f"[WS] Error en login_request de {terminal_id}: {_exc_login}", exc_info=True)
                    try:
                        await websocket.send_json({"tipo": "login_rechazado", "motivo": "Error interno, intente de nuevo"})
                    except Exception:
                        pass

            elif tipo == "unlock_confirmed":
                # Reintentar brevemente: si el cliente confirma muy rápido, la
                # creación de la sesión (en el WS del admin) puede no haber
                # terminado de hacer commit todavía. Reintentamos unas pocas
                # veces antes de rendirnos.
                confirmada_ok = False
                for _intento in range(5):
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
                                await manager.notificar_evento(f"Desbloqueo confirmado en {terminal_id}", "login")
                                confirmada_ok = True
                                break
                    await asyncio.sleep(0.4)  # esperar a que el commit de la sesión aterrice
                if not confirmada_ok:
                    logger.warning(f"[WS] {terminal_id} confirmó desbloqueo pero no se halló sesión activa sin confirmar")
                await manager.notificar_admins()

            elif tipo == "actividad":
                # Evento de actividad del alumno en la PC (proceso, archivo, comando, navegador)
                async with async_session() as db:
                    t = await _buscar_terminal(db, terminal_id, terminal_ip)
                    if not t:
                        continue
                    # Buscar sesión activa para obtener el alumno
                    res_s = await db.execute(
                        select(Sesion).where(Sesion.id_terminal == t.id, Sesion.estado == "activa")
                    )
                    sesion = res_s.scalar_one_or_none()
                    if not sesion:
                        continue  # sin sesión activa, ignorar

                    res_a = await db.execute(
                        select(AlumnoMaestro).where(AlumnoMaestro.dni == sesion.dni_alumno)
                    )
                    alumno = res_a.scalar_one_or_none()
                    if not alumno:
                        continue

                    tipo_ev    = str(data.get("evento",      "proceso")).strip()
                    descripcion= str(data.get("descripcion", "")).strip()[:300]
                    detalle    = str(data.get("detalle",     "") or "").strip()[:600]
                    nivel      = str(data.get("nivel",       "normal")).strip()
                    proceso_exe= str(data.get("proceso_exe", "") or "").strip()
                    if nivel not in ("normal", "sospechoso"):
                        nivel = "normal"
                    # E-13: whitelist del tipo de evento. El cliente solo emite
                    # estos tres; cualquier otro valor (entrada manipulada) se
                    # normaliza a "proceso" en vez de guardarse tal cual.
                    if tipo_ev not in ("proceso", "comando", "archivo"):
                        tipo_ev = "proceso"
                    # E-13: sanear proceso_exe — solo caracteres válidos de un
                    # nombre de ejecutable (letras, dígitos, . _ - espacio).
                    # Defensa en profundidad: el panel ya escapa HTML al mostrar.
                    if proceso_exe:
                        proceso_exe = re.sub(r"[^\w.\- ]", "", proceso_exe)[:120]

                    # NOTA: ya NO descartamos los procesos ignorados aquí.
                    # Se guarda TODO siempre (evidencia forense intacta); los
                    # ignorados solo se OCULTAN al consultar la vista normal.
                    # Así, al investigar una sospecha, el admin ve el contexto
                    # completo del alumno sin haber perdido nada.

                    log = ActividadLog(
                        id_terminal     = t.id,
                        nombre_terminal = t.nombre_red,
                        dni_alumno      = alumno.dni,
                        nombre_alumno   = alumno.nombre,
                        tipo            = tipo_ev,
                        descripcion     = descripcion,
                        detalle         = detalle or None,
                        proceso_exe     = proceso_exe or None,
                        nivel           = nivel,
                    )
                    db.add(log)

                    # Si es sospechoso, crear sospecha automáticamente
                    if nivel == "sospechoso":
                        sosp = Sospecha(
                            dni_alumno    = alumno.dni,
                            nombre_alumno = alumno.nombre,
                            tipo          = "actividad_sospechosa",
                            detalle       = f"[{t.nombre_red}] {descripcion}" + (f" — {detalle}" if detalle else ""),
                        )
                        db.add(sosp)
                        await db.flush()
                        await manager._broadcast_admins({
                            "tipo":    "sospecha",
                            "nivel":   "alerta",
                            "mensaje": f"ALERTA: {alumno.nombre} ({alumno.dni}) en {t.nombre_red}: {descripcion}",
                        })

                    await db.commit()
                    logger.info(f"[ACT] {t.nombre_red} | {_mask_dni(alumno.dni)} | {tipo_ev} | {nivel} | {descripcion}")

                    # Notificar al panel admin en tiempo real
                    await manager._broadcast_admins({
                        "tipo":            "actividad",
                        "nombre_terminal": t.nombre_red,
                        "dni_alumno":      alumno.dni,
                        "nombre_alumno":   alumno.nombre,
                        "tipo_evento":     tipo_ev,
                        "descripcion":     descripcion,
                        "nivel":           nivel,
                    })

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
            await manager.notificar_evento(f"Terminal '{terminal_id}' perdió conexión", "offline")
            await manager.notificar_admins()
        except Exception as cleanup_exc:
            logger.error(f"[WS] Error en cleanup de {terminal_id}: {cleanup_exc}")


# ── WebSocket para panel admin ──────────────────────────────────────

@app.websocket("/ws/admin")
async def websocket_admin(websocket: WebSocket):
    """WebSocket bidireccional: recibe comandos del admin y envía push de estado."""
    # Aceptar primero (Starlette requiere accept() antes de close() con código custom)
    await websocket.accept()

    # ── Autenticación por MENSAJE INICIAL (A-8) ──
    # El token NO viaja en la URL (quedaría en logs/historial). El cliente debe
    # enviar como PRIMER mensaje: {"tipo": "auth", "token": "<jwt>"}.
    # Se espera ese mensaje con un timeout corto; si no llega o es inválido,
    # se cierra la conexión sin entrar al loop principal.
    try:
        auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
    except (asyncio.TimeoutError, WebSocketDisconnect, Exception):
        await websocket.close(code=4001, reason="Auth requerido")
        return

    token = ""
    if isinstance(auth_msg, dict) and auth_msg.get("tipo") == "auth":
        token = str(auth_msg.get("token", "")).strip()

    if not token:
        await websocket.close(code=4001, reason="Token requerido")
        return
    try:
        from auth_service import SECRET_KEY, ALGORITHM
        from jose import jwt as _jwt
        payload = _jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise ValueError("Token sin subject")
        async with async_session() as db:
            res = await db.execute(select(Usuario).where(Usuario.username == username))
            user = res.scalar_one_or_none()
            if not user or not user.activo:
                raise ValueError("Usuario inactivo o inexistente")
            if user.rol not in ("superadmin", "admin"):
                raise ValueError("Rol insuficiente")
    except Exception as e:
        logger.warning(f"[WS-Admin] Conexión rechazada: {e}")
        await websocket.close(code=4003, reason="Token inválido o expirado")
        return

    # Conexión autenticada — registrar en el manager (ya aceptada arriba).
    # Conservamos la identidad del operador (G-2) para auditar comandos y para
    # restringir las acciones más destructivas a superadmin.
    op_username = user.username
    op_rol      = user.rol
    manager._admins.append(websocket)
    logger.info(f"Panel admin conectado (autenticado): {op_username} [{op_rol}]")
    await manager._enviar_estado(websocket)
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
                        await registrar_auditoria(op_username, "bloquear_terminal", rol=op_rol,
                                                  objetivo=tid, db=db)
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
                        logger.warning(f"[WS-Admin] DNI={_mask_dni(dni_param)} no en maestro — acceso denegado")
                        await websocket.send_json({"tipo": "error", "motivo": f"El DNI {dni_param} no esta registrado en la base de datos local"})
                        continue

                    if alumno is None:
                        await websocket.send_json({"tipo": "error", "motivo": f"Error: El DNI {dni_param} no existe"})
                        continue

                    partes = alumno.nombre.split()
                    nombres_a   = " ".join(partes[:max(1, len(partes)-2)])
                    apellidos_a = " ".join(partes[max(1, len(partes)-2):])

                    # PASO 1: Crear la sesión en la DB ANTES de enviar el desbloqueo.
                    # Con clientes rápidos, el 'unlock_confirmed' puede llegar en
                    # milisegundos; si la sesión aún no existe, su handler no la
                    # encuentra y el limpiador la mata como fantasma. Creándola
                    # primero garantizamos que ya esté disponible para confirmar.
                    res_t = await db.execute(select(Terminal).where(Terminal.nombre_red == tid))
                    terminal_db = res_t.scalar_one_or_none()
                    if not terminal_db:
                        res_t2 = await db.execute(select(Terminal).where(Terminal.ip == target))
                        terminal_db = res_t2.scalar_one_or_none()
                    if not terminal_db:
                        await websocket.send_json({"tipo": "error", "motivo": f"Terminal '{tid}' no registrada"})
                        continue

                    sesion = Sesion(
                        dni_alumno  = alumno.dni,
                        id_terminal = terminal_db.id,
                        fecha_uso   = datetime.now().date(),
                        razon_uso   = razon_uso,
                        confirmada  = False,
                    )
                    terminal_db.estado = "activo"
                    db.add(sesion)
                    await registrar_auditoria(op_username, "desbloquear_terminal", rol=op_rol,
                                              objetivo=tid, detalle=f"DNI {alumno.dni}", db=db)
                    await db.commit()
                    logger.info(f"[WS-Admin] Sesión creada id={sesion.id} para {_mask_nombre(alumno.nombre)}")

                    # PASO 2: Ahora sí, enviar el comando de desbloqueo a la PC.
                    ok = await manager.desbloquear_terminal(tid, {
                        "codigo":    alumno.codigo,
                        "nombres":   nombres_a,
                        "apellidos": apellidos_a,
                    })
                    if not ok:
                        # La PC se desconectó entre crear la sesión y enviar: revertir.
                        sesion.activa        = False
                        sesion.motivo_cierre = "desbloqueo_fallido"
                        sesion.hora_salida   = datetime.now().replace(tzinfo=None)
                        terminal_db.estado   = "bloqueado"
                        await db.commit()
                        await websocket.send_json({"tipo": "error", "motivo": f"Terminal '{tid}' no está conectada"})
                        await manager.notificar_admins()
                        continue

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
                    await registrar_auditoria(op_username, "apagar_terminal", rol=op_rol, objetivo=tid)
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
                    logger.warning(f"[AUDIT] {op_username} [{op_rol}] ejecutó BLOQUEO GLOBAL: {cerradas} sesión(es) cerrada(s) (solo conectadas)")

                    res_terms = await db.execute(select(Terminal))
                    for t in res_terms.scalars().all():
                        if t.nombre_red in ids_conectados or t.ip in ips_conectadas:
                            t.estado = "bloqueado"
                        # Las offline/desconectadas conservan su estado actual
                    await registrar_auditoria(op_username, "bloquear_todas", rol=op_rol,
                                              objetivo="toda la sala",
                                              detalle=f"{cerradas} sesión(es) cerrada(s)", db=db)
                    await db.commit()

                # Enviar comando "bloquear" solo a los kioscos conectados
                await manager.bloquear_todas()
                await manager.notificar_evento(f"BLOQUEO GLOBAL: {cerradas} sesión(es) cerrada(s) ({len(ids_conectados)} terminal(es) conectada(s))", "warning")
                await websocket.send_json({"tipo": "ok", "mensaje": f"Terminales conectadas bloqueadas ({cerradas} sesión(es) cerrada(s))"})
                await manager.notificar_admins()

            logger.info(f"[WS-Admin] comando: {tipo}")

    except WebSocketDisconnect:
        manager.desconectar_admin(websocket)


# ── Mensajes programados ───────────────────────────────────────────

class _MensajeReq(_BaseModel):
    mensaje:    str
    hora_envio: str    # "HH:MM"
    tipo:       str = "extra"   # "cierre" | "extra"
    fecha_envio: str = ""       # "YYYY-MM-DD" para extras, vacío para cierre


class _OfflineSyncReq(_BaseModel):
    dni:         str = _Field(..., max_length=8)
    razon:       str = _Field("", max_length=200)
    hora_inicio: str = _Field("", max_length=25)
    hora_fin:    str = _Field("", max_length=25)
    terminal:    str = _Field("", max_length=120)


@app.post("/api/offline-sync")
async def offline_sync(datos: _OfflineSyncReq, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Recibe una sesión offline al reconectar.
    Verifica si el alumno existe y no está baneado.
    Si todo está bien, registra la sesión en el historial con hora retroactiva.
    No requiere autenticación de admin — lo llama el kiosco directamente.
    Por eso valida formato del DNI y NO crea terminales nuevas aquí (G-1/G-7):
    una sesión offline solo puede asociarse a una terminal que ya conectó antes.
    """
    # Rate limit por IP (mismo límite que /alumnos/validar): corta enumeración
    # de DNIs desde la LAN por respuestas distinguibles (not_found vs ok).
    rate_limit.exigir(request, "kiosco", limite=30, ventana_seg=60)

    dni = datos.dni.strip()

    # Validar formato del DNI (8 dígitos), igual que el login normal — evita
    # inyección de basura y enumeración por payloads arbitrarios.
    if len(dni) != 8 or not dni.isdigit():
        return {"estado": "not_found", "motivo": "DNI inválido."}

    # Verificar existencia
    alumno = await db.get(AlumnoMaestro, dni)
    if alumno is None:
        return {"estado": "not_found", "motivo": "El DNI no está registrado en el sistema."}

    # Verificar ban activo
    res_ban = await db.execute(
        select(Ban).where(
            Ban.dni_alumno == dni,
            (Ban.fecha_fin == None) | (Ban.fecha_fin > datetime.now())
        )
    )
    ban = res_ban.scalar_one_or_none()
    if ban:
        return {"estado": "baneado", "motivo": f"Acceso restringido: {ban.motivo}"}

    # Buscar terminal por nombre. NO se crea aquí (G-1): una sesión offline
    # legítima proviene de una PC que ya estaba registrada. Si no existe, se
    # rechaza para no permitir crear terminales fantasma sin autenticación.
    res_t = await db.execute(select(Terminal).where(Terminal.nombre_red == datos.terminal))
    terminal = res_t.scalar_one_or_none()
    if terminal is None:
        logger.warning(f"[OfflineSync] Terminal desconocida rechazada: {datos.terminal}")
        return {"estado": "terminal_desconocida", "motivo": "Terminal no registrada en el sistema."}

    # Parsear hora de inicio (retroactiva). NOTA sobre hora_fin (G-8): el cliente
    # envía hora_fin al RECONECTAR, pero su intención es CONTINUAR la sesión (que
    # el alumno siga trabajando), no cerrarla. Por eso la sesión se registra como
    # 'activa' y hora_fin se ignora a propósito aquí: la sesión se cerrará por el
    # flujo normal (logout, bloqueo, desconexión) cuando realmente termine.
    try:
        hora_inicio = datetime.strptime(datos.hora_inicio, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        hora_inicio = datetime.now()

    # Registrar sesión activa con hora de entrada retroactiva.
    # fecha_uso usa la fecha del servidor (no del cliente) para que los filtros
    # de historial por día/mes/año funcionen correctamente independiente
    # de la zona horaria del kiosco.
    sesion = Sesion(
        dni_alumno   = dni,
        id_terminal  = terminal.id,
        hora_entrada = hora_inicio,
        razon_uso    = datos.razon,
        estado       = "activa",
        confirmada   = True,
        fecha_uso    = datetime.now().date(),
    )
    db.add(sesion)
    terminal.estado = "activo"   # Terminal usa "activo" (masculino); "activa" es de Sesion
    terminal.ultima_conexion = datetime.now()
    await db.commit()
    await db.refresh(sesion)

    logger.info(f"[OfflineSync] Sesión offline sincronizada: DNI={dni} terminal={datos.terminal} inicio={datos.hora_inicio}")

    # Notificar al kiosco que la sesión fue confirmada — le pasa el nombre del alumno
    # para que la UI se actualice con la bienvenida normal
    await manager.enviar_comando(terminal.ip, {
        "tipo":   "sesion_sync_ok",
        "dni":    dni,
        "nombre": alumno.nombre,
        "razon":  datos.razon,
    })
    await manager.notificar_admins()

    return {"estado": "ok", "motivo": f"Sesión registrada para {alumno.nombre}"}


@app.get("/api/descargar-cliente")
async def descargar_cliente(db: AsyncSession = Depends(get_db)):
    """Sirve el exe del kiosco para auto-actualización. No requiere auth — el kiosco lo llama al arrancar."""
    import pathlib
    from fastapi.responses import FileResponse
    from api.endpoints import _obtener_ruta_distribucion
    ruta = await _obtener_ruta_distribucion(db)
    exe = pathlib.Path(ruta) / "ControlBiblioteca.Client.exe"
    if not exe.exists():
        raise HTTPException(status_code=404, detail=f"Ejecutable no encontrado en {ruta}")
    return FileResponse(
        path=str(exe),
        media_type="application/octet-stream",
        filename="ControlBiblioteca.Client.exe",
    )


@app.get("/api/mensajes")
async def listar_mensajes(
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(MensajeProgramado).order_by(MensajeProgramado.tipo, MensajeProgramado.hora_envio)
    )
    rows = res.scalars().all()
    return [
        {
            "id":         r.id,
            "mensaje":    r.mensaje,
            "hora_envio": r.hora_envio,
            "tipo":       r.tipo,
            "activo":     r.activo,
            "enviado":    r.enviado,
            "fecha_envio": r.fecha_envio.strftime("%Y-%m-%d") if r.fecha_envio else None,
        }
        for r in rows
    ]


class _DuracionMensajeReq(_BaseModel):
    minutos: int = _Field(..., ge=1, le=15)   # UI en minutos; se persiste en segundos


@app.get("/api/mensajes/duracion")
async def obtener_duracion_mensaje(
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db),
):
    from models import ConfiguracionKiosco
    res = await db.execute(select(ConfiguracionKiosco).limit(1))
    cfg = res.scalar_one_or_none()
    seg = (cfg.mensaje_duracion_seg if cfg and cfg.mensaje_duracion_seg else 60)
    return {"minutos": max(1, round(seg / 60)), "segundos": seg}


@app.put("/api/mensajes/duracion")
async def guardar_duracion_mensaje(
    datos: _DuracionMensajeReq,
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db),
):
    from models import ConfiguracionKiosco
    res = await db.execute(select(ConfiguracionKiosco).limit(1))
    cfg = res.scalar_one_or_none()
    if not cfg:
        cfg = ConfiguracionKiosco()
        db.add(cfg)
    cfg.mensaje_duracion_seg = datos.minutos * 60
    await registrar_auditoria(admin.username, "cambiar_duracion_mensaje", rol=admin.rol,
                              objetivo=f"{datos.minutos} min", db=db)
    await db.commit()
    return {"minutos": datos.minutos, "segundos": cfg.mensaje_duracion_seg}


@app.post("/api/mensajes")
async def crear_mensaje(
    datos: _MensajeReq,
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db),
):
    from datetime import date as _date
    import re
    # BUG-7: validar formato Y rango. Antes el regex aceptaba "25:99" (formato
    # correcto pero hora imposible): el mensaje se guardaba pero nunca se enviaba.
    if not re.match(r"^\d{2}:\d{2}$", datos.hora_envio):
        raise HTTPException(status_code=422, detail="hora_envio debe ser HH:MM")
    _h, _m = map(int, datos.hora_envio.split(":"))
    if not (0 <= _h <= 23 and 0 <= _m <= 59):
        raise HTTPException(status_code=422, detail="hora_envio fuera de rango (00:00–23:59)")
    if datos.tipo not in ("cierre", "extra"):
        raise HTTPException(status_code=422, detail="tipo debe ser 'cierre' o 'extra'")
    if not datos.mensaje.strip():
        raise HTTPException(status_code=422, detail="mensaje no puede estar vacío")

    # Para cierre: solo puede existir uno; actualizarlo en lugar de crear otro
    if datos.tipo == "cierre":
        res = await db.execute(select(MensajeProgramado).where(MensajeProgramado.tipo == "cierre"))
        existente = res.scalar_one_or_none()
        if existente:
            # BUG-MSG2: al cambiar la hora hay que RESETEAR fecha_envio. Si quedaba
            # con la marca "enviado hoy" de un disparo anterior, el scheduler veía
            # ya_enviado_hoy=True y NUNCA reenviaba con la hora nueva. Por eso
            # "lo programo para dentro de 2 min y no sale". Al guardar, lo dejamos
            # listo para volver a dispararse hoy.
            existente.mensaje     = datos.mensaje.strip()
            existente.hora_envio  = datos.hora_envio
            existente.activo      = True
            existente.fecha_envio = None
            await registrar_auditoria(admin.username, "editar_mensaje", rol=admin.rol,
                                      objetivo=f"cierre {datos.hora_envio}",
                                      detalle=datos.mensaje.strip()[:120], db=db)
            await db.commit()
            return {"id": existente.id, "mensaje": "Mensaje de cierre actualizado"}

    fecha_dt = None
    if datos.tipo == "extra" and datos.fecha_envio:
        try:
            fecha_dt = datetime.strptime(datos.fecha_envio, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=422, detail="fecha_envio inválida, use YYYY-MM-DD")

    nuevo = MensajeProgramado(
        mensaje    = datos.mensaje.strip(),
        hora_envio = datos.hora_envio,
        tipo       = datos.tipo,
        activo     = True,
        enviado    = False,
        fecha_envio = fecha_dt,
    )
    db.add(nuevo)
    await registrar_auditoria(admin.username, "crear_mensaje", rol=admin.rol,
                              objetivo=f"{datos.tipo} {datos.hora_envio}",
                              detalle=datos.mensaje.strip()[:120], db=db)
    await db.commit()
    await db.refresh(nuevo)
    return {"id": nuevo.id, "mensaje": "Mensaje creado"}


@app.post("/api/mensajes/probar")
async def probar_mensaje(
    datos: _MensajeReq,
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db),
):
    """Envía un mensaje de prueba INMEDIATO a todas las terminales conectadas,
    sin guardarlo ni esperar a la hora programada. Sirve para verificar en campo
    que el aviso se ve en los kioscos. Devuelve a cuántas terminales llegó."""
    texto = (datos.mensaje or "").strip() or "Mensaje de prueba del panel."
    from models import ConfiguracionKiosco
    _resc = await db.execute(select(ConfiguracionKiosco).limit(1))
    _cfg = _resc.scalar_one_or_none()
    duracion_seg = (_cfg.mensaje_duracion_seg if _cfg and _cfg.mensaje_duracion_seg else 60)
    entregados = await manager.broadcast({
        "tipo": "mensaje_broadcast",
        "mensaje": texto,
        "origen": "prueba",
        "duracion_seg": duracion_seg,
    })
    await registrar_auditoria(admin.username, "probar_mensaje", rol=admin.rol,
                              objetivo=f"{entregados} terminal(es)",
                              detalle=texto[:120], db=db)
    await db.commit()
    return {"entregados": entregados}


@app.put("/api/mensajes/{msg_id}/toggle")
async def toggle_mensaje(
    msg_id: int,
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(MensajeProgramado).where(MensajeProgramado.id == msg_id))
    msg = res.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado")
    msg.activo = not msg.activo
    await registrar_auditoria(admin.username, "toggle_mensaje", rol=admin.rol,
                              objetivo=f"{msg.tipo} {msg.hora_envio}",
                              detalle="activado" if msg.activo else "desactivado", db=db)
    await db.commit()
    return {"activo": msg.activo}


@app.delete("/api/mensajes/{msg_id}")
async def eliminar_mensaje(
    msg_id: int,
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(MensajeProgramado).where(MensajeProgramado.id == msg_id))
    msg = res.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado")
    _info = f"{msg.tipo} {msg.hora_envio}"
    await db.delete(msg)
    await registrar_auditoria(admin.username, "eliminar_mensaje", rol=admin.rol,
                              objetivo=_info, db=db)
    await db.commit()
    return {"mensaje": "Eliminado"}


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
