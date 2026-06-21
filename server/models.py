# models.py - Esquema relacional normalizado (UNASAM Ingeniería de Sistemas)
from datetime import datetime, date
from sqlalchemy import String, Integer, Boolean, DateTime, Date, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class Facultad(Base):
    __tablename__ = "facultades"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:     Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)

    escuelas: Mapped[list["Escuela"]] = relationship(back_populates="facultad")

    def __repr__(self): return f"<Facultad {self.id}: {self.nombre}>"


class Escuela(Base):
    __tablename__ = "escuelas"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:           Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre:       Mapped[str] = mapped_column(String(200), nullable=False)
    id_facultad:  Mapped[int] = mapped_column(ForeignKey("facultades.id"), nullable=False)

    facultad:  Mapped["Facultad"]          = relationship(back_populates="escuelas")
    alumnos:   Mapped[list["AlumnoMaestro"]] = relationship(back_populates="escuela_rel")

    def __repr__(self): return f"<Escuela {self.id}: {self.nombre}>"


class AlumnoMaestro(Base):
    __tablename__ = "alumnos_maestro"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    dni:          Mapped[str] = mapped_column(String(8),   primary_key=True)
    codigo:       Mapped[str] = mapped_column(String(30),  nullable=True, index=True)
    nombre:       Mapped[str] = mapped_column(String(200), nullable=False)
    id_escuela:   Mapped[int] = mapped_column(ForeignKey("escuelas.id"),  nullable=True)
    id_facultad:  Mapped[int] = mapped_column(ForeignKey("facultades.id"), nullable=True)

    escuela_rel:  Mapped["Escuela"]  = relationship(back_populates="alumnos", foreign_keys="[AlumnoMaestro.id_escuela]")
    facultad_rel: Mapped["Facultad"] = relationship(foreign_keys="[AlumnoMaestro.id_facultad]")
    sesiones:     Mapped[list["Sesion"]] = relationship(back_populates="alumno", cascade="all, delete-orphan")

    def __repr__(self): return f"<AlumnoMaestro {self.dni}: {self.nombre}>"


class Terminal(Base):
    __tablename__ = "terminales"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:               Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre_red:       Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    ip:               Mapped[str] = mapped_column(String(45),  nullable=False)
    estado:           Mapped[str] = mapped_column(String(20),  default="bloqueado")
    ultima_conexion:  Mapped[datetime] = mapped_column(DateTime, nullable=True)
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0)
    bloqueada_hasta:  Mapped[datetime] = mapped_column(DateTime, nullable=True)

    sesiones: Mapped[list["Sesion"]] = relationship(back_populates="terminal")

    def __repr__(self): return f"<Terminal {self.nombre_red} ({self.ip}) - {self.estado}>"

    # Alias para compatibilidad con código existente que usa .nombre
    @property
    def nombre(self): return self.nombre_red
    @nombre.setter
    def nombre(self, v): self.nombre_red = v


class CatalogoMotivo(Base):
    __tablename__ = "catalogo_motivos"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:          Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    descripcion: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    activo:      Mapped[bool] = mapped_column(Boolean, default=True)

    sesiones: Mapped[list["Sesion"]] = relationship(back_populates="motivo_rel")

    def __repr__(self): return f"<CatalogoMotivo {self.id}: {self.descripcion}>"


class Sesion(Base):
    __tablename__ = "sesiones"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:           Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dni_alumno:   Mapped[str] = mapped_column(ForeignKey("alumnos_maestro.dni"), nullable=False)
    id_terminal:  Mapped[int] = mapped_column(ForeignKey("terminales.id"),       nullable=False)
    hora_entrada: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    hora_salida:  Mapped[datetime] = mapped_column(DateTime, nullable=True)
    motivo_id:    Mapped[int] = mapped_column(ForeignKey("catalogo_motivos.id"), nullable=True)
    estado:       Mapped[str] = mapped_column(String(20), default="activa")  # activa | cerrada

    # Campos extra compatibilidad y snapshot
    razon_uso:     Mapped[str]  = mapped_column(String(200), nullable=True)
    motivo_cierre: Mapped[str]  = mapped_column(String(50),  nullable=True)
    confirmada:    Mapped[bool] = mapped_column(Boolean, default=False)
    fecha_uso:     Mapped[date] = mapped_column(Date, nullable=True)

    alumno:     Mapped["AlumnoMaestro"]  = relationship(back_populates="sesiones")
    terminal:   Mapped["Terminal"]       = relationship(back_populates="sesiones")
    motivo_rel: Mapped["CatalogoMotivo"] = relationship(back_populates="sesiones")

    # ── Propiedades de compatibilidad con código legacy ──
    @property
    def activa(self): return self.estado == "activa"
    @activa.setter
    def activa(self, v): self.estado = "activa" if v else "cerrada"

    @property
    def inicio(self): return self.hora_entrada
    @inicio.setter
    def inicio(self, v): self.hora_entrada = v

    @property
    def fin(self): return self.hora_salida
    @fin.setter
    def fin(self, v): self.hora_salida = v

    @property
    def terminal_id(self): return self.id_terminal
    @terminal_id.setter
    def terminal_id(self, v): self.id_terminal = v

    @property
    def dni(self): return self.dni_alumno
    @dni.setter
    def dni(self, v): self.dni_alumno = v

    # facultad/escuela como propiedades resueltas por JOIN
    @property
    def facultad(self):
        if self.alumno and self.alumno.escuela_rel and self.alumno.escuela_rel.facultad:
            return self.alumno.escuela_rel.facultad.nombre
        return ""
    @facultad.setter
    def facultad(self, v): pass  # ignorado, viene de FK

    @property
    def escuela(self):
        if self.alumno and self.alumno.escuela_rel:
            return self.alumno.escuela_rel.nombre
        return ""
    @escuela.setter
    def escuela(self, v): pass  # ignorado, viene de FK

    def __repr__(self): return f"<Sesion {self.id} alumno={self.dni_alumno} terminal={self.id_terminal}>"


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:               Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username:         Mapped[str] = mapped_column(String(50),  unique=True, nullable=False)
    hashed_password:  Mapped[str] = mapped_column(String(255), nullable=False)
    nombre_completo:  Mapped[str] = mapped_column(String(150), nullable=True)
    rol:              Mapped[str] = mapped_column(String(20),  default="admin")
    activo:           Mapped[bool]= mapped_column(Boolean, default=True)
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_hasta:  Mapped[datetime] = mapped_column(DateTime, nullable=True)

    def __repr__(self): return f"<Usuario {self.username} ({self.rol})>"


class Ban(Base):
    __tablename__ = "bans"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:            Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dni_alumno:    Mapped[str] = mapped_column(ForeignKey("alumnos_maestro.dni", ondelete="CASCADE"), nullable=False, index=True)
    motivo:        Mapped[str] = mapped_column(String(300), nullable=False)
    fecha_ini:     Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    fecha_fin:     Mapped[datetime] = mapped_column(DateTime, nullable=True)
    baneado_por:   Mapped[str] = mapped_column(String(50), nullable=True)
    levantado_por: Mapped[str] = mapped_column(String(50), nullable=True)
    fecha_levantado: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    alumno: Mapped["AlumnoMaestro"] = relationship()

    def __repr__(self): return f"<Ban {self.id} alumno={self.dni_alumno} hasta={self.fecha_fin}>"


class Incidencia(Base):
    __tablename__ = "incidencias"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:              Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dni_alumno:      Mapped[str] = mapped_column(ForeignKey("alumnos_maestro.dni", ondelete="CASCADE"), nullable=False, index=True)
    nombre_alumno:   Mapped[str] = mapped_column(String(200), nullable=False)
    tipo:            Mapped[str] = mapped_column(String(10),  nullable=False, default="leve")  # leve | grave
    motivo:          Mapped[str] = mapped_column(String(200), nullable=False)
    descripcion:     Mapped[str] = mapped_column(String(600), nullable=True)
    fecha:           Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    registrado_por:  Mapped[str] = mapped_column(String(50),  nullable=False)
    activa:          Mapped[bool] = mapped_column(Boolean, default=True)  # False = reseteada por ban levantado

    alumno: Mapped["AlumnoMaestro"] = relationship()

    def __repr__(self): return f"<Incidencia {self.id} alumno={self.dni_alumno} tipo={self.tipo}>"


class PersonalUniversidad(Base):
    __tablename__ = "personal_universidad"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    dni:        Mapped[str] = mapped_column(String(8),   primary_key=True)
    nombre:     Mapped[str] = mapped_column(String(200), nullable=False)
    cargo:      Mapped[str] = mapped_column(String(150), nullable=True)
    area:       Mapped[str] = mapped_column(String(200), nullable=True)
    correo:     Mapped[str] = mapped_column(String(150), nullable=True)
    telefono:   Mapped[str] = mapped_column(String(20),  nullable=True)
    activo:     Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self): return f"<PersonalUniversidad {self.dni}: {self.nombre}>"


class Egresado(Base):
    __tablename__ = "egresados"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    dni:        Mapped[str] = mapped_column(String(8),   primary_key=True)
    nombre:     Mapped[str] = mapped_column(String(200), nullable=False)
    codigo:     Mapped[str] = mapped_column(String(30),  nullable=True, index=True)
    escuela:    Mapped[str] = mapped_column(String(200), nullable=True)
    anio_egreso: Mapped[str] = mapped_column(String(4),  nullable=True)   # "2024"
    activo:     Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self): return f"<Egresado {self.dni}: {self.nombre}>"


class Docente(Base):
    __tablename__ = "docentes"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    dni:        Mapped[str] = mapped_column(String(8),   primary_key=True)
    nombre:     Mapped[str] = mapped_column(String(200), nullable=False)
    facultad:   Mapped[str] = mapped_column(String(200), nullable=True)
    escuela:    Mapped[str] = mapped_column(String(200), nullable=True)
    correo:     Mapped[str] = mapped_column(String(150), nullable=True)
    telefono:   Mapped[str] = mapped_column(String(20),  nullable=True)
    activo:     Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self): return f"<Docente {self.dni}: {self.nombre}>"


class Autoridad(Base):
    __tablename__ = "autoridades"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    dni:        Mapped[str] = mapped_column(String(8),   primary_key=True)
    nombre:     Mapped[str] = mapped_column(String(200), nullable=False)
    cargo:      Mapped[str] = mapped_column(String(150), nullable=True)
    correo:     Mapped[str] = mapped_column(String(150), nullable=True)
    telefono:   Mapped[str] = mapped_column(String(20),  nullable=True)
    activo:     Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self): return f"<Autoridad {self.dni}: {self.nombre}>"


class Sospecha(Base):
    __tablename__ = "sospechas"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:            Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dni_alumno:    Mapped[str] = mapped_column(ForeignKey("alumnos_maestro.dni", ondelete="CASCADE"), nullable=False, index=True)
    nombre_alumno: Mapped[str] = mapped_column(String(200), nullable=False)
    tipo:          Mapped[str] = mapped_column(String(50),  nullable=False)   # cambio_pc_rapido | dni_baneado_intento | sesion_larga
    detalle:       Mapped[str] = mapped_column(String(600), nullable=False)
    fecha:         Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    estado:        Mapped[str] = mapped_column(String(20),  default="pendiente")  # pendiente | aprobada | descartada
    revisado_por:  Mapped[str] = mapped_column(String(50),  nullable=True)
    fecha_revision: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    alumno: Mapped["AlumnoMaestro"] = relationship()

    def __repr__(self): return f"<Sospecha {self.id} dni={self.dni_alumno} tipo={self.tipo}>"


class ActividadLog(Base):
    __tablename__ = "actividad_logs"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:              Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id_terminal:     Mapped[int] = mapped_column(ForeignKey("terminales.id", ondelete="CASCADE"), nullable=False, index=True)
    nombre_terminal: Mapped[str] = mapped_column(String(100), nullable=False)
    dni_alumno:      Mapped[str] = mapped_column(ForeignKey("alumnos_maestro.dni", ondelete="CASCADE"), nullable=False, index=True)
    nombre_alumno:   Mapped[str] = mapped_column(String(200), nullable=False)
    tipo:            Mapped[str] = mapped_column(String(30),  nullable=False)   # proceso | archivo | comando | navegador
    descripcion:     Mapped[str] = mapped_column(String(300), nullable=False)
    detalle:         Mapped[str] = mapped_column(String(600), nullable=True)
    proceso_exe:     Mapped[str] = mapped_column(String(150), nullable=True)    # nombre del .exe, para "ignorar" desde el panel
    nivel:           Mapped[str] = mapped_column(String(20),  nullable=False, default="normal")  # normal | sospechoso
    fecha_hora:      Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)

    terminal: Mapped["Terminal"]      = relationship()
    alumno:   Mapped["AlumnoMaestro"] = relationship()

    def __repr__(self): return f"<ActividadLog {self.id} {self.tipo} {self.dni_alumno}>"


class ProcesoIgnorado(Base):
    """Procesos cuyo nombre de ejecutable NO debe registrarse como actividad.
    Editable desde el panel: el admin marca ruido del sistema y el servidor
    los descarta antes de guardar, aplicando al instante a todas las PCs."""
    __tablename__ = "procesos_ignorados"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:           Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre_exe:   Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)  # ej. "MoUsoCoreWorker.exe"
    agregado_por: Mapped[str] = mapped_column(String(100), nullable=True)   # username del admin
    fecha:        Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    def __repr__(self): return f"<ProcesoIgnorado {self.nombre_exe}>"


class BancoApp(Base):
    """Banco de programas RECONOCIDOS (lista blanca).

    Un ejecutable que figura aquí es una app legítima que el alumno usa
    (Word, Chrome, AutoCAD…). Lo que NO está ni aquí ni en el banco de ruido
    se marca como SOSPECHOSO hasta que el superadmin lo clasifique.
    Las herramientas peligrosas (cmd, powershell…) se dejan FUERA a propósito."""
    __tablename__ = "banco_apps"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    nombre_exe:      Mapped[str] = mapped_column(String(150), primary_key=True)   # ej. "winword.exe" (lowercase)
    nombre_amigable: Mapped[str] = mapped_column(String(200), nullable=False)     # ej. "Microsoft Word"
    categoria:       Mapped[str] = mapped_column(String(60),  nullable=True)      # Ofimática | Navegador | Diseño | …
    descripcion:     Mapped[str] = mapped_column(String(300), nullable=True)      # qué es, en legible
    aprobado_por:    Mapped[str] = mapped_column(String(100), nullable=True)      # username; NULL = pre-cargado
    fecha:           Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    def __repr__(self): return f"<BancoApp {self.nombre_exe}>"


class BancoRuido(Base):
    """Banco de RUIDO: procesos de fondo que no son actividad del alumno.

    Cada ruido pertenece a un dueño: o un programa del banco_apps
    (ej. GoogleUpdate.exe -> chrome.exe) o al sistema operativo
    (dueno_exe = "__sistema__"). El ruido se OCULTA de la vista normal de
    actividad (como procesos_ignorados) y nunca cuenta como uso real."""
    __tablename__ = "banco_ruido"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    nombre_exe:      Mapped[str] = mapped_column(String(150), primary_key=True)   # ej. "googleupdate.exe" (lowercase)
    nombre_amigable: Mapped[str] = mapped_column(String(200), nullable=True)      # ej. "Actualizador de Google"
    descripcion:     Mapped[str] = mapped_column(String(300), nullable=True)      # ej. "ruido de Chrome: actualizaciones"
    dueno_exe:       Mapped[str] = mapped_column(String(150), nullable=True)      # nombre_exe en banco_apps, o "__sistema__"
    aprobado_por:    Mapped[str] = mapped_column(String(100), nullable=True)      # username; NULL = pre-cargado
    fecha:           Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    def __repr__(self): return f"<BancoRuido {self.nombre_exe} dueno={self.dueno_exe}>"


class ConfiguracionKiosco(Base):
    __tablename__ = "configuracion_kiosco"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:                 Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backdoor_modifiers: Mapped[int] = mapped_column(Integer, default=3)      # 0x0003 (Ctrl+Alt)
    backdoor_key:       Mapped[int] = mapped_column(Integer, default=118)    # 0x76 (F7)
    backdoor_pin:       Mapped[str] = mapped_column(String(50), default="UNASAM2025")
    offline_modifiers:  Mapped[int] = mapped_column(Integer, default=3)      # 0x0003 (Ctrl+Alt)
    offline_key:        Mapped[int] = mapped_column(Integer, default=122)    # 0x7A (F11)
    offline_pin:        Mapped[str] = mapped_column(String(50), default="UNASAM")
    mensaje_duracion_seg: Mapped[int] = mapped_column(Integer, default=60)   # cuánto se muestra el aviso en el kiosco (segundos); UI lo edita en minutos

    def __repr__(self): return f"<ConfiguracionKiosco ID={self.id}>"


class MensajeProgramado(Base):
    __tablename__ = "mensajes_programados"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:        Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    mensaje:   Mapped[str]  = mapped_column(String(500), nullable=False)
    hora_envio: Mapped[str] = mapped_column(String(5),  nullable=False)   # "HH:MM"
    tipo:      Mapped[str]  = mapped_column(String(20), nullable=False, default="extra")  # "cierre" | "extra"
    activo:    Mapped[bool] = mapped_column(Boolean, default=True)
    enviado:   Mapped[bool] = mapped_column(Boolean, default=False)
    fecha_envio: Mapped[datetime] = mapped_column(DateTime, nullable=True)  # solo para tipo="extra"

    def __repr__(self): return f"<MensajeProgramado {self.id} {self.hora_envio} tipo={self.tipo}>"


class Auditoria(Base):
    """Bitácora inmutable de acciones administrativas: quién hizo qué, cuándo y
    sobre qué objeto. Sin FK a propósito — debe sobrevivir a borrados (reset_total
    elimina usuarios/alumnos; una FK con CASCADE destruiría el rastro). Los datos
    se guardan como texto plano para preservar la traza histórica."""
    __tablename__ = "auditoria"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True, autoincrement=True)
    fecha_hora: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    usuario:    Mapped[str]      = mapped_column(String(100), nullable=False, index=True)
    rol:        Mapped[str]      = mapped_column(String(20),  nullable=True)
    accion:     Mapped[str]      = mapped_column(String(60),  nullable=False, index=True)
    objetivo:   Mapped[str]      = mapped_column(String(255), nullable=True)   # qué terminal/alumno/config
    detalle:    Mapped[str]      = mapped_column(String(500), nullable=True)   # contexto libre opcional
    ip_origen:  Mapped[str]      = mapped_column(String(45),  nullable=True)   # IPv4/IPv6, opcional

    def __repr__(self): return f"<Auditoria {self.id}: {self.usuario} {self.accion}>"


# ── Alias legacy: Alumno → AlumnoMaestro para no romper main.py ──────
Alumno = AlumnoMaestro
