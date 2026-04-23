# endpoints.py - Rutas de la API REST
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models import Alumno, Terminal, Sesion, Usuario, ahora_lima
import pytz
import socket
from auth_service import (
    verificar_password, hashear_password, crear_token, obtener_usuario_actual
)
from core.websocket_manager import manager
from pydantic import BaseModel

router = APIRouter(prefix="/api")


def _obtener_ip_local() -> str:
    """Detecta la IP de la interfaz de red local (no 127.0.0.1)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


# ── Esquemas Pydantic ──────────────────────────────────────────────

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class AlumnoAuth(BaseModel):
    codigo: str

class TerminalRegistro(BaseModel):
    nombre: str
    ip: str

class SesionResponse(BaseModel):
    id: int
    alumno_codigo: str
    alumno_nombre: str
    terminal_nombre: str
    inicio: datetime
    activa: bool

class UsuarioCrear(BaseModel):
    username: str
    password: str
    nombre_completo: str | None = None
    rol: str = "encargado"


# ── Server Info ────────────────────────────────────────────────────

@router.get("/server-info")
async def server_info():
    """Retorna la IP real de la interfaz de red del servidor."""
    return {"ip": _obtener_ip_local()}


# ── Auth ────────────────────────────────────────────────────────────

@router.post("/auth/login", response_model=LoginResponse)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    """Iniciar sesión como administrador/encargado."""
    result = await db.execute(select(Usuario).where(Usuario.username == form.username))
    usuario = result.scalar_one_or_none()
    if not usuario or not verificar_password(form.password, usuario.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales incorrectas")
    token = crear_token(data={"sub": usuario.username, "rol": usuario.rol})
    return LoginResponse(access_token=token)


@router.post("/auth/registro", status_code=201)
async def registrar_usuario(
    datos: UsuarioCrear,
    db: AsyncSession = Depends(get_db),
    admin: Usuario = Depends(obtener_usuario_actual)
):
    """Registrar nuevo usuario (solo admins)."""
    if admin.rol != "admin":
        raise HTTPException(status_code=403, detail="Solo administradores pueden crear usuarios")
    nuevo = Usuario(
        username=datos.username,
        hashed_password=hashear_password(datos.password),
        nombre_completo=datos.nombre_completo,
        rol=datos.rol
    )
    db.add(nuevo)
    await db.flush()
    return {"mensaje": f"Usuario '{datos.username}' creado"}


# ── Alumnos ─────────────────────────────────────────────────────────

@router.post("/alumnos/validar")
async def validar_alumno(datos: AlumnoAuth, db: AsyncSession = Depends(get_db)):
    """Validar código de alumno para desbloquear terminal."""
    result = await db.execute(select(Alumno).where(Alumno.codigo == datos.codigo))
    alumno = result.scalar_one_or_none()
    if not alumno:
        raise HTTPException(status_code=404, detail="Alumno no encontrado")
    if not alumno.habilitado:
        raise HTTPException(status_code=403, detail="Alumno no habilitado")
    return {
        "valido": True,
        "codigo": alumno.codigo,
        "nombres": alumno.nombres,
        "apellidos": alumno.apellidos,
        "escuela": alumno.escuela
    }


@router.get("/alumnos")
async def listar_alumnos(
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(obtener_usuario_actual)
):
    """Listar todos los alumnos en caché local."""
    result = await db.execute(select(Alumno).order_by(Alumno.apellidos))
    return [
        {
            "id": a.id, "codigo": a.codigo,
            "nombres": a.nombres, "apellidos": a.apellidos,
            "escuela": a.escuela, "habilitado": a.habilitado
        }
        for a in result.scalars().all()
    ]


# ── Terminales ──────────────────────────────────────────────────────

@router.get("/terminales")
async def listar_terminales(
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(obtener_usuario_actual)
):
    """Listar estado de todas las terminales."""
    result = await db.execute(select(Terminal).order_by(Terminal.nombre))
    return [
        {
            "id": t.id, "nombre": t.nombre, "ip": t.ip,
            "estado": t.estado, "ultima_conexion": t.ultima_conexion
        }
        for t in result.scalars().all()
    ]


@router.post("/terminales/registrar")
async def registrar_terminal(
    datos: TerminalRegistro,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(obtener_usuario_actual)
):
    """Registrar o actualizar terminal al conectarse."""
    result = await db.execute(select(Terminal).where(Terminal.ip == datos.ip))
    terminal = result.scalar_one_or_none()
    if terminal:
        terminal.nombre = datos.nombre
        terminal.ultima_conexion = ahora_lima()
        terminal.estado = "bloqueado"
    else:
        terminal = Terminal(nombre=datos.nombre, ip=datos.ip, estado="bloqueado", ultima_conexion=ahora_lima())
        db.add(terminal)
    await db.flush()
    return {"id": terminal.id, "nombre": terminal.nombre}


# ── Sesiones ────────────────────────────────────────────────────────

@router.post("/sesiones/iniciar")
async def iniciar_sesion(
    alumno_codigo: str,
    terminal_ip: str,
    db: AsyncSession = Depends(get_db)
):
    """Iniciar sesión de uso en una terminal."""
    # Buscar alumno
    res_alumno = await db.execute(select(Alumno).where(Alumno.codigo == alumno_codigo))
    alumno = res_alumno.scalar_one_or_none()
    if not alumno or not alumno.habilitado:
        raise HTTPException(status_code=403, detail="Alumno no válido o no habilitado")

    # Buscar terminal
    res_terminal = await db.execute(select(Terminal).where(Terminal.ip == terminal_ip))
    terminal = res_terminal.scalar_one_or_none()
    if not terminal:
        raise HTTPException(status_code=404, detail="Terminal no registrada")

    # Crear sesión con datos desnormalizados (snapshot) del alumno
    sesion = Sesion(
        alumno_id=alumno.id,
        terminal_id=terminal.id,
        dni=alumno.codigo,
        facultad=alumno.facultad,
        escuela=alumno.escuela,
        inicio=ahora_lima()
    )
    terminal.estado = "activo"
    db.add(sesion)
    await db.flush()
    return {"sesion_id": sesion.id, "mensaje": "Sesión iniciada", "alumno": f"{alumno.nombres} {alumno.apellidos}"}


@router.post("/sesiones/{sesion_id}/cerrar")
async def cerrar_sesion(
    sesion_id: int,
    motivo: str = "manual",
    hora_salida: str = None,
    db: AsyncSession = Depends(get_db)
):
    """Cerrar una sesión activa."""
    result = await db.execute(select(Sesion).where(Sesion.id == sesion_id, Sesion.activa == True))
    sesion = result.scalar_one_or_none()
    if not sesion:
        raise HTTPException(status_code=404, detail="Sesión no encontrada o ya cerrada")

    # Usar hora_salida del administrador si se proporciona, sino hora del servidor
    if hora_salida:
        try:
            # Intentar parsear el formato locale del navegador (ej: "21/4/2026, 15:34:45" o "2026-04-21T...")
            import dateutil.parser
            sesion.hora_salida = dateutil.parser.parse(hora_salida, dayfirst=True)
        except:
            sesion.hora_salida = ahora_lima()
    else:
        sesion.hora_salida = ahora_lima()
    
    sesion.fin = sesion.hora_salida # Sincronizar fin con hora_salida
    sesion.activa = False
    sesion.motivo_cierre = motivo

    # Bloquear terminal
    await db.execute(
        update(Terminal).where(Terminal.id == sesion.terminal_id).values(estado="bloqueado")
    )
    # Asegurar que ambos sean offset-naive para el cálculo
    inicio_naive = sesion.inicio.replace(tzinfo=None) if sesion.inicio else ahora_lima().replace(tzinfo=None)
    fin_naive = sesion.fin.replace(tzinfo=None) if sesion.fin else ahora_lima().replace(tzinfo=None)
    
    return {"mensaje": "Sesión cerrada", "duracion_min": int((fin_naive - inicio_naive).total_seconds() / 60)}


@router.get("/sesiones/activas")
async def sesiones_activas(
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(obtener_usuario_actual)
):
    """Listar sesiones activas (para dashboard admin)."""
    result = await db.execute(
        select(Sesion, Alumno, Terminal)
        .join(Alumno)
        .join(Terminal)
        .where(Sesion.activa == True)
        .order_by(Sesion.inicio.desc())
    )
    return [
        {
            "id": s.id, "inicio": s.inicio,
            "fin": s.fin,
            "fecha_uso": s.fecha_uso or s.inicio.date() if s.inicio else None,
            "alumno_codigo": a.codigo,
            "alumno_nombre": f"{a.nombres} {a.apellidos}",
            "dni": s.dni or a.codigo,
            "facultad": s.facultad or "",
            "escuela": s.escuela or a.escuela or "",
            "terminal_nombre": t.nombre,
            "terminal_ip": t.ip,
            "razon_uso": s.razon_uso or "",
            "hora_salida": s.hora_salida
        }
        for s, a, t in result.all()
    ]


# ── Dashboard Stats ─────────────────────────────────────────────────

@router.get("/dashboard/stats")
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(obtener_usuario_actual)
):
    """Estadísticas para el panel de administración."""
    from sqlalchemy import func

    # Total terminales y estados
    terminales = await db.execute(select(func.count(Terminal.id)))
    total_terminales = terminales.scalar()

    activas_q = await db.execute(select(func.count(Terminal.id)).where(Terminal.estado == "activo"))
    terminales_activas = activas_q.scalar()

    # Sesiones activas
    sesiones_q = await db.execute(select(func.count(Sesion.id)).where(Sesion.activa == True))
    sesiones_activas_count = sesiones_q.scalar()

    # Total alumnos
    alumnos_q = await db.execute(select(func.count(Alumno.id)))
    total_alumnos = alumnos_q.scalar()

    return {
        "total_terminales": total_terminales,
        "terminales_activas": terminales_activas,
        "sesiones_activas": sesiones_activas_count,
        "total_alumnos": total_alumnos
    }


@router.post("/admin/cerrar-todas")
async def cerrar_todas_sesiones(
    db: AsyncSession = Depends(get_db),
    admin: Usuario = Depends(obtener_usuario_actual)
):
    """Cierra todas las sesiones activas en el sistema."""
    if admin.rol != "admin":
        raise HTTPException(status_code=403, detail="No tiene permisos para esta acción")
    
    # Buscar todas las sesiones activas
    res = await db.execute(select(Sesion).where(Sesion.activa == True))
    sesiones = res.scalars().all()
    
    ahora = datetime.utcnow().replace(tzinfo=None)
    for s in sesiones:
        s.activa = False
        s.fin = ahora
        s.hora_salida = ahora
        s.motivo_cierre = "admin_bulk"
    
    # Bloquear todas las terminales conectadas a nivel de base de datos
    await db.execute(update(Terminal).values(estado="bloqueado"))
    
    await db.commit()
    
    # Notificar a las terminales físicamente vía WS
    from core.websocket_manager import manager
    await manager.bloquear_todas()
    await manager.notificar_evento(f"Administrador cerró TODAS las sesiones activas ({len(sesiones)} sesiones)", "warning")
    await manager.notificar_admins()
    
    return {"mensaje": f"Se han cerrado {len(sesiones)} sesiones correctamente"}


@router.delete("/admin/reset-total")
async def reset_total(
    db: AsyncSession = Depends(get_db),
    admin: Usuario = Depends(obtener_usuario_actual)
):
    """Vaciado total de las tablas de sesiones, alumnos y terminales."""
    from main import logger
    logger.info(f"[ADMIN] Usuario '{admin.username}' solicitó RESET TOTAL")
    
    if admin.rol != "admin":
        logger.warning(f"[ADMIN] Intento de reset rechazado para usuario '{admin.username}' (no es admin)")
        raise HTTPException(status_code=403, detail="No tiene permisos para esta acción")
    
    # El orden importa por las claves foráneas
    await db.execute(delete(Sesion))
    await db.execute(delete(Alumno))
    await db.execute(delete(Terminal))
    
    # Desconectar físicamente todas las terminales para que no se re-registren solo por heartbeat
    await manager.desconectar_todo()
    
    await db.commit()
    await manager.notificar_evento("🧹 RESET TOTAL: El sistema ha sido reseteado por el administrador", "warning")
    return {"mensaje": "Todo el sistema ha sido limpiado correctamente"}
