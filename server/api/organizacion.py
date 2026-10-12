# organizacion.py - Sedes (centros de computo), ciclos academicos y reportes
#
# Multi-sede: un unico servidor central atiende a la Biblioteca Central y a los
# centros de computo de las facultades. Cada PC (terminal) pertenece a una sede;
# un encargado puede estar limitado a una sede (usuarios.id_sede) y entonces sus
# reportes e incidencias se filtran a esa sede. El superadmin ve todo.
#
# Ciclos: calendario academico de la UNASAM (2026-0, 2026-I, 2026-II...) con
# fechas exactas, configurado por el superadmin. Los reportes por ciclo lo usan.
from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, case, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import (
    Sede, CicloAcademico, Terminal, Sesion, AlumnoMaestro, Escuela, Facultad,
    Incidencia, Ban, Usuario,
)
from auth_service import obtener_usuario_actual
from core.auditoria import registrar_auditoria

router = APIRouter(prefix="/api")

# Tope por sesion al sumar horas de uso: una sesion colgada (corte de luz,
# cierre sin logout) no debe inflar las estadisticas con dias enteros.
_MAX_MIN_SESION = 720
# Tope de filas del listado imprimible de incidencias.
_MAX_FILAS_INCIDENCIAS = 5000
# Rango maximo de un reporte (los agregados son en SQL, pero un rango absurdo
# suele ser un error de tipeo).
_MAX_DIAS_REPORTE = 3 * 366


def _exigir_superadmin(u: Usuario):
    if u.rol != "superadmin":
        raise HTTPException(status_code=403, detail="Solo el superadmin puede realizar esta acción")


def _exigir_panel(u: Usuario):
    if u.rol not in ("superadmin", "admin"):
        raise HTTPException(status_code=403, detail="Acceso denegado")


def sede_efectiva(usuario: Usuario, id_sede_pedida: int | None) -> int | None:
    """Sede a la que se limita una consulta. Un encargado asignado a una sede
    SIEMPRE queda limitado a ella (ignora lo que pida); el superadmin y los
    encargados sin sede pueden elegir (None = todas)."""
    if usuario.rol != "superadmin" and getattr(usuario, "id_sede", None):
        return usuario.id_sede
    return id_sede_pedida or None


def _parse_fecha(valor: str | None, campo: str) -> date:
    try:
        return datetime.strptime(valor or "", "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Fecha '{campo}' inválida (use AAAA-MM-DD)")


# ══════════════════════════════════════════════════════════════════════
#  SEDES
# ══════════════════════════════════════════════════════════════════════

class SedeReq(BaseModel):
    nombre: str
    descripcion: str | None = None
    redes: str | None = None


def _normalizar_redes(redes: str | None) -> str | None:
    """'10.21.1., 10.21.2' -> '10.21.1.,10.21.2.' (cada prefijo termina en punto
    para que '10.21.1' no capture tambien 10.21.10x)."""
    if not redes:
        return None
    out = []
    for p in redes.replace(";", ",").split(","):
        p = p.strip()
        if not p:
            continue
        if not all(c.isdigit() or c == "." for c in p):
            raise HTTPException(status_code=422, detail=f"Prefijo de red inválido: '{p}' (ej: 10.21.1.)")
        if not p.endswith("."):
            p += "."
        if p not in out:
            out.append(p)
    return ",".join(out)[:300] or None


@router.get("/admin/sedes")
async def listar_sedes(
    incluir_inactivas: bool = False,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    _exigir_panel(usuario)
    q = select(Sede).order_by(Sede.nombre)
    if not incluir_inactivas:
        q = q.where(Sede.activo == True)
    sedes = (await db.execute(q)).scalars().all()
    conteo = dict((await db.execute(
        select(Terminal.id_sede, func.count(Terminal.id)).group_by(Terminal.id_sede)
    )).all())
    return {
        "sin_sede": int(conteo.get(None, 0)),
        "mi_sede": sede_efectiva(usuario, None),
        "items": [{
            "id": s.id, "nombre": s.nombre, "descripcion": s.descripcion or "",
            "redes": s.redes or "", "activo": bool(s.activo),
            "terminales": int(conteo.get(s.id, 0)),
        } for s in sedes],
    }


@router.post("/admin/sedes", status_code=201)
async def crear_sede(
    datos: SedeReq,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    _exigir_superadmin(usuario)
    nombre = (datos.nombre or "").strip()
    if len(nombre) < 3:
        raise HTTPException(status_code=422, detail="El nombre de la sede debe tener al menos 3 caracteres")
    if (await db.execute(select(Sede).where(Sede.nombre == nombre))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Ya existe una sede con ese nombre")
    s = Sede(nombre=nombre[:150], descripcion=(datos.descripcion or "").strip()[:300] or None,
             redes=_normalizar_redes(datos.redes), activo=True)
    db.add(s)
    await registrar_auditoria(usuario.username, "crear_sede", rol=usuario.rol, objetivo=nombre, db=db)
    await db.commit()
    return {"mensaje": f"Sede '{nombre}' creada", "id": s.id}


@router.put("/admin/sedes/{sede_id}")
async def editar_sede(
    sede_id: int, datos: SedeReq,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    _exigir_superadmin(usuario)
    s = (await db.execute(select(Sede).where(Sede.id == sede_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Sede no encontrada")
    nombre = (datos.nombre or "").strip()
    if len(nombre) < 3:
        raise HTTPException(status_code=422, detail="El nombre de la sede debe tener al menos 3 caracteres")
    otra = (await db.execute(select(Sede).where(Sede.nombre == nombre, Sede.id != sede_id))).scalar_one_or_none()
    if otra:
        raise HTTPException(status_code=409, detail="Ya existe otra sede con ese nombre")
    s.nombre = nombre[:150]
    s.descripcion = (datos.descripcion or "").strip()[:300] or None
    s.redes = _normalizar_redes(datos.redes)
    await registrar_auditoria(usuario.username, "editar_sede", rol=usuario.rol, objetivo=nombre, db=db)
    await db.commit()
    return {"mensaje": "Sede actualizada"}


@router.delete("/admin/sedes/{sede_id}")
async def desactivar_sede(
    sede_id: int,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    """Baja logica: las PCs, sesiones e incidencias conservan su sede para el
    historial. No se permite dar de baja la ultima sede activa."""
    _exigir_superadmin(usuario)
    s = (await db.execute(select(Sede).where(Sede.id == sede_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Sede no encontrada")
    activas = (await db.execute(select(func.count(Sede.id)).where(Sede.activo == True))).scalar() or 0
    if s.activo and activas <= 1:
        raise HTTPException(status_code=409, detail="No se puede dar de baja la única sede activa")
    # Una sede de baja con encargados o PCs dejaria a esos encargados limitados
    # a una sede invisible (sin poder corregirlo) y a las PCs fuera de los filtros.
    n_enc = (await db.execute(select(func.count(Usuario.id)).where(
        Usuario.id_sede == sede_id, Usuario.activo == True))).scalar() or 0
    n_pcs = (await db.execute(select(func.count(Terminal.id)).where(Terminal.id_sede == sede_id))).scalar() or 0
    if n_enc or n_pcs:
        partes = []
        if n_pcs: partes.append(f"{n_pcs} PC(s)")
        if n_enc: partes.append(f"{n_enc} encargado(s)")
        raise HTTPException(status_code=409,
            detail=f"La sede tiene asignados: {' y '.join(partes)}. Muévelos a otra sede antes de darla de baja.")
    s.activo = False
    await registrar_auditoria(usuario.username, "desactivar_sede", rol=usuario.rol, objetivo=s.nombre, db=db)
    await db.commit()
    return {"mensaje": f"Sede '{s.nombre}' dada de baja"}


@router.post("/admin/sedes/{sede_id}/reactivar")
async def reactivar_sede(
    sede_id: int,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    _exigir_superadmin(usuario)
    s = (await db.execute(select(Sede).where(Sede.id == sede_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Sede no encontrada")
    s.activo = True
    await registrar_auditoria(usuario.username, "reactivar_sede", rol=usuario.rol, objetivo=s.nombre, db=db)
    await db.commit()
    return {"mensaje": f"Sede '{s.nombre}' reactivada"}


@router.get("/admin/sedes-terminales")
async def listar_terminales_sede(
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    """PCs registradas con su sede, para asignarlas desde Configuracion."""
    _exigir_superadmin(usuario)
    filas = (await db.execute(
        select(Terminal.id, Terminal.nombre_red, Terminal.ip, Terminal.id_sede)
        .where(Terminal.nombre_red != "IMPORTADO")
        .order_by(Terminal.nombre_red)
    )).all()
    return [{"id": r.id, "nombre": r.nombre_red, "ip": r.ip, "id_sede": r.id_sede} for r in filas]


@router.get("/admin/sedes-resumen")
async def resumen_sedes(
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    """Estado de cada sede de un vistazo (Monitoreo): PCs registradas, en uso,
    libres (encendidas y bloqueadas) y apagadas. Un solo GROUP BY: barato aunque
    el panel lo pida cada 15 s."""
    _exigir_panel(usuario)
    mi_sede = sede_efectiva(usuario, None)
    activa = select(Sesion.id_terminal).where(Sesion.estado == "activa").distinct().subquery()
    en_uso = case((Terminal.id.in_(select(activa.c.id_terminal)), 1), else_=0)
    online = case((Terminal.estado != "offline", 1), else_=0)
    q = (select(Terminal.id_sede, func.count(Terminal.id), func.sum(online), func.sum(en_uso),
                func.max(Terminal.ultima_conexion))
         .where(Terminal.nombre_red != "IMPORTADO")
         .group_by(Terminal.id_sede))
    if mi_sede:
        q = q.where(Terminal.id_sede == mi_sede)
    filas = {r[0]: r for r in (await db.execute(q)).all()}
    qs = select(Sede).where(Sede.activo == True).order_by(Sede.nombre)
    if mi_sede:
        qs = select(Sede).where(Sede.id == mi_sede)
    sedes = (await db.execute(qs)).scalars().all()

    def item(sid, nombre):
        r = filas.get(sid)
        total, on, uso = (int(r[1]), int(r[2] or 0), int(r[3] or 0)) if r else (0, 0, 0)
        return {
            "id": sid, "nombre": nombre, "total": total, "en_uso": uso,
            "libres": max(on - uso, 0), "apagadas": total - on,
            "ultima_conexion": r[4].isoformat() if r and r[4] else None,
        }
    items = [item(sd.id, sd.nombre) for sd in sedes]
    if not mi_sede and filas.get(None):
        items.append(item(None, "Sin sede asignada"))
    return {"mi_sede": mi_sede, "items": items}


class AsignarSedeReq(BaseModel):
    terminales: list[int]
    id_sede: int | None = None


@router.put("/admin/sedes-terminales")
async def asignar_terminales_sede(
    datos: AsignarSedeReq,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    _exigir_superadmin(usuario)
    if not datos.terminales:
        raise HTTPException(status_code=422, detail="Seleccione al menos una PC")
    if len(datos.terminales) > 1000:
        raise HTTPException(status_code=422, detail="Demasiadas PCs en una sola operación")
    nombre_sede = "sin sede"
    if datos.id_sede is not None:
        s = (await db.execute(select(Sede).where(Sede.id == datos.id_sede, Sede.activo == True))).scalar_one_or_none()
        if not s:
            raise HTTPException(status_code=404, detail="Sede no encontrada o dada de baja")
        nombre_sede = s.nombre
    from sqlalchemy import update
    res = await db.execute(
        update(Terminal).where(Terminal.id.in_(datos.terminales)).values(id_sede=datos.id_sede)
    )
    await registrar_auditoria(usuario.username, "asignar_sede_pcs", rol=usuario.rol,
                              objetivo=nombre_sede, detalle=f"{res.rowcount} PC(s)", db=db)
    await db.commit()
    return {"mensaje": f"{res.rowcount} PC(s) asignada(s) a {nombre_sede}"}


# ══════════════════════════════════════════════════════════════════════
#  CICLOS ACADEMICOS
# ══════════════════════════════════════════════════════════════════════

class CicloReq(BaseModel):
    nombre: str
    fecha_inicio: str
    fecha_fin: str


def _ciclo_dict(c: CicloAcademico, hoy: date) -> dict:
    return {
        "id": c.id, "nombre": c.nombre,
        "fecha_inicio": c.fecha_inicio.isoformat(), "fecha_fin": c.fecha_fin.isoformat(),
        "dias": (c.fecha_fin - c.fecha_inicio).days + 1,
        "actual": c.fecha_inicio <= hoy <= c.fecha_fin,
    }


async def _validar_ciclo(db: AsyncSession, datos: CicloReq, excluir_id: int | None = None):
    nombre = " ".join((datos.nombre or "").upper().split())
    if not nombre or len(nombre) > 30:
        raise HTTPException(status_code=422, detail="El nombre del ciclo es obligatorio (máx. 30 caracteres), ej: 2026-II")
    ini = _parse_fecha(datos.fecha_inicio, "inicio")
    fin = _parse_fecha(datos.fecha_fin, "fin")
    if fin < ini:
        raise HTTPException(status_code=422, detail="La fecha de fin no puede ser anterior a la de inicio")
    if (fin - ini).days > 400:
        raise HTTPException(status_code=422, detail="Un ciclo no puede durar más de 400 días")
    q = select(CicloAcademico).where(CicloAcademico.activo == True, CicloAcademico.nombre == nombre)
    if excluir_id:
        q = q.where(CicloAcademico.id != excluir_id)
    if (await db.execute(q)).scalars().first():
        raise HTTPException(status_code=409, detail=f"Ya existe el ciclo {nombre}")
    # Los ciclos no se superponen: asi un dia pertenece a un solo ciclo y los
    # reportes por ciclo no cuentan dos veces la misma sesion.
    q = select(CicloAcademico).where(
        CicloAcademico.activo == True,
        CicloAcademico.fecha_inicio <= fin,
        CicloAcademico.fecha_fin >= ini,
    )
    if excluir_id:
        q = q.where(CicloAcademico.id != excluir_id)
    choque = (await db.execute(q)).scalars().first()
    if choque:
        raise HTTPException(
            status_code=409,
            detail=f"Las fechas se superponen con el ciclo {choque.nombre} "
                   f"({choque.fecha_inicio:%d/%m/%Y} - {choque.fecha_fin:%d/%m/%Y})")
    return nombre, ini, fin


@router.get("/admin/ciclos")
async def listar_ciclos(
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    _exigir_panel(usuario)
    hoy = date.today()
    ciclos = (await db.execute(
        select(CicloAcademico).where(CicloAcademico.activo == True)
        .order_by(CicloAcademico.fecha_inicio.desc())
    )).scalars().all()
    items = [_ciclo_dict(c, hoy) for c in ciclos]
    actual = next((i for i in items if i["actual"]), None)
    return {"items": items, "actual": actual}


@router.post("/admin/ciclos", status_code=201)
async def crear_ciclo(
    datos: CicloReq,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    _exigir_superadmin(usuario)
    nombre, ini, fin = await _validar_ciclo(db, datos)
    # Si existe uno dado de baja con el mismo nombre, se reutiliza (nombre unico).
    previo = (await db.execute(select(CicloAcademico).where(CicloAcademico.nombre == nombre))).scalar_one_or_none()
    if previo:
        previo.fecha_inicio, previo.fecha_fin, previo.activo = ini, fin, True
        c = previo
    else:
        c = CicloAcademico(nombre=nombre, fecha_inicio=ini, fecha_fin=fin, activo=True)
        db.add(c)
    await registrar_auditoria(usuario.username, "crear_ciclo", rol=usuario.rol, objetivo=nombre,
                              detalle=f"{ini:%d/%m/%Y} - {fin:%d/%m/%Y}", db=db)
    await db.commit()
    return {"mensaje": f"Ciclo {nombre} registrado", "id": c.id}


@router.put("/admin/ciclos/{ciclo_id}")
async def editar_ciclo(
    ciclo_id: int, datos: CicloReq,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    _exigir_superadmin(usuario)
    c = (await db.execute(select(CicloAcademico).where(CicloAcademico.id == ciclo_id))).scalar_one_or_none()
    if not c or not c.activo:
        raise HTTPException(status_code=404, detail="Ciclo no encontrado")
    nombre, ini, fin = await _validar_ciclo(db, datos, excluir_id=ciclo_id)
    otro = (await db.execute(select(CicloAcademico).where(
        CicloAcademico.nombre == nombre, CicloAcademico.id != ciclo_id))).scalar_one_or_none()
    if otro:  # uno dado de baja con ese nombre: liberar el nombre
        otro.nombre = f"{otro.nombre}~{otro.id}"[:30]
        await db.flush()
    c.nombre, c.fecha_inicio, c.fecha_fin = nombre, ini, fin
    await registrar_auditoria(usuario.username, "editar_ciclo", rol=usuario.rol, objetivo=nombre,
                              detalle=f"{ini:%d/%m/%Y} - {fin:%d/%m/%Y}", db=db)
    await db.commit()
    return {"mensaje": f"Ciclo {nombre} actualizado"}


@router.delete("/admin/ciclos/{ciclo_id}")
async def eliminar_ciclo(
    ciclo_id: int,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    """Quitar un ciclo no borra datos: solo deja de aparecer en los reportes."""
    _exigir_superadmin(usuario)
    c = (await db.execute(select(CicloAcademico).where(CicloAcademico.id == ciclo_id))).scalar_one_or_none()
    if not c or not c.activo:
        raise HTTPException(status_code=404, detail="Ciclo no encontrado")
    c.activo = False
    await registrar_auditoria(usuario.username, "eliminar_ciclo", rol=usuario.rol, objetivo=c.nombre, db=db)
    await db.commit()
    return {"mensaje": f"Ciclo {c.nombre} eliminado"}


# ══════════════════════════════════════════════════════════════════════
#  REPORTES (estadisticas e incidencias por periodo)
# ══════════════════════════════════════════════════════════════════════

async def _resolver_periodo(db: AsyncSession, desde: str | None, hasta: str | None,
                            ciclo_id: int | None) -> tuple[date, date, str | None]:
    """Devuelve (inicio, fin inclusivo, nombre_ciclo)."""
    if ciclo_id:
        c = (await db.execute(select(CicloAcademico).where(
            CicloAcademico.id == ciclo_id, CicloAcademico.activo == True))).scalar_one_or_none()
        if not c:
            raise HTTPException(status_code=404, detail="Ciclo no encontrado")
        return c.fecha_inicio, c.fecha_fin, c.nombre
    ini = _parse_fecha(desde, "desde")
    fin = _parse_fecha(hasta or desde, "hasta")
    if fin < ini:
        raise HTTPException(status_code=422, detail="La fecha final no puede ser anterior a la inicial")
    if (fin - ini).days > _MAX_DIAS_REPORTE:
        raise HTTPException(status_code=422, detail="El periodo no puede superar 3 años")
    return ini, fin, None


def _rango_dt(ini: date, fin: date) -> tuple[datetime, datetime]:
    """[ini 00:00, fin+1 00:00) — fin exclusivo, asi el ultimo dia cuenta completo."""
    return datetime(ini.year, ini.month, ini.day), datetime(fin.year, fin.month, fin.day) + timedelta(days=1)


async def _nombre_sede(db: AsyncSession, id_sede: int | None) -> str:
    if not id_sede:
        return "Todas las sedes"
    s = (await db.execute(select(Sede.nombre).where(Sede.id == id_sede))).scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Sede no encontrada")
    return s


@router.get("/admin/reportes/estadisticas")
async def reporte_estadisticas(
    desde: str | None = None, hasta: str | None = None,
    ciclo_id: int | None = None, id_sede: int | None = None,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    """Estadisticas de uso de un periodo (dia, mes, ciclo, año o rango). Todo se
    agrega en MySQL: el costo no crece con el numero de sesiones devueltas."""
    _exigir_panel(usuario)
    ini, fin, ciclo_nombre = await _resolver_periodo(db, desde, hasta, ciclo_id)
    id_sede = sede_efectiva(usuario, id_sede)
    sede_nombre = await _nombre_sede(db, id_sede)
    dt_ini, dt_fin = _rango_dt(ini, fin)

    # Filtro base de sesiones del periodo. Las canceladas por falta de
    # confirmacion del kiosco no fueron un uso real.
    cond = [
        Sesion.hora_entrada >= dt_ini, Sesion.hora_entrada < dt_fin,
        or_(Sesion.motivo_cierre.is_(None), Sesion.motivo_cierre != "sin_confirmacion"),
    ]
    if id_sede:
        cond.append(Terminal.id_sede == id_sede)

    def base(*cols):
        return select(*cols).select_from(Sesion).join(Terminal, Terminal.id == Sesion.id_terminal).where(and_(*cond))

    minutos = func.least(
        func.greatest(func.coalesce(func.timestampdiff(text_minute(), Sesion.hora_entrada, Sesion.hora_salida), 0), 0),
        _MAX_MIN_SESION,
    )

    r = (await db.execute(base(
        func.count(Sesion.id), func.count(func.distinct(Sesion.dni_alumno)),
        func.coalesce(func.sum(minutos), 0), func.count(func.distinct(func.date(Sesion.hora_entrada))),
    ))).one()
    total_ses, usuarios, total_min, dias_act = int(r[0] or 0), int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)

    # Facultad / escuela del alumno (outer join: alumnos sin escuela cuentan como "Sin facultad")
    fac_nombre = func.coalesce(Facultad.nombre, "Sin facultad")
    por_facultad = [
        {"nombre": n, "sesiones": int(s), "usuarios": int(u)}
        for n, s, u in (await db.execute(
            base(fac_nombre, func.count(Sesion.id), func.count(func.distinct(Sesion.dni_alumno)))
            .join(AlumnoMaestro, AlumnoMaestro.dni == Sesion.dni_alumno)
            .outerjoin(Escuela, Escuela.id == AlumnoMaestro.id_escuela)
            .outerjoin(Facultad, Facultad.id == func.coalesce(Escuela.id_facultad, AlumnoMaestro.id_facultad))
            .group_by(fac_nombre).order_by(func.count(Sesion.id).desc())
        )).all()
    ]
    esc_nombre = func.coalesce(Escuela.nombre, "Sin escuela")
    por_escuela = [
        {"nombre": n, "sesiones": int(s), "usuarios": int(u)}
        for n, s, u in (await db.execute(
            base(esc_nombre, func.count(Sesion.id), func.count(func.distinct(Sesion.dni_alumno)))
            .join(AlumnoMaestro, AlumnoMaestro.dni == Sesion.dni_alumno)
            .outerjoin(Escuela, Escuela.id == AlumnoMaestro.id_escuela)
            .group_by(esc_nombre).order_by(func.count(Sesion.id).desc()).limit(15)
        )).all()
    ]
    razon = func.coalesce(func.nullif(Sesion.razon_uso, ""), "Sin especificar")
    por_motivo = [
        {"nombre": n, "sesiones": int(s)}
        for n, s in (await db.execute(
            base(razon, func.count(Sesion.id)).group_by(razon).order_by(func.count(Sesion.id).desc()).limit(15)
        )).all()
    ]
    sede_col = func.coalesce(Sede.nombre, "Sin sede")
    por_sede = [
        {"nombre": n, "sesiones": int(s), "usuarios": int(u), "minutos": int(m or 0)}
        for n, s, u, m in (await db.execute(
            base(sede_col, func.count(Sesion.id), func.count(func.distinct(Sesion.dni_alumno)), func.sum(minutos))
            .outerjoin(Sede, Sede.id == Terminal.id_sede)
            .group_by(sede_col).order_by(func.count(Sesion.id).desc())
        )).all()
    ]
    por_pc = [
        {"nombre": n, "sesiones": int(s), "minutos": int(m or 0)}
        for n, s, m in (await db.execute(
            base(Terminal.nombre_red, func.count(Sesion.id), func.sum(minutos))
            .group_by(Terminal.nombre_red).order_by(func.count(Sesion.id).desc()).limit(15)
        )).all()
    ]
    horas = dict((int(h), int(n)) for h, n in (await db.execute(
        base(func.hour(Sesion.hora_entrada), func.count(Sesion.id)).group_by(func.hour(Sesion.hora_entrada))
    )).all())
    por_hora = [{"hora": h, "sesiones": horas.get(h, 0)} for h in range(24)]
    # DAYOFWEEK: 1=domingo ... 7=sabado
    dsem = dict((int(d), int(n)) for d, n in (await db.execute(
        base(func.dayofweek(Sesion.hora_entrada), func.count(Sesion.id)).group_by(func.dayofweek(Sesion.hora_entrada))
    )).all())
    nombres_dia = {2: "Lunes", 3: "Martes", 4: "Miércoles", 5: "Jueves", 6: "Viernes", 7: "Sábado", 1: "Domingo"}
    por_dia_semana = [{"nombre": nombres_dia[d], "sesiones": dsem.get(d, 0)} for d in (2, 3, 4, 5, 6, 7, 1)]

    # Serie temporal: por dia si el periodo es corto, por mes si es largo.
    dias_periodo = (fin - ini).days + 1
    if dias_periodo <= 62:
        dia = func.date(Sesion.hora_entrada)
        filas = dict((str(d), (int(s), int(u))) for d, s, u in (await db.execute(
            base(dia, func.count(Sesion.id), func.count(func.distinct(Sesion.dni_alumno))).group_by(dia)
        )).all())
        serie, cur = [], ini
        while cur <= fin:
            s, u = filas.get(cur.isoformat(), (0, 0))
            serie.append({"etiqueta": cur.strftime("%d/%m"), "sesiones": s, "usuarios": u})
            cur += timedelta(days=1)
        serie_tipo = "dia"
    else:
        mes = func.date_format(Sesion.hora_entrada, "%Y-%m")
        filas = dict((str(m), (int(s), int(u))) for m, s, u in (await db.execute(
            base(mes, func.count(Sesion.id), func.count(func.distinct(Sesion.dni_alumno))).group_by(mes)
        )).all())
        serie, y, m = [], ini.year, ini.month
        while (y, m) <= (fin.year, fin.month):
            s, u = filas.get(f"{y:04d}-{m:02d}", (0, 0))
            serie.append({"etiqueta": f"{_MESES[m - 1]} {y}", "sesiones": s, "usuarios": u})
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        serie_tipo = "mes"

    # Incidencias del periodo (por sede de registro)
    cond_inc = [Incidencia.fecha >= dt_ini, Incidencia.fecha < dt_fin]
    if id_sede:
        cond_inc.append(Incidencia.id_sede == id_sede)
    inc_tipos = dict((t, int(n)) for t, n in (await db.execute(
        select(Incidencia.tipo, func.count(Incidencia.id)).where(*cond_inc).group_by(Incidencia.tipo)
    )).all())
    inc_motivos = [
        {"nombre": n, "total": int(c)}
        for n, c in (await db.execute(
            select(Incidencia.motivo, func.count(Incidencia.id)).where(*cond_inc)
            .group_by(Incidencia.motivo).order_by(func.count(Incidencia.id).desc()).limit(10)
        )).all()
    ]
    inc_alumnos = (await db.execute(
        select(func.count(func.distinct(Incidencia.dni_alumno))).where(*cond_inc)
    )).scalar() or 0
    # Bans y altas al padron son globales (no tienen sede): solo sin filtro de sede.
    bans = nuevos = None
    if not id_sede:
        bans = (await db.execute(select(func.count(Ban.id)).where(
            Ban.fecha_ini >= dt_ini, Ban.fecha_ini < dt_fin))).scalar() or 0
        nuevos = (await db.execute(select(func.count(AlumnoMaestro.dni)).where(
            AlumnoMaestro.fecha_alta >= dt_ini, AlumnoMaestro.fecha_alta < dt_fin))).scalar() or 0

    pico = max(por_hora, key=lambda x: x["sesiones"]) if total_ses else None
    return {
        "periodo": {
            "desde": ini.isoformat(), "hasta": fin.isoformat(), "dias": dias_periodo,
            "ciclo": ciclo_nombre, "sede": sede_nombre, "id_sede": id_sede,
        },
        "resumen": {
            "sesiones": total_ses,
            "usuarios": usuarios,
            "horas_uso": round(total_min / 60, 1),
            "promedio_min": round(total_min / total_ses, 1) if total_ses else 0,
            "dias_con_actividad": dias_act,
            "promedio_sesiones_dia": round(total_ses / dias_act, 1) if dias_act else 0,
            "hora_pico": f"{pico['hora']:02d}:00 - {pico['hora'] + 1:02d}:00" if pico and pico["sesiones"] else None,
            "alumnos_nuevos": nuevos,
            "bans": bans,
        },
        "incidencias": {
            "total": sum(inc_tipos.values()),
            "leves": inc_tipos.get("leve", 0),
            "graves": inc_tipos.get("grave", 0),
            "alumnos": int(inc_alumnos),
            "motivos": inc_motivos,
        },
        "por_facultad": por_facultad,
        "por_escuela": por_escuela,
        "por_motivo": por_motivo,
        "por_sede": por_sede,
        "por_pc": por_pc,
        "por_hora": por_hora,
        "por_dia_semana": por_dia_semana,
        "serie": serie,
        "serie_tipo": serie_tipo,
        "generado": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "generado_por": usuario.nombre_completo or usuario.username,
    }


_MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def text_minute():
    """Literal MINUTE para TIMESTAMPDIFF (func.timestampdiff no acepta str)."""
    from sqlalchemy import literal_column
    return literal_column("MINUTE")


@router.get("/admin/reportes/incidencias")
async def reporte_incidencias(
    desde: str | None = None, hasta: str | None = None,
    ciclo_id: int | None = None, id_sede: int | None = None,
    tipo: str | None = None,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(obtener_usuario_actual),
):
    """Listado imprimible de incidencias del periodo (todas, activas o no)."""
    _exigir_panel(usuario)
    ini, fin, ciclo_nombre = await _resolver_periodo(db, desde, hasta, ciclo_id)
    id_sede = sede_efectiva(usuario, id_sede)
    sede_nombre = await _nombre_sede(db, id_sede)
    dt_ini, dt_fin = _rango_dt(ini, fin)
    cond = [Incidencia.fecha >= dt_ini, Incidencia.fecha < dt_fin]
    if id_sede:
        cond.append(Incidencia.id_sede == id_sede)
    if tipo in ("leve", "grave"):
        cond.append(Incidencia.tipo == tipo)
    total = (await db.execute(select(func.count(Incidencia.id)).where(*cond))).scalar() or 0
    filas = (await db.execute(
        select(Incidencia, Sede.nombre)
        .outerjoin(Sede, Sede.id == Incidencia.id_sede)
        .where(*cond)
        .order_by(Incidencia.fecha.asc())
        .limit(_MAX_FILAS_INCIDENCIAS)
    )).all()
    tipos = dict((t, int(n)) for t, n in (await db.execute(
        select(Incidencia.tipo, func.count(Incidencia.id)).where(*cond).group_by(Incidencia.tipo)
    )).all())
    return {
        "periodo": {
            "desde": ini.isoformat(), "hasta": fin.isoformat(),
            "ciclo": ciclo_nombre, "sede": sede_nombre, "id_sede": id_sede,
        },
        "total": int(total),
        "truncado": total > _MAX_FILAS_INCIDENCIAS,
        "leves": tipos.get("leve", 0),
        "graves": tipos.get("grave", 0),
        "items": [{
            "id": i.id, "dni": i.dni_alumno, "nombre_alumno": i.nombre_alumno,
            "tipo": i.tipo, "motivo": i.motivo, "descripcion": i.descripcion or "",
            "fecha": i.fecha.isoformat() if i.fecha else None,
            "registrado_por": i.registrado_por, "activa": bool(i.activa),
            "sede": sede or "Sin sede",
        } for i, sede in filas],
        "generado": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "generado_por": usuario.nombre_completo or usuario.username,
    }
