# koha_connector.py - Conector para sincronización con sistema Koha/OGE
import httpx
import logging
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models import Alumno

logger = logging.getLogger("control.koha")

# URL base de la API externa (Koha u OGE de UNASAM)
KOHA_API_URL = "http://localhost:8080/api/v1"  # Cambiar por URL real
OGE_API_URL = "http://localhost:9090/api"       # Cambiar por URL real


async def sincronizar_alumnos(db: AsyncSession) -> dict:
    """
    Sincronizar alumnos desde API externa.
    Graceful Degradation: si falla la API, se usa la caché local.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{OGE_API_URL}/alumnos")
            response.raise_for_status()
            alumnos_ext = response.json()

        actualizados = 0
        nuevos = 0
        for data in alumnos_ext:
            result = await db.execute(select(Alumno).where(Alumno.codigo == data["codigo"]))
            alumno = result.scalar_one_or_none()
            if alumno:
                alumno.nombres = data.get("nombres", alumno.nombres)
                alumno.apellidos = data.get("apellidos", alumno.apellidos)
                alumno.escuela = data.get("escuela", alumno.escuela)
                alumno.habilitado = data.get("habilitado", True)
                alumno.ultima_sync = datetime.utcnow()
                actualizados += 1
            else:
                nuevo = Alumno(
                    codigo=data["codigo"],
                    nombres=data["nombres"],
                    apellidos=data["apellidos"],
                    escuela=data.get("escuela"),
                    habilitado=data.get("habilitado", True),
                    ultima_sync=datetime.utcnow()
                )
                db.add(nuevo)
                nuevos += 1

        await db.commit()
        logger.info(f"Sincronización completada: {nuevos} nuevos, {actualizados} actualizados")
        return {"status": "ok", "nuevos": nuevos, "actualizados": actualizados}

    except (httpx.RequestError, httpx.HTTPStatusError) as e:
        logger.warning(f"Error al sincronizar con API externa: {e}. Usando caché local.")
        return {"status": "cache_local", "error": str(e)}


async def validar_alumno_externo(codigo: str) -> dict | None:
    """
    Validar alumno consultando API externa.
    Retorna None si la API no está disponible (graceful degradation).
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{OGE_API_URL}/alumnos/{codigo}")
            if response.status_code == 200:
                return response.json()
            return None
    except httpx.RequestError:
        logger.warning(f"API externa no disponible para validar alumno {codigo}")
        return None
