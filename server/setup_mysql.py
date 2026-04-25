"""
setup_mysql.py — Crea la base de datos biblioteca_unasam en MySQL y genera todas las tablas.

Uso:
    cd server
    python setup_mysql.py
"""
import asyncio
import sys

MYSQL_USER     = "root"
MYSQL_PASSWORD = "impo2010"
MYSQL_HOST     = "localhost"
MYSQL_PORT     = 3306
DB_NAME        = "biblioteca_unasam"


def _crear_db_sincrono():
    """Crea la BD usando pymysql síncrono (sin necesidad de asyncmy para este paso)."""
    try:
        import pymysql
    except ImportError:
        print("ERROR: falta pymysql. Instala con:  pip install pymysql")
        sys.exit(1)

    conn = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            )
        conn.commit()
        print(f"✅ Base de datos '{DB_NAME}' lista.")
    finally:
        conn.close()


async def _crear_tablas():
    """Apunta el engine a biblioteca_unasam y ejecuta create_all."""
    import os
    os.environ["DATABASE_URL"] = (
        f"mysql+aiomysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
        f"@{MYSQL_HOST}:{MYSQL_PORT}/{DB_NAME}"
    )

    # Importar DESPUÉS de setear la variable para que database.py la lea
    from database import engine, Base
    import models  # noqa: F401 — registra todos los modelos en Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()
    print("✅ Tablas creadas correctamente (InnoDB, utf8mb4).")


if __name__ == "__main__":
    print(f"→ Conectando a MySQL {MYSQL_HOST}:{MYSQL_PORT} como '{MYSQL_USER}'...")
    _crear_db_sincrono()

    print("→ Creando tablas en asyncmy...")
    asyncio.run(_crear_tablas())

    print("\n🎉 Listo. El servidor ya puede iniciar apuntando a MySQL.")
    print(f"   DATABASE_URL=mysql+aiomysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{DB_NAME}")
