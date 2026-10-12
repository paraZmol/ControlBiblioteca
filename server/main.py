# main.py - Punto de entrada del servidor FastAPI
import logging
import os
import re
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
import asyncio

import hashlib
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from database import init_db, async_session
from models import AlumnoMaestro, Usuario, Terminal, Sesion, Facultad, Escuela, Ban, PersonalUniversidad, ActividadLog, Sospecha, MensajeProgramado
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from pydantic import BaseModel as _BaseModel, Field as _Field
from auth_service import hashear_password, obtener_usuario_actual
from api.endpoints import router as api_router, _ban_activo, _sesion_activa_de_terminal
from api.organizacion import router as organizacion_router
from core.websocket_manager import manager
from core.auditoria import registrar_auditoria
from core import rate_limit

# Configurar logging: consola + archivo rotativo (server/logs/server.log,
# 10 MB x 10). Con cientos de PCs la consola sola no sirve para diagnosticar
# después, y un archivo sin rotar llenaría el disco.
def _configurar_logging():
    from logging.handlers import RotatingFileHandler
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    raiz = logging.getLogger()
    if any(getattr(h, "_control_log", False) for h in raiz.handlers):
        return   # import repetido (reload): no duplicar handlers
    raiz.setLevel(logging.INFO)
    consola = logging.StreamHandler()
    consola.setFormatter(fmt)
    consola._control_log = True
    raiz.addHandler(consola)
    try:
        carpeta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(carpeta, exist_ok=True)
        archivo = RotatingFileHandler(os.path.join(carpeta, "server.log"),
                                      maxBytes=10 * 1024 * 1024, backupCount=10,
                                      encoding="utf-8")
        archivo.setFormatter(fmt)
        archivo._control_log = True
        raiz.addHandler(archivo)
    except Exception as e:   # sin permiso de escritura: seguir solo con consola
        logging.getLogger("control").warning(f"No se pudo abrir server/logs/server.log: {e}")

_configurar_logging()
logger = logging.getLogger("control")


def _limpio(texto, maximo: int = 300) -> str:
    """Sanea texto que viene del cliente antes de loguearlo o reenviarlo:
    sin saltos de línea (evita falsificar líneas de log) y acotado."""
    s = str(texto if texto is not None else "")
    s = s.replace("\r", " ").replace("\n", " ").replace("\x00", "")
    return s[:maximo]


# Hostname de Windows/NetBIOS: letras, dígitos, punto, guion y guion bajo.
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,63}$")

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

# ── Bancos de programas / ruido (caché en memoria) ─────────────────────
# Se consultan en CADA evento de actividad, así que se cachean para no pegarle
# a MySQL en el camino caliente. Refrescar con _refrescar_bancos() al editar.
# Sets de nombre_exe en minúscula.
_BANCO_APPS_SET: set = set()
_BANCO_RUIDO_SET: set = set()

async def _refrescar_bancos(db):
    """Recarga en memoria los sets de banco_apps y banco_ruido."""
    from sqlalchemy import text
    global _BANCO_APPS_SET, _BANCO_RUIDO_SET
    try:
        ra = await db.execute(text("SELECT nombre_exe FROM banco_apps"))
        _BANCO_APPS_SET = {r[0].lower() for r in ra.fetchall() if r[0]}
        rr = await db.execute(text("SELECT nombre_exe FROM banco_ruido"))
        _BANCO_RUIDO_SET = {r[0].lower() for r in rr.fetchall() if r[0]}
        logger.info(f"[BANCO] Caché: {len(_BANCO_APPS_SET)} apps, {len(_BANCO_RUIDO_SET)} ruido.")
    except Exception as e:
        logger.error(f"[BANCO] No se pudo refrescar la caché: {e}")

# ── API SGA UNASAM ───────────────────────────────────────────────────
_SGA_BASE = os.getenv("SGA_API_URL", "https://sga.unasam.edu.pe/integracion/api/biblioteca/matriculados")
_SGA_TIMEOUT = float(os.getenv("SGA_TIMEOUT_SECONDS", "6"))
# Verificación SSL al consultar el SGA. True por defecto (seguro). Solo se
# desactiva si SGA_SSL_VERIFY es explícitamente "false"/"0"/"no" en .env.
_SGA_VERIFY_SSL = os.getenv("SGA_SSL_VERIFY", "true").strip().lower() not in ("false", "0", "no")

# Contraseña inicial para los usuarios por defecto (solo se usa al crearlos por
# primera vez). Se lee de .env; si no está, cae a un valor temporal y se avisa.
# NUNCA se registra en logs en claro. Cambiar tras el primer login.
_DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "").strip()
if not _DEFAULT_ADMIN_PASSWORD:
    # Sin contraseña inicial configurada: una aleatoria e irrecuperable. Las
    # cuentas creadas con ella se habilitan cambiando la clave por BD o
    # reinstalando con configurador.py (que la genera y la muestra una vez).
    import secrets as _secrets
    _DEFAULT_ADMIN_PASSWORD = "X" + _secrets.token_urlsafe(24)


# ── PINs del kiosco: nunca viajan en claro ─────────────────────────────
# El WS de terminal no tiene autenticacion fuerte: cualquiera en la LAN puede
# abrirlo. Antes el hello_ack entregaba los PIN de mantenimiento y offline en
# texto plano. Ahora se envia solo un hash PBKDF2 con sal que el kiosco verifica
# localmente. Se cachea por valor de PIN (calcularlo cuesta ~50 ms y se pide en
# cada hello de cada PC).
_PIN_ITER = 120_000
_pin_hash_cache: dict[str, str] = {}


def _hash_pin(pin: str) -> str:
    import hashlib, base64
    if pin in _pin_hash_cache:
        return _pin_hash_cache[pin]
    sal = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", (pin or "").encode("utf-8"), sal, _PIN_ITER)
    h = f"pbkdf2_sha256${_PIN_ITER}${base64.b64encode(sal).decode()}${base64.b64encode(dk).decode()}"
    if len(_pin_hash_cache) > 32:
        _pin_hash_cache.clear()
    _pin_hash_cache[pin] = h
    return h


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
            # Sin volcar la respuesta: puede traer DNI/nombres en claro (B-8).
            logger.warning(f"[SGA] Respuesta sin campo 'alumno' para DNI={_mask_dni(dni)}")
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
            logger.warning(f"[SGA] Campos de nombre vacíos para DNI={_mask_dni(dni)}")
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
            # 1) Leer qué toca enviar y CERRAR la sesión de BD: el envío a cientos
            #    de kioscos no debe retener una conexión del pool ni una transacción.
            pendientes = []   # (id, tipo, mensaje)
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
                    if ultima is not None and ultima.date() >= hoy:
                        continue   # ya enviado hoy
                    pendientes.append((msg.id, "cierre", msg.mensaje))

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
                    pendientes.append((msg.id, "extra", msg.mensaje))

            # 2) Enviar sin BD abierta.
            enviados = []
            for msg_id, origen, texto in pendientes:
                entregados = await manager.broadcast({
                    "tipo": "mensaje_broadcast",
                    "mensaje": texto,
                    "origen": origen,
                    "duracion_seg": duracion_seg,
                })
                if entregados > 0:
                    enviados.append((msg_id, origen))
                    logger.info(f"[MSG] {origen} enviado a {entregados} terminal(es): {texto!r}")
                else:
                    logger.info(f"[MSG] {origen} pendiente (0 terminales conectadas): {texto!r}")

            # 3) Marcar como enviados en una transacción corta.
            if enviados:
                async with async_session() as db:
                    for msg_id, origen in enviados:
                        msg = await db.get(MensajeProgramado, msg_id)
                        if msg is None:
                            continue
                        if origen == "cierre":
                            msg.fecha_envio = ahora
                        else:
                            msg.enviado = True
                    await db.commit()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[SCHEDULER] Error: {e}")

        try:
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            break


async def _indice_existe(db, tabla: str, columnas: tuple) -> bool:
    """True si la tabla ya tiene un índice cuyas primeras columnas son `columnas`
    (en orden). Así no se duplica uno creado por create_all con otro nombre."""
    from sqlalchemy import text
    filas = (await db.execute(text(
        "SELECT index_name, column_name FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = :t "
        "ORDER BY index_name, seq_in_index"), {"t": tabla})).all()
    por_indice: dict = {}
    for nombre, col in filas:
        por_indice.setdefault(nombre, []).append(str(col).lower())
    buscadas = [c.lower() for c in columnas]
    return any(cols[:len(buscadas)] == buscadas for cols in por_indice.values())


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
            # Vigencia de credenciales: fecha_alta marca cuándo entró el alumno al
            # padrón. Al crear la columna, los alumnos existentes reciben HOY como
            # alta (su vigencia arranca desde la migración). Las altas futuras la
            # setean al insertar.
            try:
                await db.execute(text("ALTER TABLE alumnos_maestro ADD COLUMN fecha_alta DATETIME NULL"))
                # Poblar SOLO en la creación de la columna (las filas existentes).
                await db.execute(text("UPDATE alumnos_maestro SET fecha_alta = NOW() WHERE fecha_alta IS NULL"))
            except Exception: pass
            # Caducidad real del carnet (la manda biblioteca/Koha). Sin poblar:
            # NULL = sin caducidad explícita → se usa el cálculo alta + vigencia.
            try:
                await db.execute(text("ALTER TABLE alumnos_maestro ADD COLUMN fecha_caducidad DATETIME NULL"))
            except Exception: pass
            # Fecha de última renovación (opcional; se estampa al renovar).
            try:
                await db.execute(text("ALTER TABLE alumnos_maestro ADD COLUMN fecha_renovacion DATETIME NULL"))
            except Exception: pass
            # ajustes_sistema.valor: guarda config de KPIs (JSON) y el LOGO del panel
            # como data URL base64 (puede pesar >1 MB). TEXT (64 KB) no alcanza para el
            # logo → usar MEDIUMTEXT (16 MB). Idempotente.
            try:
                await db.execute(text("ALTER TABLE ajustes_sistema MODIFY COLUMN valor MEDIUMTEXT NOT NULL"))
            except Exception: pass
            # Tablas de Egresados / Docentes / Autoridad (Base de Datos del panel)
            try:
                await db.execute(text("""
                    CREATE TABLE IF NOT EXISTS egresados (
                        dni VARCHAR(8) PRIMARY KEY,
                        nombre VARCHAR(200) NOT NULL,
                        codigo VARCHAR(30) NULL,
                        escuela VARCHAR(200) NULL,
                        anio_egreso VARCHAR(4) NULL,
                        activo BOOLEAN DEFAULT TRUE,
                        INDEX idx_egresado_codigo (codigo)
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """))
            except Exception: pass
            try:
                await db.execute(text("""
                    CREATE TABLE IF NOT EXISTS docentes (
                        dni VARCHAR(8) PRIMARY KEY,
                        nombre VARCHAR(200) NOT NULL,
                        facultad VARCHAR(200) NULL,
                        escuela VARCHAR(200) NULL,
                        correo VARCHAR(150) NULL,
                        telefono VARCHAR(20) NULL,
                        activo BOOLEAN DEFAULT TRUE
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """))
            except Exception: pass
            try:
                await db.execute(text("""
                    CREATE TABLE IF NOT EXISTS autoridades (
                        dni VARCHAR(8) PRIMARY KEY,
                        nombre VARCHAR(200) NOT NULL,
                        cargo VARCHAR(150) NULL,
                        correo VARCHAR(150) NULL,
                        telefono VARCHAR(20) NULL,
                        activo BOOLEAN DEFAULT TRUE
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """))
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
            # Banco de programas (lista blanca de apps reconocidas)
            try:
                await db.execute(text("""
                    CREATE TABLE IF NOT EXISTS banco_apps (
                        nombre_exe VARCHAR(150) NOT NULL PRIMARY KEY,
                        nombre_amigable VARCHAR(200) NOT NULL,
                        categoria VARCHAR(60) NULL,
                        descripcion VARCHAR(300) NULL,
                        aprobado_por VARCHAR(100) NULL,
                        fecha DATETIME DEFAULT CURRENT_TIMESTAMP
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """))
            except Exception: pass
            # Banco de ruido (procesos de fondo; cada uno con su dueño)
            try:
                await db.execute(text("""
                    CREATE TABLE IF NOT EXISTS banco_ruido (
                        nombre_exe VARCHAR(150) NOT NULL PRIMARY KEY,
                        nombre_amigable VARCHAR(200) NULL,
                        descripcion VARCHAR(300) NULL,
                        dueno_exe VARCHAR(150) NULL,
                        aprobado_por VARCHAR(100) NULL,
                        fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_ruido_dueno (dueno_exe)
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
                """))
            except Exception: pass
            # ── Multi-sede (biblioteca central + centros de computo de facultades) ──
            # Las tablas sedes/ciclos_academicos las crea create_all. Aqui solo las
            # columnas nuevas en tablas existentes (sin FK fisica: idempotente y
            # tolerante a datos viejos).
            try:
                await db.execute(text("ALTER TABLE sedes ADD COLUMN redes VARCHAR(300) NULL"))
            except Exception: pass
            try:
                await db.execute(text("ALTER TABLE sesiones ADD COLUMN id_sede INT NULL"))
                # Solo al crear la columna: el historial existente toma la sede
                # actual de su PC (no habia otra informacion).
                await db.execute(text(
                    "UPDATE sesiones s JOIN terminales t ON t.id = s.id_terminal "
                    "SET s.id_sede = t.id_sede WHERE s.id_sede IS NULL"))
            except Exception: pass
            try:
                await db.execute(text("ALTER TABLE sesiones ADD INDEX idx_sesion_sede_entrada (id_sede, hora_entrada)"))
            except Exception: pass
            for _tabla in ("terminales", "usuarios", "incidencias"):
                try:
                    await db.execute(text(f"ALTER TABLE {_tabla} ADD COLUMN id_sede INT NULL"))
                except Exception: pass
            # Sospechas por sede (aislamiento de encargados). Solo al crear la
            # columna: las existentes toman la sede de la ultima sesion del
            # alumno anterior a la sospecha (o, si no hay, la mas reciente).
            try:
                await db.execute(text("ALTER TABLE sospechas ADD COLUMN id_sede INT NULL"))
                await db.execute(text(
                    "UPDATE sospechas sp SET sp.id_sede = COALESCE("
                    " (SELECT s.id_sede FROM sesiones s WHERE s.dni_alumno = sp.dni_alumno"
                    "   AND s.hora_entrada <= sp.fecha ORDER BY s.hora_entrada DESC LIMIT 1),"
                    " (SELECT s2.id_sede FROM sesiones s2 WHERE s2.dni_alumno = sp.dni_alumno"
                    "   ORDER BY s2.hora_entrada DESC LIMIT 1))"
                    " WHERE sp.id_sede IS NULL"))
            except Exception: pass
            try:
                await db.execute(text("ALTER TABLE sospechas ADD INDEX idx_sospecha_sede (id_sede)"))
            except Exception: pass
            for _sql in (
                "ALTER TABLE terminales ADD INDEX idx_terminal_sede (id_sede)",
                "ALTER TABLE incidencias ADD INDEX idx_incidencia_sede (id_sede)",
                # Reportes por mes/ciclo/año filtran por fecha: sin estos indices
                # cada reporte recorre la tabla completa.
                "ALTER TABLE sesiones ADD INDEX idx_sesion_entrada (hora_entrada)",
                "ALTER TABLE incidencias ADD INDEX idx_incidencia_fecha (fecha)",
            ):
                try:
                    await db.execute(text(_sql))
                except Exception: pass
            # Sede inicial: todo lo existente pertenece a la Biblioteca Central.
            try:
                n_sedes = (await db.execute(text("SELECT COUNT(*) FROM sedes"))).scalar() or 0
                if n_sedes == 0:
                    await db.execute(text(
                        "INSERT INTO sedes (nombre, descripcion, activo) "
                        "VALUES ('Biblioteca Central', 'Sede inicial del sistema', 1)"))
                    sede_id = (await db.execute(text("SELECT id FROM sedes ORDER BY id LIMIT 1"))).scalar()
                    await db.execute(text("UPDATE terminales SET id_sede = :s WHERE id_sede IS NULL"), {"s": sede_id})
                    await db.execute(text("UPDATE incidencias SET id_sede = :s WHERE id_sede IS NULL"), {"s": sede_id})
                    logger.info("[SEDES] Sede inicial 'Biblioteca Central' creada y asignada a las PCs existentes.")
            except Exception as e:
                logger.error(f"[SEDES] Error creando la sede inicial: {e}")
            # ── Índices de rendimiento (servidor central, tablas grandes) ──
            # sesiones(estado, confirmada): el limpiador de fantasmas corre cada 10s.
            # sesiones(fecha_uso): filtros del historial / exportes.
            # actividad_logs(proceso_exe, nivel): banco de pendientes y rankings.
            # Idempotente: se crea solo si no hay ya un índice que EMPIECE por esas
            # columnas (create_all ya pudo crear ix_actividad_logs_fecha_hora).
            try:
                for _tabla, _nombre, _cols in (
                    ("sesiones", "idx_sesion_estado_conf", ("estado", "confirmada")),
                    ("sesiones", "idx_sesion_fecha_uso", ("fecha_uso",)),
                    ("actividad_logs", "idx_act_fecha_hora", ("fecha_hora",)),
                    ("actividad_logs", "idx_act_exe_nivel", ("proceso_exe", "nivel")),
                    # Cada conexión/hello de kiosco busca la PC por IP.
                    ("terminales", "idx_terminal_ip", ("ip",)),
                ):
                    if not await _indice_existe(db, _tabla, _cols):
                        try:
                            await db.execute(text(
                                f"ALTER TABLE {_tabla} ADD INDEX {_nombre} ({', '.join(_cols)})"))
                            logger.info(f"[MIGRACION] Índice {_nombre} creado en {_tabla}")
                        except Exception as e_idx:
                            logger.warning(f"[MIGRACION] No se pudo crear {_nombre}: {e_idx}")
            except Exception as e:
                logger.error(f"[MIGRACION] Error revisando índices: {e}")
            await db.commit()
            # Pre-carga (seed) de ambos bancos — solo si están vacíos, para no
            # pisar lo que el superadmin haya editado a mano.
            await _seed_bancos(db)
    except Exception as e:
        logger.error(f"Error en migraciones: {e}")


async def _sede_para_ip(db, ip: str):
    """Sede que corresponde a una PC nueva: la primera sede activa cuyo prefijo
    de red coincide con la IP; si no hay coincidencia y existe UNA sola sede
    activa, esa. Si no, None (queda 'sin sede' hasta que el superadmin la asigne)."""
    from models import Sede
    try:
        sedes = (await db.execute(select(Sede).where(Sede.activo == True).order_by(Sede.id))).scalars().all()
    except Exception:
        return None
    for sd in sedes:
        for pref in (sd.redes or "").split(","):
            pref = pref.strip()
            if pref and ip and ip.startswith(pref):
                return sd.id
    return sedes[0].id if len(sedes) == 1 else None


async def _seed_bancos(db):
    """Pre-carga generosa de banco_apps y banco_ruido desde el catálogo conocido.
    Idempotente: solo inserta si la tabla está vacía."""
    from sqlalchemy import text
    from core.catalogo_apps import APPS_CONOCIDAS, RUIDO_CONOCIDO
    try:
        n_apps = (await db.execute(text("SELECT COUNT(*) FROM banco_apps"))).scalar() or 0
        if n_apps == 0:
            for exe, (nom, cat, desc) in APPS_CONOCIDAS.items():
                await db.execute(text(
                    "INSERT INTO banco_apps (nombre_exe, nombre_amigable, categoria, descripcion, aprobado_por, fecha) "
                    "VALUES (:e, :n, :c, :d, NULL, NOW())"),
                    {"e": exe.lower(), "n": nom, "c": cat, "d": desc})
            logger.info(f"[BANCO] Pre-cargadas {len(APPS_CONOCIDAS)} apps reconocidas.")
        n_ruido = (await db.execute(text("SELECT COUNT(*) FROM banco_ruido"))).scalar() or 0
        if n_ruido == 0:
            for exe, (nom, desc, dueno) in RUIDO_CONOCIDO.items():
                await db.execute(text(
                    "INSERT INTO banco_ruido (nombre_exe, nombre_amigable, descripcion, dueno_exe, aprobado_por, fecha) "
                    "VALUES (:e, :n, :d, :du, NULL, NOW())"),
                    {"e": exe.lower(), "n": nom, "d": desc, "du": (dueno or None)})
            logger.info(f"[BANCO] Pre-cargados {len(RUIDO_CONOCIDO)} procesos de ruido.")
        await db.commit()
    except Exception as e:
        logger.error(f"[BANCO] Error en pre-carga: {e}")
    # Cargar la caché en memoria con lo que haya quedado (seed o ediciones).
    await _refrescar_bancos(db)


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
                a_bloquear = []   # nombre_red de las PCs cuya sesión se canceló
                for row in fantasmas:
                    sesion_id, terminal_id_db = row
                    res_s = await db.execute(select(Sesion).where(Sesion.id == sesion_id))
                    s = res_s.scalar_one_or_none()
                    if s and s.estado == "activa":   # pudo cerrarse entre la consulta y aquí
                        s.estado = "cerrada"   # Sesion no tiene columna 'activa' (es una propiedad legacy)
                        s.motivo_cierre = "sin_confirmacion"
                        s.hora_salida = datetime.now().replace(tzinfo=None)
                        res_t = await db.execute(select(Terminal).where(Terminal.id == s.id_terminal))
                        t = res_t.scalar_one_or_none()
                        if t:
                            t.estado = "bloqueado"
                            a_bloquear.append(t.nombre_red)
                        logger.warning(f"[FANTASMA] Sesión #{sesion_id} cancelada por falta de confirmación")
                if fantasmas:
                    await db.commit()
            # Tras el commit y fuera de la sesión de BD: la sesión ya no existe, así
            # que el kiosco no puede quedar desbloqueado (si llegó a abrirse).
            for nombre_red in a_bloquear:
                await manager.bloquear_terminal(nombre_red)
                await manager.limpiar_foco(nombre_red)
            if fantasmas:
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
        # hora_salida = ahora (no hora_entrada: eso dejaba sesiones de 0 min) y
        # motivo propio 'reinicio_servidor': si el servidor solo se reinició, el
        # kiosco que reconecte en < 3 min reanuda la sesión (período de gracia).
        # Tras un apagón largo la gracia ya venció y la PC se bloquea igual.
        ahora_arranque = datetime.now()
        for s in sesiones:
            s.estado = 'cerrada'
            s.hora_salida = ahora_arranque
            s.motivo_cierre = 'reinicio_servidor'
            cerradas += 1
        if cerradas > 0:
            logger.warning(f"[STARTUP] Se cerraron {cerradas} sesión(es) que quedaron abiertas (reinicio/apagón); reanudables por {GRACIA_RECONEXION_SEG}s.")

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
    # Primera pasada poco después de arrancar (si el servidor se reinicia a
    # diario, con "cada 24 h desde el arranque" la purga no corría nunca).
    espera = _PURGA_PRIMERA_SEG
    while True:
        try:
            await asyncio.sleep(espera)
            espera = 24 * 3600
            await _purgar_una_vez()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[PURGA] Error en purga de actividad: {e}")


_PURGA_PRIMERA_SEG = 5 * 60
_PURGA_LOTE = 5000            # filas por DELETE: transacciones cortas, sin bloquear la tabla
_PURGA_FILAS_POR_EXCEL = 100_000   # filas por archivo de respaldo (memoria acotada)


def _guardar_excel(ruta: str, titulo: str, cabecera: list, filas: list) -> None:
    """Escribe un .xlsx (se llama en un hilo: openpyxl es CPU puro y bloquearía
    el event loop, congelando todos los WebSocket mientras dura)."""
    import openpyxl
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet(titulo)
    ws.append(cabecera)
    for f in filas:
        ws.append(f)
    wb.save(ruta)


async def _purgar_tabla(nombre: str, modelo, filtro, cabecera: list, fila_de) -> int:
    """Respaldar a Excel y borrar en lotes las filas de `modelo` que cumplan
    `filtro`. Recorre por id ascendente; cada archivo se guarda ANTES de borrar
    sus filas, así un fallo nunca pierde datos sin respaldo."""
    total = 0
    ultimo_id = 0
    while True:
        filas, ids = [], []
        async with async_session() as db:
            while len(ids) < _PURGA_FILAS_POR_EXCEL:
                lote = (await db.execute(
                    select(modelo).where(filtro, modelo.id > ultimo_id)
                    .order_by(modelo.id).limit(_PURGA_LOTE))).scalars().all()
                if not lote:
                    break
                for r in lote:
                    filas.append(fila_de(r))
                    ids.append(r.id)
                ultimo_id = lote[-1].id
        if not ids:
            return total
        os.makedirs(RESPALDOS_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        ruta = os.path.join(RESPALDOS_DIR, f"{nombre}_purgada_{stamp}.xlsx")
        await asyncio.to_thread(_guardar_excel, ruta, nombre[:31], cabecera, filas)
        for i in range(0, len(ids), _PURGA_LOTE):
            async with async_session() as db:
                await db.execute(delete(modelo).where(modelo.id.in_(ids[i:i + _PURGA_LOTE])))
                await db.commit()
            await asyncio.sleep(0)   # ceder el loop entre lotes
        total += len(ids)
        logger.info(f"[PURGA] {len(ids)} fila(s) de {nombre} respaldada(s) en '{ruta}' y borrada(s).")


async def _purgar_una_vez():
    from datetime import timedelta
    corte = datetime.now() - timedelta(days=RETENCION_NORMALES_DIAS)
    _f = lambda d: d.strftime("%Y-%m-%d %H:%M:%S") if d else ""
    # Solo eventos NORMALES vencidos. Los sospechosos se conservan (evidencia).
    n_act = await _purgar_tabla(
        "actividad", ActividadLog,
        (ActividadLog.nivel != "sospechoso") & (ActividadLog.fecha_hora < corte),
        ["ID", "Fecha/Hora", "PC", "Alumno", "DNI", "Tipo", "Descripción", "Detalle", "Proceso", "Nivel"],
        lambda r: [r.id, _f(r.fecha_hora), r.nombre_terminal, r.nombre_alumno, r.dni_alumno,
                   r.tipo, r.descripcion, r.detalle or "", r.proceso_exe or "", r.nivel])
    # Sospechas YA RESUELTAS (aprobada/descartada) con la misma retención. Las
    # pendientes nunca se purgan: alguien todavía tiene que revisarlas.
    n_sosp = await _purgar_tabla(
        "sospechas", Sospecha,
        Sospecha.estado.in_(("aprobada", "descartada"))
        & (Sospecha.fecha < corte)
        & ((Sospecha.fecha_revision.is_(None)) | (Sospecha.fecha_revision < corte)),
        ["ID", "Fecha", "DNI", "Alumno", "Tipo", "Detalle", "Estado", "Revisado por", "Fecha revisión"],
        lambda r: [r.id, _f(r.fecha), r.dni_alumno, r.nombre_alumno, r.tipo, r.detalle,
                   r.estado, r.revisado_por or "", _f(r.fecha_revision)])
    if not n_act and not n_sosp:
        logger.info("[PURGA] No hay logs de actividad ni sospechas resueltas vencidas.")


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
        # Puede haber varias cuentas por rol (gestion de encargados): nunca
        # scalar_one_or_none aqui, o el servidor no arranca.
        res_sa = await db.execute(
            select(Usuario).where(Usuario.rol == "superadmin").order_by(Usuario.id).limit(1))
        usuario_sa = res_sa.scalars().first()
        if usuario_sa:
            if usuario_sa.username != "superadmin":
                usuario_sa.username = "superadmin"
                await db.commit()
                logger.info(f"Usuario superadmin renombrado a 'superadmin'")
        else:
            db.add(Usuario(
                username="superadmin",
                # bcrypt es CPU pesado: en un hilo para no frenar el event loop.
                hashed_password=await asyncio.to_thread(hashear_password, _DEFAULT_ADMIN_PASSWORD),
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
        res_a = await db.execute(select(Usuario.id).where(Usuario.rol == "admin").limit(1))
        if res_a.scalars().first() is None:
            db.add(Usuario(
                username="admin",
                hashed_password=await asyncio.to_thread(hashear_password, _DEFAULT_ADMIN_PASSWORD),
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
app.include_router(organizacion_router)

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
    datos: Optional[_ActualizarUsuario] = None,   # se ignora: endpoint retirado
    superadmin: Usuario = Depends(obtener_usuario_actual),
):
    """OBSOLETO. Buscaba "el" usuario por rol, lo que falla con varios encargados.
    La gestión de cuentas vive en /api/admin/usuarios. Responde 410 sin tocar la BD."""
    raise HTTPException(
        status_code=410,
        detail="Esta opción fue reemplazada. Use Configuración > Credenciales > Encargados.",
    )


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
        "backdoor_pin_hash": _hash_pin(cfg.backdoor_pin),
    })
    return {"mensaje": "Configuración de mantenimiento actualizada correctamente"}


@app.get("/api/config/offline-pin")
async def obtener_config_offline(
    admin: Usuario = Depends(obtener_usuario_actual),
    db: AsyncSession = Depends(get_db)
):
    # El PIN offline es una credencial de desbloqueo local persistente: solo el
    # superadmin puede verla, igual que el backdoor de mantenimiento (H-3).
    if admin.rol != "superadmin":
        raise HTTPException(status_code=403, detail="Solo el superadmin puede ver el PIN offline.")
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
    # Fijar el PIN offline equivale a fijar una llave de acceso físico en todos
    # los kioscos: reservado a superadmin (H-3).
    if admin.rol != "superadmin":
        raise HTTPException(status_code=403, detail="Solo el superadmin puede modificar el PIN offline.")
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
        "offline_pin_hash": _hash_pin(cfg.offline_pin),
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
        res2 = await db.execute(select(Terminal).where(Terminal.ip == ip).order_by(Terminal.ultima_conexion.desc()))
        t = res2.scalars().first()  # la IP no es única (DHCP)
    return t


def _cerrar_sesion(sesion: Sesion, motivo: str):
    """Marca una sesión como cerrada con la hora actual del servidor."""
    ahora = datetime.now().replace(tzinfo=None)
    sesion.hora_salida   = ahora
    sesion.estado        = "cerrada"
    sesion.motivo_cierre = motivo
    return ahora


async def _aplicar_sesion_unica(db, dni: str, terminal_destino_id: Optional[int] = None):
    """Sesión única por alumno: cierra sus sesiones activas en otras PCs y les
    ordena volver al login. Sin commit (lo hace el llamador). Si la sesión previa
    está en la misma PC que se va a desbloquear, no se le manda el cierre (le
    llega enseguida el 'desbloquear')."""
    res_dup = await db.execute(
        select(Sesion).where(Sesion.dni_alumno == dni, Sesion.estado == "activa"))
    for sp in res_dup.scalars().all():
        _cerrar_sesion(sp, "desplazado_por_nuevo_login")
        t_prev = await db.get(Terminal, sp.id_terminal)
        if t_prev and t_prev.id != terminal_destino_id:
            t_prev.estado = "bloqueado"
            await manager.forzar_cierre_sesion(t_prev.nombre_red)
            await manager.limpiar_foco(t_prev.nombre_red)
            logger.warning(f"[WS] Sesión duplicada cerrada: alumno {_mask_dni(dni)} en {t_prev.nombre_red}")


async def _cerrar_activas_de_terminal(db, id_terminal: int, motivo: str) -> int:
    """Cierra toda sesión que siga activa en una PC (sin commit)."""
    res = await db.execute(
        select(Sesion).where(Sesion.id_terminal == id_terminal, Sesion.estado == "activa"))
    n = 0
    for s in res.scalars().all():
        _cerrar_sesion(s, motivo)
        n += 1
    if n:
        logger.warning(f"[WS] {n} sesión(es) previa(s) cerrada(s) en terminal id={id_terminal} ({motivo})")
    return n


# Período de gracia de reconexión (segundos) y motivos de cierre que se pueden
# revertir: solo cortes involuntarios, nunca logout/bloqueo/desplazamiento.
GRACIA_RECONEXION_SEG = 180
_MOTIVOS_REANUDABLES = ("desconexion_red", "reinicio_servidor")


def _datos_alumno_kiosco(alumno: AlumnoMaestro) -> dict:
    """Payload 'alumno' del comando desbloquear (mismo corte de nombre que el login)."""
    partes = (alumno.nombre or "").split()
    if len(partes) >= 3:
        nombres, apellidos = " ".join(partes[:-2]), " ".join(partes[-2:])
    elif len(partes) == 2:
        nombres, apellidos = partes[0], partes[1]
    else:
        nombres, apellidos = alumno.nombre or "", ""
    return {"codigo": alumno.codigo or alumno.dni, "nombres": nombres, "apellidos": apellidos}


async def _reanudar_sesion_con_gracia(db, t: Terminal):
    """Reabre la ÚLTIMA sesión de la terminal si se cerró por corte de red o
    reinicio del servidor hace menos de GRACIA_RECONEXION_SEG. Devuelve la
    sesión reabierta (sin commit) o None. Conservador: cualquier condición
    dudosa -> None (el llamador bloquea la PC)."""
    ultima = (await db.execute(
        select(Sesion).where(Sesion.id_terminal == t.id)
        .order_by(Sesion.id.desc()).limit(1))).scalars().first()
    if (ultima is None or ultima.estado != "cerrada" or not ultima.confirmada
            or ultima.motivo_cierre not in _MOTIVOS_REANUDABLES or ultima.hora_salida is None):
        return None
    transcurrido = (datetime.now() - ultima.hora_salida).total_seconds()
    if transcurrido < 0 or transcurrido > GRACIA_RECONEXION_SEG:
        return None
    # Baneado mientras estaba desconectado -> no se reanuda.
    if await _ban_activo(db, ultima.dni_alumno):
        return None
    # Tiene otra sesión activa, o abrió alguna después del corte (aunque ya la
    # haya cerrado: se movió de PC) -> no se reanuda (sesión única).
    otra = (await db.execute(
        select(Sesion.id).where(
            Sesion.dni_alumno == ultima.dni_alumno,
            (Sesion.estado == "activa") | (Sesion.id > ultima.id))
        .limit(1))).scalars().first()
    if otra is not None:
        return None
    alumno = await db.get(AlumnoMaestro, ultima.dni_alumno)
    if alumno is None:
        return None
    from api.endpoints import _vigencia_meses, _vencido_efectivo
    if _vencido_efectivo(alumno.fecha_alta, alumno.fecha_caducidad, await _vigencia_meses(db)):
        return None
    ultima.estado        = "activa"
    ultima.hora_salida   = None
    ultima.motivo_cierre = None
    t.estado = "activo"
    return ultima


class _TokenBucket:
    """Cubeta de fichas: `tasa` eventos/s sostenidos con ráfagas de hasta
    `rafaga`. Por conexión, sin locks (un handler = una tarea asyncio)."""

    def __init__(self, tasa: float, rafaga: int):
        import time as _time
        self._reloj = _time.monotonic
        self.tasa = tasa
        self.rafaga = float(rafaga)
        self.fichas = float(rafaga)
        self.ultimo = self._reloj()

    def permitir(self) -> bool:
        ahora = self._reloj()
        self.fichas = min(self.rafaga, self.fichas + (ahora - self.ultimo) * self.tasa)
        self.ultimo = ahora
        if self.fichas >= 1.0:
            self.fichas -= 1.0
            return True
        return False


async def _registrar_terminal_por_ip(terminal_ip: str) -> None:
    """Registro inicial de la PC por IP (antes del 'hello'): UPDATE si existe,
    INSERT si no. Tolerante a la carrera de dos conexiones simultáneas desde
    una IP nueva: la segunda choca con el UNIQUE de nombre_red, relee y
    actualiza en vez de reventar el handler."""
    async def _actualizar(db) -> bool:
        res = await db.execute(select(Terminal).where(Terminal.ip == terminal_ip)
                               .order_by(Terminal.ultima_conexion.desc()))
        terminal_db = res.scalars().first()  # la IP no es única (DHCP)
        if terminal_db is None:
            return False
        terminal_db.estado = "bloqueado"
        terminal_db.ultima_conexion = datetime.now()
        await db.commit()
        logger.info(f"[WS] Terminal conocida actualizada: {terminal_ip}")
        return True

    async with async_session() as db:
        if await _actualizar(db):
            return
        db.add(Terminal(
            nombre_red=f"Terminal-{terminal_ip}",
            ip=terminal_ip,
            estado="bloqueado",
            ultima_conexion=datetime.now(),
            id_sede=await _sede_para_ip(db, terminal_ip),
        ))
        try:
            await db.commit()
            logger.info(f"[WS] Nueva terminal registrada en DB: {terminal_ip}")
            return
        except IntegrityError:
            await db.rollback()
    # Otra conexión la insertó entre la lectura y el INSERT: releer y actualizar.
    async with async_session() as db:
        await _actualizar(db)


async def _sospecha_pendiente_igual(db, dni: str, proceso_exe: str, detalle: str) -> bool:
    """True si el alumno ya tiene HOY una sospecha de actividad PENDIENTE por el
    mismo programa (o, sin .exe, con el mismo detalle). Sobrevive a reconexiones,
    a diferencia del set en memoria de la conexión."""
    hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    q = select(Sospecha.id).where(
        Sospecha.dni_alumno == dni,
        Sospecha.tipo == "actividad_sospechosa",
        Sospecha.estado == "pendiente",
        Sospecha.fecha >= hoy,
    )
    if proceso_exe:
        q = q.where(Sospecha.detalle.contains(proceso_exe, autoescape=True))
    else:
        q = q.where(Sospecha.detalle == detalle)
    return (await db.execute(q.limit(1))).first() is not None


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

    # Rate limit de 'actividad' por CONEXIÓN: cada evento es un INSERT y, si es
    # sospechoso, un aviso a todos los paneles. Un kiosco honesto emite pocos
    # por segundo al abrir programas; un cliente falso no puede inundar la BD.
    cubeta_actividad = _TokenBucket(tasa=5.0, rafaga=20)
    # Sospechas ya creadas en esta conexión: (sesion_id, exe). Evita una fila y
    # una alerta por cada vez que el alumno reabre el mismo programa.
    sospechas_vistas: set = set()
    errores_recepcion = 0

    try:
        await manager.notificar_evento(f"Terminal '{terminal_id}' conectada desde {terminal_ip}")
        await manager.enviar_log("activity", f"Terminal '{terminal_id}' conectada desde {terminal_ip}")

        # ── Registro automático: INSERT si no existe, UPDATE si ya existe ─
        # Dentro del try: si la BD falla, el finally saca la conexión del manager
        # (antes quedaba una conexión fantasma listada como conectada).
        await _registrar_terminal_por_ip(terminal_ip)

        # Notificar panel admin para que refresque la lista
        await manager.notificar_admins()

        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning(f"[WS] Timeout 60s sin mensajes de {terminal_id}")
                break
            except WebSocketDisconnect:
                raise
            except Exception as e:
                # Socket ya cerrado (p.ej. reemplazado con 4009): salir, no girar
                # en vacío reintentando recibir de un socket muerto.
                if websocket.application_state != WebSocketState.CONNECTED \
                        or websocket.client_state != WebSocketState.CONNECTED:
                    break
                errores_recepcion += 1
                if errores_recepcion > 50:
                    logger.warning(f"[WS] {terminal_id}: demasiados mensajes inválidos; se cierra")
                    break
                logger.debug(f"[WS] Error recibiendo JSON: {e}")
                try:
                    await websocket.send_json({"tipo": "error", "motivo": "Mensaje JSON inválido"})
                except Exception:
                    pass
                continue

            if not isinstance(data, dict):
                continue
            tipo = data.get("tipo", "")
            if not tipo:
                tipo = data.get("type", "") # Soporte para "type" en lugar de "tipo"
            tipo = str(tipo)[:40]

            # DEBUG: con cientos de PCs (heartbeat cada 3 s) en INFO llenaría el log.
            logger.debug(f"[WS] {terminal_id} -> tipo={tipo!r}")

            if tipo == "error_report":
                msg = _limpio(data.get("message", "Error sin detalle"))
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
                # El hostname es la clave de la PC en el manager, en la BD y en
                # los logs: solo caracteres de nombre de máquina, sin saltos de
                # línea ni HTML.
                if not _HOSTNAME_RE.match(hostname):
                    logger.warning(f"[WS] {terminal_id}: hello con hostname inválido rechazado: {_limpio(hostname, 80)!r}")
                    await websocket.send_json({"tipo": "error", "motivo": "hostname inválido en hello"})
                    continue

                cliente_desbloqueado = bool(data.get("desbloqueado", False))
                cliente_modo_offline = bool(data.get("modo_offline", False))

                old_id = terminal_id
                terminal_id = hostname
                # Si ya había otra conexión con este hostname, el manager la
                # cierra con 4009 ('reemplazado'); su finally no toca la sesión.
                await manager.actualizar_id(old_id, terminal_id, ip=terminal_ip, websocket=websocket)
                logger.info(f"[WS] Terminal re-identificada: {old_id} -> {terminal_id} (hostname={hostname}, desbloqueado={cliente_desbloqueado})")
                focos_a_limpiar = []

                # Sincronizar DB: nombre (hostname) ↔ IP de forma atómica
                async with async_session() as db:
                    # Buscar registro canónico por hostname
                    res = await db.execute(select(Terminal).where(Terminal.nombre_red == hostname))
                    t = res.scalar_one_or_none()

                    res2 = await db.execute(select(Terminal).where(Terminal.ip == terminal_ip).order_by(Terminal.ultima_conexion.desc()))
                    t_by_ip = res2.scalars().first()  # la IP no es única (DHCP)

                    # En modo offline el cliente ya tiene sesión activa — no pisar estado.
                    # Terminal usa "activo" (masculino); "activa" es de Sesion.
                    estado_inicial = "activo" if cliente_modo_offline else "bloqueado"

                    if t:
                        t.ip = terminal_ip
                        if not cliente_modo_offline:
                            t.estado = estado_inicial
                        t.ultima_conexion = datetime.now()
                        if t_by_ip and t_by_ip.id != t.id:
                            await db.delete(t_by_ip)
                            logger.info(f"[WS] Registro temporal '{t_by_ip.nombre_red}' eliminado (duplicado de '{hostname}')")
                        logger.info(f"[WS] Terminal '{hostname}' actualizada con IP={terminal_ip}")
                    elif t_by_ip:
                        t_by_ip.nombre_red = hostname
                        if not cliente_modo_offline:
                            t_by_ip.estado = estado_inicial
                        t_by_ip.ultima_conexion = datetime.now()
                        logger.info(f"[WS] Terminal IP={terminal_ip} sincronizada con nombre '{hostname}'")
                    else:
                        db.add(Terminal(
                            nombre_red=hostname,
                            ip=terminal_ip,
                            estado=estado_inicial,
                            ultima_conexion=datetime.now(),
                            id_sede=await _sede_para_ip(db, terminal_ip),
                        ))
                        logger.info(f"[WS] Nueva terminal '{hostname}' registrada con IP={terminal_ip}")
                    # La PC dice estar BLOQUEADA (y no en modo offline): no puede
                    # quedar una sesión activa en el servidor para ella (contaría
                    # como ocupada y el alumno "seguiría" dentro). Motivo
                    # reanudable: si en < 3 min vuelve desbloqueada, se reanuda.
                    if not cliente_desbloqueado and not cliente_modo_offline:
                        t_ref = t or t_by_ip
                        if t_ref is not None and t_ref.id is not None:
                            if await _cerrar_activas_de_terminal(db, t_ref.id, "desconexion_red"):
                                focos_a_limpiar.append(hostname)
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
                    "backdoor_pin_hash": _hash_pin(backdoor_pin),
                    "offline_modifiers": offline_modifiers,
                    "offline_key": offline_key,
                    "offline_pin_hash": _hash_pin(offline_pin),
                })
                for _nr in focos_a_limpiar:
                    await manager.limpiar_foco(_nr)

                # ── Si el cliente dice estar desbloqueado, verificar que haya sesión activa ──
                # En modo offline el cliente maneja su propia sincronización — no forzar bloqueo.
                # Período de gracia: si la sesión de esta PC se cerró hace < 3 min
                # por un corte de red o un reinicio del servidor, se REANUDA en vez
                # de sacar al alumno (con un servidor central, un microcorte
                # bloquearía todas las PCs de todas las sedes). Ante cualquier
                # duda se bloquea (seguridad primero).
                if cliente_desbloqueado and not cliente_modo_offline:
                    datos_reanudar = None
                    try:
                        async with async_session() as db:
                            t_check = (await db.execute(
                                select(Terminal).where(Terminal.nombre_red == hostname))).scalars().first()
                            if t_check:
                                sesion_viva = await _sesion_activa_de_terminal(db, t_check.id)
                                reanudada = False
                                if sesion_viva is None:
                                    sesion_viva = await _reanudar_sesion_con_gracia(db, t_check)
                                    reanudada = sesion_viva is not None
                                if sesion_viva is not None:
                                    alumno_v = await db.get(AlumnoMaestro, sesion_viva.dni_alumno)
                                    if alumno_v is not None:
                                        # La PC sigue en uso: el hello la había dejado en "bloqueado".
                                        t_check.estado = "activo"
                                        datos_reanudar = (_datos_alumno_kiosco(alumno_v), reanudada,
                                                          alumno_v.nombre, sesion_viva.id)
                                await db.commit()
                    except Exception as e_gracia:
                        logger.error(f"[WS] Error verificando sesión de {hostname} en hello: {e_gracia}", exc_info=True)
                        datos_reanudar = None
                    if datos_reanudar is None:
                        logger.warning(f"[WS] {hostname} reporta desbloqueado pero sin sesión activa — forzando bloqueo")
                        await manager.bloquear_terminal(terminal_id)
                    else:
                        datos_k, reanudada, nombre_v, sesion_id_v = datos_reanudar
                        # El kiosco que notó el corte se bloquea solo al reconectar;
                        # re-enviar 'desbloquear' lo deja coherente con la sesión
                        # viva del servidor (no cierra programas: el bloqueo es un overlay).
                        await manager.desbloquear_terminal(terminal_id, datos_k)
                        if reanudada:
                            logger.info(f"[WS] Sesión #{sesion_id_v} reanudada con gracia en {hostname}")
                            await manager.notificar_evento(f"Sesión reanudada tras reconexión: {nombre_v} en {hostname}", "login")
                            await manager.enviar_log("activity", f"Sesión reanudada: {nombre_v} en {hostname}")

                await manager.notificar_admins()

            elif tipo == "login_request":
                codigo = str(data.get("codigo", "")).strip().upper()
                razon = _limpio(data.get("razon", ""), 200).strip()   # = largo de sesiones.razon_uso
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
                        ban_activo = await _ban_activo(db, codigo)
                        if ban_activo:
                            motivo_ban = ban_activo.motivo or "Acceso restringido"
                            fecha_fin_ban = ban_activo.fecha_fin.strftime("%d/%m/%Y") if ban_activo.fecha_fin else "indefinido"
                            logger.warning(f"[WS] {terminal_id} DNI={_mask_dni(codigo)} BANEADO — {motivo_ban}")
                            await websocket.send_json({"tipo": "login_rechazado", "motivo": f"Acceso denegado. {motivo_ban}. Expira: {fecha_fin_ban}"})
                            continue

                        # ── Capa 1: alumnos_maestro (fuente primaria, respuesta instantánea) ──
                        res_m = await db.execute(select(AlumnoMaestro).where(AlumnoMaestro.dni == codigo))
                        maestro = res_m.scalar_one_or_none()

                        # ── Verificar vigencia del carnet ──
                        # Un carnet vencido no puede iniciar sesión: se le indica que
                        # debe renovarlo en biblioteca.
                        if maestro:
                            from api.endpoints import _vigencia_meses, _vencido_efectivo, _venc_efectivo
                            _meses_vig = await _vigencia_meses(db)
                            if _vencido_efectivo(maestro.fecha_alta, maestro.fecha_caducidad, _meses_vig):
                                _venc = _venc_efectivo(maestro.fecha_alta, maestro.fecha_caducidad, _meses_vig)
                                _venc_txt = _venc.strftime("%d/%m/%Y") if _venc else ""
                                logger.warning(f"[WS] {terminal_id} DNI={_mask_dni(codigo)} carnet VENCIDO ({_venc_txt}) — acceso denegado")
                                await websocket.send_json({"tipo": "login_rechazado", "motivo": f"Su carnet venció el {_venc_txt}. Debe renovarlo en biblioteca para usar las PCs."})
                                continue

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
                        await _aplicar_sesion_unica(db, alumno.dni)

                        t = await _buscar_terminal(db, terminal_id, terminal_ip)
                        if t:
                            # Una PC en la pantalla de login no puede tener sesión viva:
                            # si quedó alguna (fantasma), se cierra antes de crear la nueva.
                            await _cerrar_activas_de_terminal(db, t.id, "reemplazada_en_terminal")

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
                            sesion = await _sesion_activa_de_terminal(db, t.id, solo_sin_confirmar=True)
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

            elif tipo == "foco":
                # Programa en foco AHORA (estado en vivo del mapa). No se guarda
                # en la base ni genera sospechas: es estado efímero para pintar
                # la tarjeta de la PC. Solo se difunde si el .exe es una app
                # CONOCIDA (banco de apps); ruido y sin-clasificar se descartan
                # para no ensuciar el mapa. Requiere sesión activa.
                exe = str(data.get("proceso_exe", "") or "").strip()
                exe = re.sub(r"[^\w.\- ]", "", exe)[:120]
                if not exe:
                    continue
                # Pre-barrera anti-ráfaga ANTES de tocar la DB (M1): un cliente
                # falso no puede forzar una query por cada mensaje spameado.
                if not manager.foco_permitido_por_conn(terminal_id):
                    continue
                nombre_red_foco = None
                async with async_session() as db:
                    t = await _buscar_terminal(db, terminal_id, terminal_ip)
                    if not t:
                        continue
                    if not await _sesion_activa_de_terminal(db, t.id):
                        continue  # sin sesión activa, no hay foco que mostrar
                    # Leer el nombre DENTRO de la sesión: fuera, el objeto queda
                    # detached y acceder a sus atributos lanzaría DetachedInstanceError.
                    nombre_red_foco = t.nombre_red
                if exe.lower() in _BANCO_APPS_SET:
                    await manager.set_foco(nombre_red_foco, exe)
                else:
                    # No es app conocida: asegurar que la tarjeta no muestre un
                    # foco viejo (p.ej. cerró Word y abrió algo sin clasificar).
                    await manager.limpiar_foco(nombre_red_foco)

            elif tipo == "actividad":
                # Evento de actividad del alumno en la PC (proceso, archivo, comando, navegador)
                # Excedente del rate limit: se descarta ANTES de tocar la BD.
                if not cubeta_actividad.permitir():
                    logger.debug(f"[ACT] {terminal_id}: 'actividad' descartada por rate limit")
                    continue
                aviso_sospecha = None
                aviso_actividad = None
                async with async_session() as db:
                    t = await _buscar_terminal(db, terminal_id, terminal_ip)
                    if not t:
                        continue
                    # Buscar sesión activa para obtener el alumno
                    sesion = await _sesion_activa_de_terminal(db, t.id)
                    if not sesion:
                        continue  # sin sesión activa, ignorar

                    res_a = await db.execute(
                        select(AlumnoMaestro).where(AlumnoMaestro.dni == sesion.dni_alumno)
                    )
                    alumno = res_a.scalar_one_or_none()
                    if not alumno:
                        continue

                    # Truncado a los largos de columna (actividad_logs): un valor
                    # más largo haría fallar el INSERT en MySQL estricto.
                    tipo_ev    = _limpio(data.get("evento",      "proceso"), 30).strip()
                    descripcion= _limpio(data.get("descripcion", ""), 300).strip()
                    detalle    = _limpio(data.get("detalle",     "") or "", 600).strip()
                    nivel      = _limpio(data.get("nivel",       "normal"), 20).strip()
                    proceso_exe= _limpio(data.get("proceso_exe", "") or "", 150).strip()
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

                    # ── Reevaluación por BANCO (fail-closed) ──────────────
                    # Regla: un programa que NO está ni en el banco de apps ni
                    # en el de ruido se marca SOSPECHOSO hasta que el superadmin
                    # lo clasifique. Solo aplica a eventos de proceso con .exe.
                    #   · Si el cliente ya dijo "sospechoso" (herramienta
                    #     peligrosa: cmd, powershell…) se RESPETA siempre; el
                    #     banco nunca rebaja una sospecha del cliente.
                    #   · En banco_apps   -> normal.
                    #   · En banco_ruido  -> normal (la vista normal lo oculta).
                    #   · En ninguno      -> sospechoso.
                    if tipo_ev == "proceso" and proceso_exe and nivel != "sospechoso":
                        _exe = proceso_exe.lower()
                        if _exe in _BANCO_APPS_SET or _exe in _BANCO_RUIDO_SET:
                            nivel = "normal"
                        else:
                            nivel = "sospechoso"

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

                    # Si es sospechoso, crear sospecha automáticamente, salvo que
                    # ya exista una PENDIENTE igual (mismo alumno y programa, hoy):
                    # el log de actividad sí se guarda siempre (evidencia), pero
                    # no se duplica la fila de sospecha ni la alerta del panel.
                    if nivel == "sospechoso":
                        detalle_sosp = (f"[{t.nombre_red}] {descripcion}" + (f" — {detalle}" if detalle else ""))[:600]
                        clave = (sesion.id, (proceso_exe or descripcion).lower()[:150])
                        if clave not in sospechas_vistas:
                            if len(sospechas_vistas) > 1000:
                                sospechas_vistas.clear()   # acotar memoria por conexión
                            sospechas_vistas.add(clave)
                            if not await _sospecha_pendiente_igual(db, alumno.dni, proceso_exe, detalle_sosp):
                                db.add(Sospecha(
                                    dni_alumno    = alumno.dni,
                                    nombre_alumno = alumno.nombre,
                                    tipo          = "actividad_sospechosa",
                                    detalle       = detalle_sosp,
                                    id_sede       = sesion.id_sede,
                                ))
                                aviso_sospecha = {
                                    "tipo":    "sospecha",
                                    "nivel":   "alerta",
                                    "mensaje": f"ALERTA: {alumno.nombre} ({alumno.dni}) en {t.nombre_red}: {descripcion}",
                                }

                    # Commit ANTES de difundir: nunca esperar a los paneles con la
                    # transacción (y la conexión del pool) abiertas.
                    await db.commit()
                    _nivel_log = logging.INFO if nivel == "sospechoso" else logging.DEBUG
                    logger.log(_nivel_log, f"[ACT] {t.nombre_red} | {_mask_dni(alumno.dni)} | {tipo_ev} | {nivel} | {descripcion}")
                    aviso_actividad = {
                        "tipo":            "actividad",
                        "nombre_terminal": t.nombre_red,
                        "dni_alumno":      alumno.dni,
                        "nombre_alumno":   alumno.nombre,
                        "tipo_evento":     tipo_ev,
                        "descripcion":     descripcion,
                        "nivel":           nivel,
                    }

                # Notificar al panel admin en tiempo real (fuera de la sesión de BD)
                if aviso_sospecha:
                    await manager._broadcast_admins(aviso_sospecha)
                if aviso_actividad:
                    await manager._broadcast_admins(aviso_actividad)

            elif tipo == "logout":
                logger.info(f"[WS] {terminal_id} logout recibido")
                async with async_session() as db:
                    t = await _buscar_terminal(db, terminal_id, terminal_ip)
                    if t:
                        sesion = await _sesion_activa_de_terminal(db, t.id)
                        if sesion:
                            ahora_logout = _cerrar_sesion(sesion, "logout")
                            await db.commit()
                            logger.info(f"[WS] {terminal_id} sesión cerrada: {ahora_logout.strftime('%I:%M:%S %p')}")
                            await manager.notificar_evento(f"SALIDA: Terminal {terminal_id} (manual logout)", "logout")
                        # Sesión cerrada: la tarjeta ya no debe mostrar programa en foco.
                        await manager.limpiar_foco(t.nombre_red)
                await manager.bloquear_terminal(terminal_id)
                await manager.notificar_admins()

    except WebSocketDisconnect:
        logger.info(f"[WS] Terminal desconectada (WebSocketDisconnect): {terminal_id}")
    except Exception as exc:
        logger.error(f"[WS] Error inesperado en {terminal_id}: {exc}", exc_info=True)
    finally:
        # Limpieza garantizada: se ejecuta en desconexión normal, timeout, apagado o error.
        # Si la PC ya reconectó por OTRO socket (el viejo tardó en morir, p.ej.
        # timeout de 60s tras un microcorte), el manager apunta a la conexión
        # nueva: no se toca nada, o se sacaría de la lista a la conexión viva y
        # se cerraría la sesión que el alumno sigue usando.
        _actual = manager.conexiones_activas.get(terminal_id)
        if _actual is not None and _actual is not websocket:
            logger.info(f"[WS] Socket viejo de {terminal_id} cerrado; ya hay una conexión nueva — sin limpieza")
        else:
            manager.desconectar(terminal_id)
            try:
                async with async_session() as db:
                    t = await _buscar_terminal(db, terminal_id, terminal_ip)
                    # Socket que nunca mandó 'hello' (clave = IP): la búsqueda por
                    # IP puede dar con una PC que SÍ está conectada por otro socket
                    # (misma IP). No marcarla offline ni cerrarle la sesión.
                    _viva = manager.conexiones_activas.get(t.nombre_red) if t else None
                    if _viva is not None and _viva is not websocket:
                        logger.info(f"[WS] Socket {terminal_id} cerrado; '{t.nombre_red}' sigue conectada por otro socket — sin limpieza")
                        t = None
                    if t:
                        t.estado = "offline"
                        t.ultima_conexion = datetime.now()   # "visto por última vez"
                        res_s = await db.execute(
                            select(Sesion).where(Sesion.id_terminal == t.id, Sesion.estado == "activa")
                        )
                        for sesion in res_s.scalars().all():
                            # 'desconexion_red' es reanudable dentro del período de gracia.
                            ahora = _cerrar_sesion(sesion, "desconexion_red")
                            logger.info(f"[WS] Sesión cerrada por desconexión en {terminal_id}: {ahora.strftime('%I:%M:%S %p')}")
                        await db.commit()
                        # Terminal caída: borrar su programa en foco del mapa en vivo.
                        await manager.limpiar_foco(t.nombre_red)
                await manager.notificar_evento(f"Terminal '{terminal_id}' perdió conexión", "offline")
                await manager.notificar_admins()
            except Exception as cleanup_exc:
                logger.error(f"[WS] Error en cleanup de {terminal_id}: {cleanup_exc}")


# ── WebSocket para panel admin ──────────────────────────────────────

async def _sede_de_terminal(tid: str, target: str):
    """id_sede de la PC por hostname o, si no, por IP. (False si no existe)."""
    async with async_session() as db:
        t = (await db.execute(select(Terminal).where(Terminal.nombre_red == tid))).scalars().first()
        if not t and target:
            t = (await db.execute(select(Terminal).where(Terminal.ip == target)
                                  .order_by(Terminal.ultima_conexion.desc()))).scalars().first()
        return t.id_sede if t else False


async def _fuera_de_sede(op_sede, tid: str, target: str) -> bool:
    """True si un encargado limitado a una sede intenta operar una PC de otra."""
    if not op_sede:
        return False
    return (await _sede_de_terminal(tid, target)) != op_sede


def _resolver_tid(target: str) -> str:
    """El panel identifica la PC por hostname o IP; devuelve el id de conexion."""
    if target in manager.conexiones_activas:
        return target
    for k, v in manager.terminal_ips.items():
        if v == target:
            return k
    return target


_ADMIN_REVALIDAR_SEG = 60
# Marcador: "no se pudo verificar (BD caída), conservar rol/sede actuales".
_USUARIO_SIN_CAMBIOS = object()


async def _usuario_panel_vigente(username: str):
    """Usuario si sigue existiendo, activo y con rol de panel; None si no;
    _USUARIO_SIN_CAMBIOS si la BD falló (no expulsar por un error transitorio)."""
    try:
        async with async_session() as db:
            u = (await db.execute(select(Usuario).where(Usuario.username == username))).scalar_one_or_none()
            if u is None or not u.activo or u.rol not in ("superadmin", "admin"):
                return None
            return u
    except Exception as e:
        logger.warning(f"[WS-Admin] No se pudo revalidar a {username}: {e}")
        return _USUARIO_SIN_CAMBIOS


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
    # Encargado con sede fija: solo puede operar las PCs de su sede (multi-sede).
    op_sede     = user.id_sede if user.rol != "superadmin" else None
    token_exp   = payload.get("exp")
    ultima_validacion = time.monotonic()
    manager._admins.append(websocket)
    logger.info(f"Panel admin conectado (autenticado): {op_username} [{op_rol}]")
    await manager._enviar_estado(websocket)
    try:
        while True:
            try:
                # Timeout para revalidar también a los paneles que solo miran
                # (sin mandar comandos) y seguir recibiendo pushes.
                data = await asyncio.wait_for(websocket.receive_json(), timeout=_ADMIN_REVALIDAR_SEG)
            except asyncio.TimeoutError:
                data = None
            except WebSocketDisconnect:
                raise
            except Exception:
                # Socket cerrado por el manager (panel lento): salir del loop.
                if websocket.application_state != WebSocketState.CONNECTED \
                        or websocket.client_state != WebSocketState.CONNECTED:
                    break
                await websocket.send_json({"tipo": "error", "motivo": "JSON inválido"})
                continue

            # ── Revalidación de la sesión del operador ──
            # Un JWT expirado o un usuario desactivado/degradado no puede seguir
            # operando PCs por un socket abierto horas atrás. Expiración: en cada
            # mensaje (barato). Estado en BD: como máximo cada 60 s.
            motivo_rechazo = None
            if token_exp is not None and time.time() >= float(token_exp):
                motivo_rechazo = "Sesión expirada. Vuelva a iniciar sesión."
            elif time.monotonic() - ultima_validacion >= _ADMIN_REVALIDAR_SEG:
                u = await _usuario_panel_vigente(op_username)
                if u is None:
                    motivo_rechazo = "Usuario desactivado o sin permisos."
                elif u is not _USUARIO_SIN_CAMBIOS:
                    ultima_validacion = time.monotonic()
                    op_rol = u.rol
                    op_sede = u.id_sede if u.rol != "superadmin" else None
            if motivo_rechazo:
                logger.warning(f"[WS-Admin] {op_username}: {motivo_rechazo} Se cierra el socket.")
                try:
                    await asyncio.wait_for(websocket.send_json({"tipo": "error", "motivo": motivo_rechazo}), 2)
                except Exception:
                    pass
                manager.desconectar_admin(websocket)
                try:
                    await websocket.close(code=4003, reason="Sesion invalida")
                except Exception:
                    pass
                break
            if data is None:
                continue

            tipo = data.get("tipo") if isinstance(data, dict) else None
            # Cada comando va aislado: un error de BD (u otro) en uno NO debe
            # cerrar el socket del panel; se registra y se responde con 'error'
            # (tipo que el panel ya muestra) y se sigue atendiendo.
            try:
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
                    if await _fuera_de_sede(op_sede, tid, target):
                        await websocket.send_json({"tipo": "error", "motivo": "Esa PC pertenece a otra sede"})
                        continue
                    ok = await manager.bloquear_terminal(tid)
                    # Acción atómica: bloquear terminal + cerrar sesión activa en una transacción
                    async with async_session() as db:
                        res = await db.execute(select(Terminal).where(Terminal.nombre_red == tid))
                        t = res.scalar_one_or_none()
                        if not t:
                            res2 = await db.execute(select(Terminal).where(Terminal.ip == target).order_by(Terminal.ultima_conexion.desc()))
                            t = res2.scalars().first()  # la IP no es única (DHCP)
                        if t:
                            t.estado = "bloqueado"
                            # Cerrar TODAS las activas de la PC (si quedaron duplicadas,
                            # ninguna debe sobrevivir al bloqueo).
                            res_s = await db.execute(
                                select(Sesion).where(Sesion.id_terminal == t.id, Sesion.estado == "activa")
                            )
                            for sesion_activa in res_s.scalars().all():
                                ahora_bloqueo = _cerrar_sesion(sesion_activa, "bloqueo_admin")
                                logger.info(f"[WS-Admin] Sesión cerrada por bloqueo admin en {tid}: {ahora_bloqueo.strftime('%I:%M:%S %p')}")
                            await registrar_auditoria(op_username, "bloquear_terminal", rol=op_rol,
                                                      objetivo=tid, db=db)
                            await db.commit()
                            # Bloqueada: la tarjeta ya no muestra programa en foco.
                            await manager.limpiar_foco(t.nombre_red)
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
                    if await _fuera_de_sede(op_sede, tid, target):
                        await websocket.send_json({"tipo": "error", "motivo": "Esa PC pertenece a otra sede"})
                        continue
                    async with async_session() as db:
                        # PASO 1: Buscar alumno por DNI, luego por código de matrícula
                        res_a = await db.execute(select(AlumnoMaestro).where(AlumnoMaestro.dni == dni_param))
                        alumno = res_a.scalar_one_or_none()
                        if alumno is None:
                            res_a2 = await db.execute(select(AlumnoMaestro).where(AlumnoMaestro.codigo == dni_param))
                            alumno = res_a2.scalars().first()   # el código no es único

                        if alumno is None:
                            logger.warning(f"[WS-Admin] DNI={_mask_dni(dni_param)} no en maestro — acceso denegado")
                            await websocket.send_json({"tipo": "error", "motivo": f"El DNI {dni_param} no esta registrado en la base de datos local"})
                            continue

                        if alumno is None:
                            await websocket.send_json({"tipo": "error", "motivo": f"Error: El DNI {dni_param} no existe"})
                            continue

                        # BLOQUEO POR BAN: un alumno sancionado no puede entrar por
                        # NINGUNA vía. El kiosco ya lo bloquea en login_request; aquí
                        # se replica para que el desbloqueo manual del admin tampoco
                        # evada la sanción (M-2).
                        ban_adm = await _ban_activo(db, alumno.dni)
                        if ban_adm:
                            motivo_ban = ban_adm.motivo or "Acceso restringido"
                            fecha_fin_ban = ban_adm.fecha_fin.strftime("%d/%m/%Y") if ban_adm.fecha_fin else "indefinido"
                            logger.warning(f"[WS-Admin] DNI={_mask_dni(alumno.dni)} BANEADO — desbloqueo denegado ({motivo_ban})")
                            await websocket.send_json({
                                "tipo": "error",
                                "motivo": f"{alumno.nombre} tiene un ban activo: {motivo_ban}. Expira: {fecha_fin_ban}. No puede usar las PCs.",
                            })
                            continue

                        # BLOQUEO POR VIGENCIA: una credencial vencida NO puede
                        # desbloquear la PC. Se avisa al operador que el carnet venció
                        # y debe renovarse. (La renovación se hace desde Base de Datos.)
                        from api.endpoints import _vigencia_meses, _vencido_efectivo, _venc_efectivo
                        _meses_vig = await _vigencia_meses(db)
                        if _vencido_efectivo(alumno.fecha_alta, alumno.fecha_caducidad, _meses_vig):
                            _venc = _venc_efectivo(alumno.fecha_alta, alumno.fecha_caducidad, _meses_vig)
                            _venc_txt = _venc.strftime("%d/%m/%Y") if _venc else ""
                            logger.warning(f"[WS-Admin] DNI={_mask_dni(alumno.dni)} carnet VENCIDO ({_venc_txt}) — desbloqueo denegado")
                            await websocket.send_json({
                                "tipo": "error",
                                "motivo": f"Carnet VENCIDO el {_venc_txt}. {alumno.nombre} debe renovar su carnet en biblioteca para poder usar las PCs.",
                            })
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
                            res_t2 = await db.execute(select(Terminal).where(Terminal.ip == target).order_by(Terminal.ultima_conexion.desc()))
                            terminal_db = res_t2.scalars().first()  # la IP no es única (DHCP)
                        if not terminal_db:
                            await websocket.send_json({"tipo": "error", "motivo": f"Terminal '{tid}' no registrada"})
                            continue

                        # Sesión única (misma regla que el login del kiosco): el alumno
                        # sale de cualquier otra PC, y la PC destino no conserva
                        # sesiones previas de otro alumno. Antes, comprobar que la PC
                        # destino esté conectada: no sacar al alumno de su otra PC si
                        # el desbloqueo no va a poder entregarse.
                        if tid not in manager.conexiones_activas:
                            await websocket.send_json({"tipo": "error", "motivo": f"Terminal '{tid}' no está conectada"})
                            continue
                        await _aplicar_sesion_unica(db, alumno.dni, terminal_destino_id=terminal_db.id)
                        await _cerrar_activas_de_terminal(db, terminal_db.id, "reemplazada_en_terminal")

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
                    if await _fuera_de_sede(op_sede, tid, target):
                        await websocket.send_json({"tipo": "error", "motivo": "Esa PC pertenece a otra sede"})
                        continue
                    ok = await manager.enviar_comando(tid, {"tipo": "remote_command", "action": action})
                    if ok:
                        await registrar_auditoria(op_username, "apagar_terminal", rol=op_rol, objetivo=tid)
                        await websocket.send_json({"tipo": "ok", "mensaje": f"Comando '{action}' enviado a {tid}"})
                    else:
                        await websocket.send_json({"tipo": "error", "motivo": f"Terminal {tid} no conectada"})

                elif tipo == "bloquear_todas":
                    ahora_bloqueo_todas = datetime.now().replace(tzinfo=None)
                    # Con varias sedes, "Bloquear sala" bloquea UNA sede: la del
                    # encargado, o la que el superadmin tiene elegida en Monitoreo.
                    # Sin sede (instalacion de una sola sala) bloquea todo, como antes.
                    sede_bloqueo = op_sede
                    if not sede_bloqueo and op_rol == "superadmin":
                        try:
                            sede_bloqueo = int(data.get("id_sede") or 0) or None
                        except (TypeError, ValueError):
                            sede_bloqueo = None
                    # IPs y nombres de terminales con conexión WS activa en este momento
                    ids_conectados = set(manager.conexiones_activas.keys())
                    ips_conectadas = set(manager.terminal_ips.values())
                    if sede_bloqueo:
                        async with async_session() as db:
                            de_sede = (await db.execute(select(Terminal.nombre_red, Terminal.ip)
                                                        .where(Terminal.id_sede == sede_bloqueo))).all()
                        nombres_sede = {r[0] for r in de_sede}
                        ips_sede = {r[1] for r in de_sede}
                        ids_conectados = {k for k in ids_conectados
                                          if k in nombres_sede or manager.terminal_ips.get(k) in ips_sede}
                        ips_conectadas = {manager.terminal_ips.get(k) for k in ids_conectados} - {None}

                    cerradas = 0
                    focos_bloqueo = []
                    async with async_session() as db:
                        # Solo las terminales conectadas (de la sede, si aplica), en
                        # UNA consulta; y sus sesiones activas en OTRA (antes: una
                        # consulta por sesión, N+1 con cientos de PCs).
                        terminales_afectadas = []
                        if ids_conectados or ips_conectadas:
                            cond = Terminal.nombre_red.in_(list(ids_conectados)) if ids_conectados else None
                            if ips_conectadas:
                                c_ip = Terminal.ip.in_(list(ips_conectadas))
                                cond = c_ip if cond is None else (cond | c_ip)
                            terminales_afectadas = (await db.execute(select(Terminal).where(cond))).scalars().all()
                        # Las offline/desconectadas conservan su estado actual
                        for t in terminales_afectadas:
                            t.estado = "bloqueado"
                        ids_terminal = [t.id for t in terminales_afectadas]
                        if ids_terminal:
                            nombre_por_id = {t.id: t.nombre_red for t in terminales_afectadas}
                            res_s = await db.execute(select(Sesion).where(
                                Sesion.estado == "activa", Sesion.id_terminal.in_(ids_terminal)))
                            for sesion_activa in res_s.scalars().all():
                                _cerrar_sesion(sesion_activa, "bloqueo_admin")
                                sesion_activa.hora_salida = ahora_bloqueo_todas
                                focos_bloqueo.append(nombre_por_id.get(sesion_activa.id_terminal))
                                cerradas += 1
                        logger.warning(f"[AUDIT] {op_username} [{op_rol}] ejecutó BLOQUEO GLOBAL: {cerradas} sesión(es) cerrada(s) (solo conectadas)")
                        await registrar_auditoria(op_username, "bloquear_todas", rol=op_rol,
                                                  objetivo=f"sede {sede_bloqueo}" if sede_bloqueo else "toda la sala",
                                                  detalle=f"{cerradas} sesión(es) cerrada(s)", db=db)
                        await db.commit()

                    # Enviar "bloquear" en paralelo (con timeout por PC) solo a los
                    # kioscos conectados de la sede, o a todos si no hay sede.
                    if sede_bloqueo:
                        await manager.bloquear_todas(ids_conectados)
                    else:
                        await manager.bloquear_todas()
                    for _nr in set(focos_bloqueo) - {None}:
                        await manager.limpiar_foco(_nr)
                    await manager.notificar_evento(f"BLOQUEO GLOBAL: {cerradas} sesión(es) cerrada(s) ({len(ids_conectados)} terminal(es) conectada(s))", "warning")
                    await websocket.send_json({"tipo": "ok", "mensaje": f"Terminales conectadas bloqueadas ({cerradas} sesión(es) cerrada(s))"})
                    await manager.notificar_admins()

                logger.info(f"[WS-Admin] comando: {tipo}")
            except WebSocketDisconnect:
                raise
            except Exception as exc_cmd:
                logger.error(f"[WS-Admin] Error procesando '{tipo}' de {op_username}: {exc_cmd}", exc_info=True)
                try:
                    await websocket.send_json({"tipo": "error", "motivo": f"Error interno al procesar '{tipo}'. Intente de nuevo."})
                except Exception:
                    raise WebSocketDisconnect()   # socket muerto: salir del loop

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error(f"[WS-Admin] Conexión de {op_username} terminada por error: {exc}", exc_info=True)
    finally:
        # Siempre sacar el socket de la lista de difusión, salga como salga.
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
    ban = await _ban_activo(db, dni)
    if ban:
        return {"estado": "baneado", "motivo": f"Acceso restringido: {ban.motivo}"}

    # Buscar terminal por nombre. NO se crea aquí (G-1): una sesión offline
    # legítima proviene de una PC que ya estaba registrada. Si no existe, se
    # rechaza para no permitir crear terminales fantasma sin autenticación.
    nombre_terminal = _limpio(datos.terminal, 120).strip()
    res_t = await db.execute(select(Terminal).where(Terminal.nombre_red == nombre_terminal))
    terminal = res_t.scalar_one_or_none()
    if terminal is None:
        logger.warning(f"[OfflineSync] Terminal desconocida rechazada: {nombre_terminal!r}")
        return {"estado": "terminal_desconocida", "motivo": "Terminal no registrada en el sistema."}

    # Mitigación sin token: solo la propia PC puede sincronizar su sesión. La IP
    # TCP real de la petición debe ser la que el servidor registró para esa
    # terminal (la fija el WS con la IP de conexión, no la que dice el cliente).
    # También vale la IP de su WS vivo (si DHCP le cambió la IP durante el corte,
    # el 'hello' ya la actualizó en memoria aunque la fila aún no).
    ip_peticion = rate_limit._ip_cliente(request)
    ip_ok = bool(terminal.ip) and ip_peticion == terminal.ip
    ip_ok = ip_ok or manager.terminal_ips.get(terminal.nombre_red) == ip_peticion
    if not ip_ok:
        logger.warning(f"[OfflineSync] Rechazado: '{nombre_terminal}' registrada con IP={terminal.ip} "
                       f"pero la petición vino de {ip_peticion}")
        return {"estado": "rechazado", "motivo": "La petición no proviene de la terminal indicada."}

    # Parsear hora de inicio (retroactiva). NOTA sobre hora_fin (G-8): el cliente
    # envía hora_fin al RECONECTAR, pero su intención es CONTINUAR la sesión (que
    # el alumno siga trabajando), no cerrarla. Por eso la sesión se registra como
    # 'activa' y hora_fin se ignora a propósito aquí: la sesión se cerrará por el
    # flujo normal (logout, bloqueo, desconexión) cuando realmente termine.
    from datetime import timedelta
    ahora = datetime.now()
    try:
        hora_inicio = datetime.strptime(datos.hora_inicio, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        hora_inicio = ahora
    # Acotar la hora que manda el cliente: nunca futura ni de más de 24 h atrás
    # (un reloj desfasado o un valor manipulado inflarían las horas de uso).
    hora_inicio = max(ahora - timedelta(hours=24), min(hora_inicio, ahora))

    # Mismas reglas que el login: sesión única por alumno y una sola sesión viva
    # por PC.
    await _aplicar_sesion_unica(db, dni, terminal_destino_id=terminal.id)
    await _cerrar_activas_de_terminal(db, terminal.id, "reemplazada_en_terminal")

    # Registrar sesión activa con hora de entrada retroactiva.
    # fecha_uso usa la fecha del servidor (no del cliente) para que los filtros
    # de historial por día/mes/año funcionen correctamente independiente
    # de la zona horaria del kiosco.
    sesion = Sesion(
        dni_alumno   = dni,
        id_terminal  = terminal.id,
        hora_entrada = hora_inicio,
        razon_uso    = _limpio(datos.razon, 200) or None,
        estado       = "activa",
        confirmada   = True,
        fecha_uso    = ahora.date(),
    )
    db.add(sesion)
    # Solo se marca en uso si el kiosco está conectado ahora mismo; si no, la
    # terminal conserva su estado (offline) hasta que el WS la registre.
    conectada = terminal.nombre_red in manager.conexiones_activas
    if conectada:
        terminal.estado = "activo"   # Terminal usa "activo" (masculino); "activa" es de Sesion
        terminal.ultima_conexion = ahora
    await db.commit()
    await db.refresh(sesion)

    logger.info(f"[OfflineSync] Sesión offline sincronizada: DNI={_mask_dni(dni)} terminal={nombre_terminal} "
                f"inicio={hora_inicio:%Y-%m-%d %H:%M:%S} conectada={conectada}")

    # Notificar al kiosco que la sesión fue confirmada — le pasa el nombre del alumno
    # para que la UI se actualice con la bienvenida normal. El manager indexa
    # por hostname (nombre_red), no por IP: antes este envío nunca llegaba.
    await manager.enviar_comando(terminal.nombre_red, {
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
    # ws_max_size: tope de 64 KB por mensaje WebSocket (el default de uvicorn es
    # 16 MB). Ningún mensaje legítimo de kiosco o panel pasa de unos pocos KB;
    # así un cliente falso no puede hacer que el servidor bufferice megas por
    # mensaje. Un solo proceso: NUNCA workers>1 (el estado WS vive en memoria).
    # Si se arranca con la CLI de uvicorn, pasar --ws-max-size 65536.
    uvicorn.run("main:app", host=_host, port=_port, reload=False, ws_max_size=64 * 1024)
