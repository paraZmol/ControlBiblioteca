# endpoints.py - Rutas de la API REST
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models import Alumno, Terminal, Sesion, Usuario
from auth_service import (
    verificar_password, hashear_password, crear_token, obtener_usuario_actual
)
from pydantic import BaseModel

router = APIRouter(prefix="/api")


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
        terminal.ultima_conexion = datetime.utcnow()
        terminal.estado = "bloqueado"
    else:
        terminal = Terminal(nombre=datos.nombre, ip=datos.ip, estado="bloqueado", ultima_conexion=datetime.utcnow())
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

    # Crear sesión
    sesion = Sesion(alumno_id=alumno.id, terminal_id=terminal.id)
    terminal.estado = "activo"
    db.add(sesion)
    await db.flush()
    return {"sesion_id": sesion.id, "mensaje": "Sesión iniciada", "alumno": f"{alumno.nombres} {alumno.apellidos}"}


@router.post("/sesiones/{sesion_id}/cerrar")
async def cerrar_sesion(
    sesion_id: int,
    motivo: str = "manual",
    db: AsyncSession = Depends(get_db)
):
    """Cerrar una sesión activa."""
    result = await db.execute(select(Sesion).where(Sesion.id == sesion_id, Sesion.activa == True))
    sesion = result.scalar_one_or_none()
    if not sesion:
        raise HTTPException(status_code=404, detail="Sesión no encontrada o ya cerrada")

    sesion.fin = datetime.utcnow()
    sesion.activa = False
    sesion.motivo_cierre = motivo

    # Bloquear terminal
    await db.execute(
        update(Terminal).where(Terminal.id == sesion.terminal_id).values(estado="bloqueado")
    )
    return {"mensaje": "Sesión cerrada", "duracion_min": int((sesion.fin - sesion.inicio).total_seconds() / 60)}


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
            "razon_uso": s.razon_uso or ""
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
