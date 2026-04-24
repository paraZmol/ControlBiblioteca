# models.py - Modelos de base de datos para el sistema de control
from datetime import datetime, date
from sqlalchemy import String, Integer, Boolean, DateTime, Date, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class Alumno(Base):
    """Caché local de alumnos (sincronizado desde API externa OGE/Koha)."""
    __tablename__ = "alumnos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)  # código de matrícula ej: 161.2502.614
    dni: Mapped[str] = mapped_column(String(20), nullable=True, index=True)                   # DNI del estudiante ej: 71926257
    nombres: Mapped[str] = mapped_column(String(100), nullable=False)
    apellidos: Mapped[str] = mapped_column(String(100), nullable=False)
    escuela: Mapped[str] = mapped_column(String(100), nullable=True)
    facultad: Mapped[str] = mapped_column(String(150), nullable=True)
    habilitado: Mapped[bool] = mapped_column(Boolean, default=True)
    ultima_sync: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relación con sesiones
    sesiones: Mapped[list["Sesion"]] = relationship(back_populates="alumno")

    def __repr__(self):
        return f"<Alumno {self.codigo} - {self.apellidos}, {self.nombres}>"


class Terminal(Base):
    """Terminales (PCs) registradas en la biblioteca."""
    __tablename__ = "terminales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    ip: Mapped[str] = mapped_column(String(45), nullable=False)
    estado: Mapped[str] = mapped_column(String(20), default="bloqueado")  # bloqueado, activo, offline
    ultima_conexion: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # Relación con sesiones
    sesiones: Mapped[list["Sesion"]] = relationship(back_populates="terminal")

    def __repr__(self):
        return f"<Terminal {self.nombre} ({self.ip}) - {self.estado}>"


class Sesion(Base):
    """Sesiones de uso de las terminales."""
    __tablename__ = "sesiones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alumno_id: Mapped[int] = mapped_column(ForeignKey("alumnos.id"), nullable=False)
    terminal_id: Mapped[int] = mapped_column(ForeignKey("terminales.id"), nullable=False)
    inicio: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    fin: Mapped[datetime] = mapped_column(DateTime, nullable=True)  # hora_salida
    hora_inicio: Mapped[datetime] = mapped_column(DateTime, nullable=True)  # hora capturada del administrador
    hora_salida: Mapped[datetime] = mapped_column(DateTime, nullable=True)  # hora capturada del administrador
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    confirmada: Mapped[bool] = mapped_column(Boolean, default=False)  # True cuando el cliente confirma el desbloqueo
    motivo_cierre: Mapped[str] = mapped_column(String(50), nullable=True)  # manual, timeout, admin
    razon_uso: Mapped[str] = mapped_column(String(200), nullable=True)  # razon de uso seleccionada en login
    # Campos desnormalizados del alumno (snapshots al momento del ingreso)
    dni: Mapped[str] = mapped_column(String(20), nullable=True)
    facultad: Mapped[str] = mapped_column(String(150), nullable=True)
    escuela: Mapped[str] = mapped_column(String(100), nullable=True)
    fecha_uso: Mapped[date] = mapped_column(Date, nullable=True)

    # Relaciones
    alumno: Mapped["Alumno"] = relationship(back_populates="sesiones")
    terminal: Mapped["Terminal"] = relationship(back_populates="sesiones")

    def __repr__(self):
        return f"<Sesion {self.id} - Alumno:{self.alumno_id} Terminal:{self.terminal_id}>"


class Usuario(Base):
    """Usuarios administradores del sistema (encargados de biblioteca)."""
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    nombre_completo: Mapped[str] = mapped_column(String(150), nullable=True)
    rol: Mapped[str] = mapped_column(String(20), default="encargado")  # admin, encargado
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
