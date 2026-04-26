"""
configurador.py — Lee config.json, genera .env y crea la BD si no existe.
Uso: python configurador.py
"""
import json
import sys
import os
import secrets


def cargar_config():
    ruta = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(ruta):
        print(f"[ERROR] No se encontro config.json en {ruta}")
        sys.exit(1)
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def generar_env(cfg):
    db   = cfg["database"]
    net  = cfg["network"]
    sec  = cfg["security"]
    ip   = net["server_static_ip"]
    port = db["port"]

    cors = f"http://localhost,http://127.0.0.1,http://{ip},http://{ip}:{net['port']}"
    db_url = (
        f"mysql+aiomysql://{db['user']}:{db['password']}"
        f"@{db['host']}:{port}/{db['name']}"
    )

    # Conservar SECRET_KEY si ya existe en .env
    secret_key = None
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("SECRET_KEY="):
                    secret_key = line.strip().split("=", 1)[1]
    if not secret_key or "CAMBIAR" in secret_key:
        secret_key = secrets.token_urlsafe(32)

    contenido = (
        f"DATABASE_URL={db_url}\n"
        f"SECRET_KEY={secret_key}\n"
        f"CORS_ORIGINS={cors}\n"
        f"PASS_NIVEL2={sec['pass_nivel2']}\n"
    )

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(contenido)
    print(f"[OK] .env generado.")
    print(f"     DATABASE_URL={db_url}")
    print(f"     CORS_ORIGINS={cors}")
    print(f"     PASS_NIVEL2={sec['pass_nivel2']}")


def crear_base_datos(cfg):
    db = cfg["database"]
    try:
        import pymysql
    except ImportError:
        print("[ERROR] pymysql no instalado. Ejecute: pip install pymysql")
        sys.exit(1)

    print(f"\n[DB] Conectando a MySQL {db['host']}:{db['port']} como '{db['user']}'...")
    try:
        conn = pymysql.connect(
            host=db["host"],
            port=db["port"],
            user=db["user"],
            password=db["password"],
            charset="utf8mb4",
            connect_timeout=5,
        )
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db['name']}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
            )
        conn.commit()
        conn.close()
        print(f"[OK] Base de datos '{db['name']}' lista.")
    except pymysql.err.OperationalError as e:
        print(f"\n[ERROR] No se pudo conectar a MySQL: {e}")
        print("  Verifique que:")
        print("  1. MySQL este corriendo (Servicios de Windows)")
        print(f"  2. La contrasena en config.json sea correcta (campo 'password')")
        print(f"  3. El host '{db['host']}' y puerto {db['port']} sean accesibles")
        sys.exit(1)


async def crear_tablas_y_catalogo(cfg):
    """Crea tablas ORM y pobla catálogos iniciales."""
    import asyncio
    db_cfg = cfg["database"]
    os.environ["DATABASE_URL"] = (
        f"mysql+aiomysql://{db_cfg['user']}:{db_cfg['password']}"
        f"@{db_cfg['host']}:{db_cfg['port']}/{db_cfg['name']}"
    )

    from database import engine, Base, async_session
    from sqlalchemy import text
    import models  # registra todos los modelos

    MOTIVOS = [
        "Investigacion", "Tarea", "Consulta",
        "Lectura de libros digitales", "Uso de base de datos",
        "Trabajos academicos", "Uso de AutoCAD y/o Civil 3D",
        "Clases virtuales", "Otro",
    ]

    tablas_drop = [
        "sesiones", "alumnos_maestro", "escuelas",
        "facultades", "terminales", "catalogo_motivos", "alumnos", "usuarios",
    ]

    print("[DB] Recreando esquema...")
    async with engine.begin() as conn:
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for t in tablas_drop:
            await conn.execute(text(f"DROP TABLE IF EXISTS `{t}`"))
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[OK] Tablas creadas.")

    from sqlalchemy import select
    from models import CatalogoMotivo, Facultad, Escuela, Usuario
    from auth_service import hashear_password

    async with async_session() as db:
        for desc in MOTIVOS:
            existe = (await db.execute(select(CatalogoMotivo).where(CatalogoMotivo.descripcion == desc))).scalar_one_or_none()
            if not existe:
                db.add(CatalogoMotivo(descripcion=desc))
        await db.flush()

        admin = (await db.execute(select(Usuario).where(Usuario.username == "admin"))).scalar_one_or_none()
        if not admin:
            db.add(Usuario(
                username="admin",
                hashed_password=hashear_password("admin123"),
                nombre_completo="Administrador",
                rol="admin",
            ))
        await db.commit()

    await engine.dispose()
    print("[OK] Catalogos y usuario admin creados.")
    print("     Login: admin / admin123")


def main():
    print("=" * 50)
    print("  Configurador - Control Biblioteca UNASAM")
    print("=" * 50)

    cfg = cargar_config()
    print(f"\n[OK] config.json leido.")

    generar_env(cfg)
    crear_base_datos(cfg)

    import asyncio
    asyncio.run(crear_tablas_y_catalogo(cfg))

    print("\n[OK] Instalacion completada. Inicie el servidor con servidor_run.bat")


if __name__ == "__main__":
    main()
