"""
configurador.py — Lee config.json, genera .env y crea la BD si no existe.
Uso: python configurador.py

Es SEGURO volver a ejecutarlo (p.ej. para cambiar la IP o la contraseña de
MySQL): NUNCA borra tablas ni datos. Solo crea lo que falte. Las columnas
nuevas las agrega el propio servidor al arrancar (_migrar_columnas).
"""
import json
import sys
import os
import secrets
from urllib.parse import quote_plus


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
    db_url = _db_url(db)

    # Conservar TODO lo que ya tenga el .env (SECRET_KEY, variables agregadas a
    # mano como MYSQLDUMP_PATH...) y solo actualizar las claves que manejamos.
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    previas = {}
    orden = []
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    previas[k.strip()] = v
                    orden.append(k.strip())

    secret_key = previas.get("SECRET_KEY", "")
    if not secret_key or "CAMBIAR" in secret_key or len(secret_key) < 32:
        secret_key = secrets.token_urlsafe(32)
    # Contraseña inicial de las cuentas del panel: aleatoria, nunca 'admin123'.
    pass_inicial = previas.get("DEFAULT_ADMIN_PASSWORD", "")
    nueva_inicial = not pass_inicial
    if nueva_inicial:
        pass_inicial = "Unasam" + secrets.token_hex(4)

    nuevas = {
        "DATABASE_URL": db_url,
        "SECRET_KEY": secret_key,
        "CORS_ORIGINS": cors,
        "PASS_NIVEL2": sec["pass_nivel2"],
        "DEFAULT_ADMIN_PASSWORD": pass_inicial,
    }
    previas.update(nuevas)
    for k in nuevas:
        if k not in orden:
            orden.append(k)

    with open(env_path, "w", encoding="utf-8") as f:
        f.write("".join(f"{k}={previas[k]}\n" for k in orden))
    print("[OK] .env generado (se conservaron las variables existentes).")
    print(f"     DATABASE_URL=mysql+aiomysql://{db['user']}:****@{db['host']}:{port}/{db['name']}")
    print(f"     CORS_ORIGINS={cors}")
    return pass_inicial if nueva_inicial else None


def _db_url(db) -> str:
    """URL de SQLAlchemy con usuario/contraseña codificados (una contraseña con
    @ : / # rompía la URL)."""
    return (f"mysql+aiomysql://{quote_plus(str(db['user']))}:{quote_plus(str(db['password']))}"
            f"@{db['host']}:{db['port']}/{db['name']}")


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
    os.environ["DATABASE_URL"] = _db_url(db_cfg)

    from database import engine, Base, async_session
    from sqlalchemy import text
    import models  # registra todos los modelos

    MOTIVOS = [
        "Investigacion", "Tarea", "Consulta",
        "Lectura de libros digitales", "Uso de base de datos",
        "Trabajos academicos", "Uso de AutoCAD y/o Civil 3D",
        "Clases virtuales", "Otro",
    ]

    # Antes aqui se hacia DROP TABLE de sesiones, alumnos, usuarios... en CADA
    # ejecucion: reinstalar para cambiar la IP borraba todo el historial.
    # create_all solo crea las tablas que faltan; nunca toca las existentes.
    print("[DB] Creando las tablas que falten (no se borra nada)...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[OK] Esquema listo.")

    from sqlalchemy import select
    from models import CatalogoMotivo, Facultad, Escuela, Usuario
    from auth_service import hashear_password

    async with async_session() as db:
        for desc in MOTIVOS:
            existe = (await db.execute(select(CatalogoMotivo).where(CatalogoMotivo.descripcion == desc))).scalar_one_or_none()
            if not existe:
                db.add(CatalogoMotivo(descripcion=desc))
        await db.flush()

        await db.commit()
        hay_usuarios = (await db.execute(select(Usuario.id).limit(1))).scalars().first() is not None

    await engine.dispose()
    print("[OK] Catalogos listos.")
    # Las cuentas 'superadmin' y 'admin' las crea el servidor al primer arranque
    # con DEFAULT_ADMIN_PASSWORD del .env.
    return hay_usuarios


def main():
    print("=" * 50)
    print("  Configurador - Control Biblioteca UNASAM")
    print("=" * 50)

    cfg = cargar_config()
    print(f"\n[OK] config.json leido.")

    pass_inicial = generar_env(cfg)
    crear_base_datos(cfg)

    import asyncio
    hay_usuarios = asyncio.run(crear_tablas_y_catalogo(cfg))

    print("\n[OK] Instalacion completada. Inicie el servidor con servidor_run.bat")
    if pass_inicial and not hay_usuarios:
        print("\n" + "=" * 50)
        print("  CUENTAS INICIALES DEL PANEL (anotelas ahora):")
        print("    usuario: superadmin   contrasena: " + pass_inicial)
        print("    usuario: admin        contrasena: " + pass_inicial)
        print("  Cambielas al entrar: Configuracion > Credenciales.")
        print("=" * 50)


if __name__ == "__main__":
    main()
