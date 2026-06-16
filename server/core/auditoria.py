# core/auditoria.py — Registro de bitácora de acciones administrativas.
#
# La auditoría NUNCA debe romper la acción principal: cualquier fallo se traga
# y solo deja un log [AUDIT-FAIL]. La acción real continúa y commitea normal.
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from database import async_session
from models import Auditoria

logger = logging.getLogger("control")


async def registrar_auditoria(usuario, accion, *, rol=None, objetivo=None,
                              detalle=None, ip_origen=None, db: AsyncSession = None):
    """Registra una acción administrativa en la tabla de auditoría.

    - Si se pasa `db` (caso REST con get_db, o un bloque WS que ya abrió sesión):
      hace db.add(...) y flush; el commit lo hace el dueño de la transacción, así
      la auditoría va atómica con la acción (si la acción revierte, la auditoría
      también — comportamiento correcto).
    - Si NO se pasa `db`: abre su propia sesión independiente y commitea sola
      (caso de comandos WS que no abren sesión, ej. apagar/shutdown).

    NUNCA propaga excepciones: un fallo de auditoría no debe abortar la operación.
    """
    try:
        reg = Auditoria(
            usuario   = (usuario or "?")[:100],
            rol       = (str(rol)[:20] if rol else None),
            accion    = accion[:60],
            objetivo  = (str(objetivo)[:255] if objetivo else None),
            detalle   = (str(detalle)[:500] if detalle else None),
            ip_origen = (str(ip_origen)[:45] if ip_origen else None),
        )
        if db is not None:
            db.add(reg)
            await db.flush()          # visible en la misma tx; el commit lo hace el caller
        else:
            async with async_session() as own:
                own.add(reg)
                await own.commit()
    except Exception as e:
        # La auditoría jamás debe abortar ni revertir la acción real.
        logger.error(f"[AUDIT-FAIL] no se pudo registrar '{accion}' por '{usuario}': {e}")
