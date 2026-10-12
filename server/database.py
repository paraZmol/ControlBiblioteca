# database.py - MySQL (biblioteca_unasam)
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL or not DATABASE_URL.startswith("mysql"):
    raise RuntimeError(
        "DATABASE_URL no configurado o no apunta a MySQL. "
        "Defina DATABASE_URL=mysql+asyncmy://user:pass@host:3306/db en el .env"
    )

# Pool dimensionado para el servidor central (cientos de PCs de varias sedes).
# UN solo proceso: NUNCA arrancar con uvicorn --workers N, porque el estado de
# conexiones (core/websocket_manager) vive en memoria y cada worker vería solo
# parte de las PCs. pool_timeout corto: mejor fallar rápido un evento que dejar
# colgado el handler de un kiosco esperando conexión.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=20,
    max_overflow=20,
    pool_timeout=10,
    pool_recycle=3600,
    pool_pre_ping=True,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
