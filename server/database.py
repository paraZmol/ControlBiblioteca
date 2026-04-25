# database.py - Configuración de base de datos (SQLite por defecto, MySQL via DATABASE_URL)
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event
from dotenv import load_dotenv

load_dotenv()

# Para MySQL: DATABASE_URL=mysql+asyncmy://user:pass@host:3306/biblioteca
# Para SQLite (desarrollo/fallback): dejar vacío o no definir la variable
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./biblioteca.db"
)

_es_sqlite = DATABASE_URL.startswith("sqlite")

if _es_sqlite:
    engine = create_async_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
else:
    # MySQL / MariaDB — sin connect_args especiales, pool_recycle para conexiones largas
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
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
