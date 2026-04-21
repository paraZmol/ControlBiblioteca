# auth_service.py - Servicio de autenticación JWT
import os
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models import Usuario

# Configuración JWT
SECRET_KEY = os.getenv("SECRET_KEY", "clave-secreta-cambiar-en-produccion")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 horas

# Hashing de contraseñas
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def verificar_password(plain: str, hashed: str) -> bool:
    """Verificar contraseña contra hash."""
    return pwd_context.verify(plain, hashed)


def hashear_password(password: str) -> str:
    """Generar hash de contraseña."""
    return pwd_context.hash(password)


def crear_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Crear token JWT."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def obtener_usuario_actual(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> Usuario:
    """Dependencia: obtener usuario autenticado desde token JWT."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(Usuario).where(Usuario.username == username))
    usuario = result.scalar_one_or_none()
    if usuario is None or not usuario.activo:
        raise credentials_exception
    return usuario
