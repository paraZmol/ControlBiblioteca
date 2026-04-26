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
    rol:              Mapped[str] = mapped_column(String(20),  default="encargado")
    activo:           Mapped[bool]= mapped_column(Boolean, default=True)
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_hasta:  Mapped[datetime] = mapped_column(DateTime, nullable=True)

    def __repr__(self): return f"<Usuario {self.username} ({self.rol})>"


# ── Alias legacy: Alumno → AlumnoMaestro para no romper main.py ──────
Alumno = AlumnoMaestro
