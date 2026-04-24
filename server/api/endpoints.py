# endpoints.py - Rutas de la API REST
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models import Alumno, Terminal, Sesion, Usuario
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
