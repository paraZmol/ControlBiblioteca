# endpoints.py - Rutas de la API REST
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models import Alumno, AlumnoMaestro, Terminal, Sesion, Usuario
from auth_service import (
    verificar_password, hashear_password, crear_token, obtener_usuario_actual
)
from core.websocket_manager import manager
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


# ── Server Info ────────────────────────────────────────────────────

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
        terminal.ultima_conexion = datetime.now()
        terminal.estado = "bloqueado"
    else:
        terminal = Terminal(nombre=datos.nombre, ip=datos.ip, estado="bloqueado", ultima_conexion=datetime.now())
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
        inicio=datetime.now()
    )
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
    """Cerrar una sesión activa usando exclusivamente el reloj del servidor."""
    result = await db.execute(select(Sesion).where(Sesion.id == sesion_id, Sesion.activa == True))
    sesion = result.scalar_one_or_none()
    if not sesion:
        raise HTTPException(status_code=404, detail="Sesión no encontrada o ya cerrada")

    ahora = datetime.now().replace(tzinfo=None)
    sesion.hora_salida = ahora
    sesion.fin         = ahora
    sesion.activa      = False
    sesion.motivo_cierre = motivo

    await db.execute(
        update(Terminal).where(Terminal.id == sesion.terminal_id).values(estado="bloqueado")
    )
    inicio_naive = sesion.inicio.replace(tzinfo=None) if sesion.inicio else ahora
    duracion_min = int((ahora - inicio_naive).total_seconds() / 60)

    await db.commit()
    return {"mensaje": "Sesión cerrada", "duracion_min": duracion_min, "hora_salida": ahora.strftime("%I:%M:%S %p")}


# Mapeo columna → campos ORM para ORDER BY.
# "escuela" y "estudiante" usan Alumno para agrupación real con JOIN.
_SORT_MAP = {
    "estudiante": (Alumno.apellidos, Alumno.nombres),
    "codigo":     (Alumno.codigo,),
    "dni":        (Alumno.dni,),
    "facultad":   (Sesion.facultad,),
    "escuela":    (Alumno.escuela, Alumno.apellidos, Alumno.nombres),
    "actividad":  (Sesion.razon_uso,),
    "fecha":      (Sesion.inicio,),
    "inicio":     (Sesion.inicio,),
}


def _aplicar_filtro_fecha(q, periodo: str | None, fecha_inicio: str | None, fecha_fin: str | None):
    """Aplica filtro de fecha sobre Sesion.fecha_uso (cadena YYYY-MM-DD en SQLite)."""
    from sqlalchemy import func, cast, String
    from datetime import date

    hoy = date.today()

    if periodo == "dia" or periodo is None:
        # Por defecto: sólo hoy
        q = q.where(cast(Sesion.fecha_uso, String).like(hoy.strftime("%Y-%m-%d") + "%"))
    elif periodo == "mes":
        prefijo = hoy.strftime("%Y-%m")
        q = q.where(cast(Sesion.fecha_uso, String).like(prefijo + "%"))
    elif periodo == "anio":
        prefijo = hoy.strftime("%Y")
        q = q.where(cast(Sesion.fecha_uso, String).like(prefijo + "%"))
    elif periodo == "rango" and fecha_inicio and fecha_fin:
        q = q.where(
            cast(Sesion.fecha_uso, String) >= fecha_inicio,
            cast(Sesion.fecha_uso, String) <= fecha_fin,
        )
    # Si periodo == "todo" no aplica filtro
    return q


@router.get("/sesiones/activas")
async def sesiones_activas(
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(obtener_usuario_actual),
    search:       str | None = None,
    actividad:    str | None = None,
    sort_by:      str = "fecha",
    order:        str = "desc",
    periodo:      str | None = None,   # dia | mes | anio | rango | todo
    fecha_inicio: str | None = None,   # YYYY-MM-DD (solo con periodo=rango)
    fecha_fin:    str | None = None,   # YYYY-MM-DD (solo con periodo=rango)
):
    """Historial con filtros de búsqueda, actividad, fecha y ordenamiento."""
    from sqlalchemy import or_

    q = select(Sesion, Alumno, Terminal).join(Alumno).join(Terminal)

    # ── Filtro temporal ───────────────────────────────────────────────
    q = _aplicar_filtro_fecha(q, periodo, fecha_inicio, fecha_fin)

    # ── Filtro de texto ───────────────────────────────────────────────
    if search:
        like = f"%{search}%"
        q = q.where(or_(
            (Alumno.nombres + " " + Alumno.apellidos).ilike(like),
            Alumno.apellidos.ilike(like),
            Alumno.dni.ilike(like),
            Alumno.codigo.ilike(like),
        ))
    if actividad:
        q = q.where(Sesion.razon_uso.ilike(f"%{actividad}%"))

    # ── Ordenamiento ──────────────────────────────────────────────────
    cols = _SORT_MAP.get(sort_by.lower(), (Sesion.inicio,))
    q = q.order_by(*cols) if order.lower() == "asc" else q.order_by(*[c.desc() for c in cols])

    result = await db.execute(q)
    rows = []
    for s, a, t in result.all():
        inicio_naive = s.inicio.replace(tzinfo=None)     if s.inicio     else None
        salida_naive = (s.hora_salida or s.fin)
        salida_naive = salida_naive.replace(tzinfo=None)  if salida_naive else None
        duracion_min = int((salida_naive - inicio_naive).total_seconds() / 60) if (inicio_naive and salida_naive) else None
        rows.append({
            "id":              s.id,
            "inicio":          s.inicio,
            "hora_salida":     s.hora_salida,
            "hora_salida_fmt": salida_naive.strftime("%I:%M:%S %p") if salida_naive else None,
            "fin":             s.fin,
            "fecha_uso":       s.fecha_uso or (s.inicio.date() if s.inicio else None),
            "alumno_codigo":   a.codigo,
            "alumno_dni":      a.dni or s.dni or "",
            "alumno_nombre":   f"{a.nombres} {a.apellidos}",
            "dni":             s.dni or a.dni or a.codigo,
            "facultad":        s.facultad or "",
            "escuela":         s.escuela or a.escuela or "",
            "terminal_nombre": t.nombre,
            "terminal_ip":     t.ip,
            "razon_uso":       s.razon_uso or "",
            "activa":          s.activa,
            "duracion_min":    duracion_min,
        })
    return rows


@router.get("/admin/exportar-excel")
async def exportar_excel(
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(obtener_usuario_actual)
):
    """Genera y descarga un archivo Excel con el historial completo de sesiones."""
    import io
    from fastapi.responses import StreamingResponse
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    result = await db.execute(
        select(Sesion, Alumno, Terminal)
        .join(Alumno)
        .join(Terminal)
        .order_by(Sesion.inicio.desc())
    )
    rows = result.all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Historial de Sesiones"

    headers = ["Estudiante", "Código", "DNI", "Escuela", "Facultad", "Actividad", "Equipo/PC", "Inicio", "Salida", "Fecha", "Duración (min)"]
    header_fill = PatternFill("solid", fgColor="1E40AF")
    header_font = Font(color="FFFFFF", bold=True)
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for row_idx, (s, a, t) in enumerate(rows, 2):
        inicio_naive = s.inicio.replace(tzinfo=None) if s.inicio else None
        fin_naive    = (s.hora_salida or s.fin)
        fin_naive    = fin_naive.replace(tzinfo=None) if fin_naive else None
        duracion     = int((fin_naive - inicio_naive).total_seconds() / 60) if (inicio_naive and fin_naive) else ""
        ws.append([
            f"{a.nombres} {a.apellidos}",
            a.codigo,                          # código de matrícula ej: 161.2502.614
            a.dni or s.dni or a.codigo,        # DNI del estudiante ej: 71926257
            s.escuela or a.escuela or "",
            s.facultad or "",
            s.razon_uso or "",
            t.nombre,
            inicio_naive.strftime("%I:%M:%S %p") if inicio_naive else "",
            fin_naive.strftime("%I:%M:%S %p")    if fin_naive    else "En curso",
            inicio_naive.strftime("%d/%m/%Y")    if inicio_naive else "",
            duracion,
        ])

    # Ajustar ancho de columnas
    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=0)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"historial_sesiones_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/admin/exportar-pdf")
async def exportar_pdf(
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(obtener_usuario_actual),
    search:       str | None = None,
    actividad:    str | None = None,
    sort_by:      str = "fecha",
    order:        str = "desc",
    periodo:      str | None = None,
    fecha_inicio: str | None = None,
    fecha_fin:    str | None = None,
):
    """Genera PDF con estadísticas rápidas + tabla filtrada/ordenada."""
    import io
    from collections import Counter
    from fastapi.responses import StreamingResponse
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from sqlalchemy import or_

    q = select(Sesion, Alumno, Terminal).join(Alumno).join(Terminal)
    q = _aplicar_filtro_fecha(q, periodo, fecha_inicio, fecha_fin)
    if search:
        like = f"%{search}%"
        q = q.where(or_(
            (Alumno.nombres + " " + Alumno.apellidos).ilike(like),
            Alumno.dni.ilike(like),
            Alumno.codigo.ilike(like),
        ))
    if actividad:
        q = q.where(Sesion.razon_uso.ilike(f"%{actividad}%"))

    cols = _SORT_MAP.get(sort_by.lower(), (Sesion.inicio,))
    q = q.order_by(*cols) if order.lower() == "asc" else q.order_by(*[c.desc() for c in cols])

    result = await db.execute(q)
    rows = result.all()

    # ── Calcular estadísticas ─────────────────────────────────────────
    alumnos_unicos  = len({a.id for _, a, _ in rows})
    razones         = [s.razon_uso for s, _, _ in rows if s.razon_uso]
    actividad_top   = Counter(razones).most_common(1)[0][0] if razones else "—"
    total_min       = 0
    for s, _, _ in rows:
        ini = s.inicio.replace(tzinfo=None)       if s.inicio    else None
        sal = (s.hora_salida or s.fin)
        sal = sal.replace(tzinfo=None)             if sal         else None
        if ini and sal:
            total_min += int((sal - ini).total_seconds() / 60)
    horas, mins = divmod(total_min, 60)

    # ── Estilos ───────────────────────────────────────────────────────
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=1*cm, rightMargin=1*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles  = getSampleStyleSheet()
    c_azul  = colors.HexColor("#1E40AF")
    c_claro = colors.HexColor("#EFF6FF")
    title_st = ParagraphStyle("titulo", parent=styles["Title"],
                              fontSize=14, textColor=c_azul, alignment=TA_CENTER)
    sub_st   = ParagraphStyle("sub", parent=styles["Normal"],
                              fontSize=8, textColor=colors.grey, alignment=TA_CENTER)
    _periodos = {"dia": "Hoy", "mes": "Este mes", "anio": "Este año",
                 "rango": f"{fecha_inicio} → {fecha_fin}", "todo": "Todo el historial"}
    filtro_txt = [_periodos.get(periodo or "dia", "Hoy")]
    if search:    filtro_txt.append(f"Búsqueda: «{search}»")
    if actividad: filtro_txt.append(f"Actividad: «{actividad}»")
    filtro_desc = " | ".join(filtro_txt)

    elementos = [
        Paragraph("Historial de Sesiones — Biblioteca UNASAM", title_st),
        Paragraph(f"Generado: {datetime.now().strftime('%d/%m/%Y %I:%M:%S %p')}  |  Filtro: {filtro_desc}", sub_st),
        Spacer(1, 0.3*cm),
    ]

    # ── Cuadro de estadísticas ────────────────────────────────────────
    stats_data = [
        ["📋 Total sesiones", "👥 Alumnos únicos", "⭐ Actividad frecuente", "⏱ Tiempo total de uso"],
        [str(len(rows)), str(alumnos_unicos), actividad_top, f"{horas}h {mins}min"],
    ]
    stats_table = Table(stats_data, colWidths=[6*cm, 5*cm, 9*cm, 5.5*cm])
    stats_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  c_azul),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  8),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME",      (0, 1), (-1, 1),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 1), (-1, 1),  12),
        ("TEXTCOLOR",     (0, 1), (-1, 1),  c_azul),
        ("BACKGROUND",    (0, 1), (-1, 1),  c_claro),
        ("BOX",           (0, 0), (-1, -1), 1,   c_azul),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, colors.HexColor("#93C5FD")),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elementos += [stats_table, Spacer(1, 0.4*cm)]

    # ── Tabla principal ───────────────────────────────────────────────
    # Estilos de párrafo para word-wrap en celdas
    cell_st = ParagraphStyle("cell", parent=styles["Normal"],
                             fontSize=6.5, leading=9, wordWrap="LTR")
    hdr_st  = ParagraphStyle("hdr",  parent=styles["Normal"],
                             fontSize=7, fontName="Helvetica-Bold",
                             textColor=colors.white, alignment=TA_CENTER, leading=9)

    headers_p  = [Paragraph(h, hdr_st) for h in
                  ["Estudiante", "Código", "DNI", "Escuela", "Actividad", "Equipo/PC", "Inicio", "Salida", "Fecha", "Min"]]
    # Widths: más espacio a Estudiante y Escuela; DNI/Código/Equipo más estrecho
    col_widths = [5.5*cm, 2.5*cm, 2.0*cm, 4.5*cm, 3.2*cm, 2.0*cm, 2.0*cm, 2.0*cm, 2.0*cm, 1.3*cm]
    data = [headers_p]
    for s, a, t in rows:
        inicio_naive = s.inicio.replace(tzinfo=None)    if s.inicio    else None
        fin_naive    = (s.hora_salida or s.fin)
        fin_naive    = fin_naive.replace(tzinfo=None)   if fin_naive   else None
        duracion     = str(int((fin_naive - inicio_naive).total_seconds() / 60)) if (inicio_naive and fin_naive) else "—"
        data.append([
            Paragraph(f"{a.nombres} {a.apellidos}", cell_st),
            Paragraph(a.codigo or "", cell_st),
            Paragraph(a.dni or s.dni or "", cell_st),
            Paragraph(s.escuela or a.escuela or "", cell_st),
            Paragraph(s.razon_uso or "—", cell_st),
            Paragraph(t.nombre or "", cell_st),
            inicio_naive.strftime("%I:%M %p") if inicio_naive else "",
            fin_naive.strftime("%I:%M %p")    if fin_naive    else "En curso",
            inicio_naive.strftime("%d/%m/%Y") if inicio_naive else "",
            duracion,
        ])

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  c_azul),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  7),
        ("ALIGN",         (0, 0), (-1, 0),  "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, 0),  5),
        ("TOPPADDING",    (0, 0), (-1, 0),  5),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 1), (-1, -1), 6.5),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, c_claro]),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]))
    elementos.append(table)

    doc.build(elementos)
    buf.seek(0)
    filename = f"historial_sesiones_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


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
    
    # Finalizar libera las terminales — quedan disponibles para el siguiente alumno
    await db.execute(update(Terminal).values(estado="disponible"))

    await db.commit()

    # Indicar a los clientes WPF que vuelvan a la pantalla de login
    await manager.forzar_cierre_sesion_todas()
    await manager.notificar_evento(f"Administrador finalizó TODAS las sesiones activas ({len(sesiones)} sesiones)", "warning")
    await manager.notificar_admins()

    return {"mensaje": f"Se han cerrado {len(sesiones)} sesiones correctamente"}


@router.delete("/admin/limpiar-sesiones")
async def limpiar_sesiones(
    db: AsyncSession = Depends(get_db),
    admin: Usuario = Depends(obtener_usuario_actual)
):
    """Cierra sesiones activas y borra todo el historial. Mantiene terminales y alumnos."""
    from main import logger
    logger.info(f"[ADMIN] Usuario '{admin.username}' solicitó LIMPIAR HISTORIAL DE SESIONES")

    if admin.rol != "admin":
        raise HTTPException(status_code=403, detail="No tiene permisos para esta acción")

    # Primero notificar a los clientes WPF que vuelvan al login antes de borrar
    await manager.notificar_evento("🧹 LIMPIAR HISTORIAL: Cerrando sesiones activas...", "warning")
    await manager.forzar_cierre_sesion_todas()

    # Borrar solo el historial de sesiones; terminales y alumnos se mantienen
    await db.execute(delete(Sesion))
    # Dejar terminales en disponible tras el limpiado
    await db.execute(update(Terminal).values(estado="disponible"))
    await db.commit()

    await manager.notificar_evento("🧹 Historial borrado. Terminales listas para nuevo periodo.", "warning")
    return {"mensaje": "Historial de sesiones borrado correctamente. Terminales y alumnos mantenidos."}


@router.post("/admin/importar-excel")
async def importar_excel_upload(
    archivo: UploadFile,
    db: AsyncSession = Depends(get_db),
    admin: Usuario = Depends(obtener_usuario_actual)
):
    """Importa historial de sesiones desde un Excel con el formato de exportación estándar."""
    from main import logger
    import io
    import openpyxl

    if admin.rol != "admin":
        raise HTTPException(status_code=403, detail="No tiene permisos para esta acción")

    logger.info(f"[ADMIN] '{admin.username}' inició importación de Excel")

    contenido = await archivo.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
        ws = wb.active
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Archivo Excel inválido: {e}")

    # ── Obtener o crear terminal virtual para sesiones importadas ──
    try:
        res_vt = await db.execute(select(Terminal).where(Terminal.nombre == "IMPORTADO"))
        terminal_virtual = res_vt.scalar_one_or_none()
        if not terminal_virtual:
            terminal_virtual = Terminal(nombre="IMPORTADO", ip="0.0.0.0", estado="offline")
            db.add(terminal_virtual)
            await db.flush()
        terminal_virtual_id = terminal_virtual.id
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"No se pudo preparar la terminal virtual: {e}")

    # ── Mapear encabezados (case-insensitive, sin espacios) ──
    headers = [str(c.value).strip().lower() if c.value else "" for c in next(ws.iter_rows(min_row=1, max_row=1))]

    def col(variantes):
        for v in variantes:
            if v in headers:
                return headers.index(v)
        return None

    idx_dni        = col(["dni"])
    idx_codigo     = col(["código", "codigo", "cod"])
    idx_estudiante = col(["estudiante", "nombre", "nombres"])
    idx_escuela    = col(["escuela"])
    idx_facultad   = col(["facultad"])
    idx_actividad  = col(["actividad"])
    idx_inicio     = col(["inicio"])
    idx_salida     = col(["salida"])
    idx_fecha      = col(["fecha"])

    if idx_dni is None:
        raise HTTPException(status_code=400, detail="No se encontró la columna 'DNI' en el Excel. Verifique el formato.")

    def cell(fila, idx):
        return str(fila[idx]).strip() if idx is not None and idx < len(fila) and fila[idx] is not None else ""

    def parse_dt(s):
        for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                pass
        return None

    def parse_fecha(s):
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except Exception:
                pass
        return datetime.now().date()

    insertadas = 0
    errores    = 0
    errores_detalle = []

    for num_fila, fila in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        # Saltar filas completamente vacías
        if all(v is None for v in fila):
            continue
        try:
            dni        = cell(fila, idx_dni)
            codigo     = cell(fila, idx_codigo)
            estudiante = cell(fila, idx_estudiante)
            escuela    = cell(fila, idx_escuela)
            facultad   = cell(fila, idx_facultad)
            actividad  = cell(fila, idx_actividad)
            inicio_s   = cell(fila, idx_inicio)
            salida_s   = cell(fila, idx_salida)
            fecha_s    = cell(fila, idx_fecha)

            # Validar DNI
            dni_limpio = dni.replace(" ", "").replace("-", "")
            if not dni_limpio.isdigit() or len(dni_limpio) != 8:
                errores += 1
                errores_detalle.append(f"Fila {num_fila}: DNI inválido '{dni}'")
                continue

            # Buscar alumno por DNI, luego por código
            res_a = await db.execute(select(Alumno).where(Alumno.dni == dni_limpio))
            alumno = res_a.scalar_one_or_none()

            if not alumno and codigo:
                res_a2 = await db.execute(select(Alumno).where(Alumno.codigo == codigo))
                alumno = res_a2.scalar_one_or_none()

            if not alumno:
                # Separar nombre en partes: últimas 2 palabras = apellidos, resto = nombres
                partes = estudiante.split() if estudiante else []
                if len(partes) >= 3:
                    nombres   = " ".join(partes[:len(partes)-2])
                    apellidos = " ".join(partes[len(partes)-2:])
                elif len(partes) == 2:
                    nombres, apellidos = partes[0], partes[1]
                else:
                    nombres, apellidos = estudiante, ""

                # Generar código único si viene vacío o ya existe
                cod_final = codigo if codigo else dni_limpio
                res_cod = await db.execute(select(Alumno).where(Alumno.codigo == cod_final))
                if res_cod.scalar_one_or_none():
                    cod_final = f"{dni_limpio}_imp"

                alumno = Alumno(
                    dni=dni_limpio,
                    codigo=cod_final,
                    nombres=nombres or "SIN NOMBRE",
                    apellidos=apellidos,
                    escuela=escuela or None,
                    facultad=facultad or None,
                    habilitado=True,
                )
                db.add(alumno)
                await db.flush()

            # Parsear fechas y horas
            fecha_obj  = parse_fecha(fecha_s)
            inicio_obj = parse_dt(inicio_s)
            salida_obj = parse_dt(salida_s)

            inicio_dt = datetime.combine(fecha_obj, inicio_obj.time()) if inicio_obj else datetime.combine(fecha_obj, datetime.min.time())
            salida_dt = datetime.combine(fecha_obj, salida_obj.time()) if salida_obj else None

            sesion = Sesion(
                alumno_id=alumno.id,
                terminal_id=terminal_virtual_id,
                inicio=inicio_dt,
                fin=salida_dt,
                hora_salida=salida_dt,
                activa=False,
                motivo_cierre="importacion_excel",
                razon_uso=actividad or None,
            )
            db.add(sesion)
            insertadas += 1

        except Exception as ex:
            await db.rollback()
            logger.warning(f"[IMPORT] Error en fila {num_fila}: {ex}")
            errores += 1
            errores_detalle.append(f"Fila {num_fila}: {ex}")
            # Re-crear terminal virtual tras rollback
            try:
                res_vt2 = await db.execute(select(Terminal).where(Terminal.nombre == "IMPORTADO"))
                tv2 = res_vt2.scalar_one_or_none()
                terminal_virtual_id = tv2.id if tv2 else terminal_virtual_id
            except Exception:
                pass

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error al guardar en la base de datos: {e}")

    msg = f"Importación completada: {insertadas} registro(s) insertado(s)"
    if errores:
        msg += f", {errores} fila(s) ignorada(s)"
    logger.info(f"[ADMIN] {msg}")
    await manager.notificar_evento(f"📥 {msg}", "warning")
    return {
        "mensaje": msg,
        "insertadas": insertadas,
        "errores": errores,
        "detalle_errores": errores_detalle[:10]  # máx 10 para no saturar la respuesta
    }


@router.post("/admin/importar-maestro")
async def importar_maestro(
    archivo: UploadFile,
    db: AsyncSession = Depends(get_db),
    admin: Usuario = Depends(obtener_usuario_actual)
):
    """Importación incremental del maestro de alumnos desde Excel. Upsert por DNI."""
    from main import logger
    import io
    import openpyxl

    if admin.rol != "admin":
        raise HTTPException(status_code=403, detail="No tiene permisos para esta acción")

    contenido = await archivo.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
        ws = wb.active
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Archivo Excel inválido: {e}")

    headers = [str(c.value).strip().lower() if c.value else "" for c in next(ws.iter_rows(min_row=1, max_row=1))]

    def col(variantes):
        for v in variantes:
            if v in headers:
                return headers.index(v)
        return None

    idx_dni     = col(["dni"])
    idx_nombre  = col(["nombre_completo", "nombre completo", "apellidos y nombres", "apellidos nombres", "estudiante", "nombre", "nombres"])
    idx_codigo  = col(["codigo_universitario", "código", "codigo", "cod"])
    idx_facultad = col(["facultad"])
    idx_escuela  = col(["escuela"])

    if idx_dni is None:
        raise HTTPException(status_code=400, detail="Columna 'dni' no encontrada en el Excel.")

    def cell(fila, idx):
        return str(fila[idx]).strip() if idx is not None and idx < len(fila) and fila[idx] is not None else ""

    insertados = actualizados = errores = 0
    errores_detalle = []

    for num_fila, fila in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if all(v is None for v in fila):
            continue
        try:
            dni = cell(fila, idx_dni).replace(" ", "").replace("-", "")
            if not dni.isdigit() or len(dni) != 8:
                errores += 1
                errores_detalle.append(f"Fila {num_fila}: DNI inválido '{dni}'")
                continue

            nombre   = cell(fila, idx_nombre)
            codigo   = cell(fila, idx_codigo)
            facultad = cell(fila, idx_facultad)
            escuela  = cell(fila, idx_escuela)

            res = await db.execute(select(AlumnoMaestro).where(AlumnoMaestro.dni == dni))
            existente = res.scalar_one_or_none()

            if existente:
                existente.nombre_completo     = nombre or existente.nombre_completo
                existente.codigo_universitario = codigo or existente.codigo_universitario
                existente.facultad            = facultad or existente.facultad
                existente.escuela             = escuela or existente.escuela
                existente.fecha_actualizacion = datetime.utcnow()
                actualizados += 1
            else:
                db.add(AlumnoMaestro(
                    dni=dni,
                    nombre_completo=nombre or "SIN NOMBRE",
                    codigo_universitario=codigo or None,
                    facultad=facultad or None,
                    escuela=escuela or None,
                ))
                insertados += 1

        except Exception as ex:
            errores += 1
            errores_detalle.append(f"Fila {num_fila}: {ex}")

    await db.commit()
    msg = f"Maestro importado: {insertados} nuevo(s), {actualizados} actualizado(s)"
    if errores:
        msg += f", {errores} fila(s) ignorada(s)"
    logger.info(f"[ADMIN] {msg}")
    return {"mensaje": msg, "insertados": insertados, "actualizados": actualizados,
            "errores": errores, "detalle_errores": errores_detalle[:10]}


@router.get("/admin/maestro")
async def listar_maestro(
    db: AsyncSession = Depends(get_db),
    admin: Usuario = Depends(obtener_usuario_actual),
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """Lista paginada del maestro de alumnos con búsqueda opcional."""
    if admin.rol != "admin":
        raise HTTPException(status_code=403, detail="Solo administradores pueden acceder a la base de datos")
    from sqlalchemy import or_, func

    q = select(AlumnoMaestro)
    if search:
        like = f"%{search}%"
        q = q.where(or_(
            AlumnoMaestro.dni.ilike(like),
            AlumnoMaestro.nombre_completo.ilike(like),
            AlumnoMaestro.codigo_universitario.ilike(like),
        ))
    total_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(total_q)).scalar()
    q = q.order_by(AlumnoMaestro.nombre_completo).offset(offset).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    return {
        "total": total,
        "alumnos": [
            {
                "dni": r.dni,
                "nombre_completo": r.nombre_completo,
                "codigo_universitario": r.codigo_universitario,
                "facultad": r.facultad,
                "escuela": r.escuela,
                "fecha_actualizacion": r.fecha_actualizacion,
            }
            for r in rows
        ]
    }


class AlumnoMaestroUpdate(BaseModel):
    nombre_completo: str | None = None
    codigo_universitario: str | None = None
    facultad: str | None = None
    escuela: str | None = None


@router.put("/admin/maestro/{dni}")
async def actualizar_maestro(
    dni: str,
    datos: AlumnoMaestroUpdate,
    db: AsyncSession = Depends(get_db),
    admin: Usuario = Depends(obtener_usuario_actual),
):
    """Edición manual de un registro del maestro por DNI."""
    if admin.rol != "admin":
        raise HTTPException(status_code=403, detail="Solo administradores pueden editar el maestro")
    res = await db.execute(select(AlumnoMaestro).where(AlumnoMaestro.dni == dni))
    alumno = res.scalar_one_or_none()
    if not alumno:
        raise HTTPException(status_code=404, detail="Alumno no encontrado en el maestro")
    if datos.nombre_completo  is not None: alumno.nombre_completo     = datos.nombre_completo
    if datos.codigo_universitario is not None: alumno.codigo_universitario = datos.codigo_universitario
    if datos.facultad         is not None: alumno.facultad            = datos.facultad
    if datos.escuela          is not None: alumno.escuela             = datos.escuela
    alumno.fecha_actualizacion = datetime.utcnow()
    await db.commit()
    return {"mensaje": f"Alumno {dni} actualizado correctamente"}


@router.delete("/admin/maestro/{dni}")
async def eliminar_maestro(
    dni: str,
    db: AsyncSession = Depends(get_db),
    admin: Usuario = Depends(obtener_usuario_actual),
):
    """Elimina un registro del maestro por DNI."""
    if admin.rol != "admin":
        raise HTTPException(status_code=403, detail="Solo administradores pueden eliminar del maestro")
    res = await db.execute(select(AlumnoMaestro).where(AlumnoMaestro.dni == dni))
    alumno = res.scalar_one_or_none()
    if not alumno:
        raise HTTPException(status_code=404, detail="Alumno no encontrado")
    await db.delete(alumno)
    await db.commit()
    return {"mensaje": f"Alumno {dni} eliminado del maestro"}


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
