# core/rate_limit.py - Rate limiting simple en memoria, por IP.
#
# Pensado para un solo proceso de servidor (la laptop del encargado o un PC del
# centro de cómputo). No usa Redis ni dependencias externas: guarda las marcas
# de tiempo de cada petición en un diccionario y descarta las que salen de la
# ventana. Suficiente para frenar fuerza bruta y enumeración desde la LAN del
# laboratorio. Si algún día se migra a varios procesos/servidores, habría que
# mover esto a un almacén compartido (Redis).
#
# Resuelve E-6 (endpoints públicos del kiosco sin rate limit) y E-7 (login con
# bloqueo por usuario pero no por IP).
import time
import threading
from collections import deque
from fastapi import Request, HTTPException

# Estructura: { (clave, ip): deque[timestamps] }
_eventos: dict[tuple[str, str], deque] = {}
_lock = threading.Lock()

# Última limpieza global, para no recorrer todo el dict en cada petición.
_ultima_limpieza = [0.0]
_INTERVALO_LIMPIEZA = 300  # segundos


def _ip_cliente(request: Request) -> str:
    """IP real del cliente.

    Usa la IP de la conexión TCP (request.client.host). NO confía en
    X-Forwarded-For salvo que el servidor esté detrás de un proxy de confianza;
    aquí el kiosco conecta directo, así que usamos la IP real para que nadie
    pueda evadir el límite falsificando una cabecera.
    """
    if request.client and request.client.host:
        return request.client.host
    return "desconocido"


def _limpiar_vencidos(ahora: float, ventana: float) -> None:
    """Elimina entradas cuyas marcas ya salieron de la ventana (mantenimiento)."""
    if ahora - _ultima_limpieza[0] < _INTERVALO_LIMPIEZA:
        return
    _ultima_limpieza[0] = ahora
    muertas = []
    for k, dq in _eventos.items():
        while dq and ahora - dq[0] > ventana:
            dq.popleft()
        if not dq:
            muertas.append(k)
    for k in muertas:
        _eventos.pop(k, None)


def verificar(clave: str, ip: str, limite: int, ventana_seg: float) -> bool:
    """Devuelve True si la petición está dentro del límite, False si lo excede.

    clave: identifica el grupo de endpoints (p.ej. "login", "kiosco").
    ip: IP del cliente.
    limite: máximo de peticiones permitidas en la ventana.
    ventana_seg: tamaño de la ventana en segundos.
    """
    ahora = time.monotonic()
    k = (clave, ip)
    with _lock:
        _limpiar_vencidos(ahora, ventana_seg)
        dq = _eventos.get(k)
        if dq is None:
            dq = deque()
            _eventos[k] = dq
        # Descartar marcas fuera de la ventana.
        while dq and ahora - dq[0] > ventana_seg:
            dq.popleft()
        if len(dq) >= limite:
            return False
        dq.append(ahora)
        return True


def exigir(request: Request, clave: str, limite: int, ventana_seg: float,
           mensaje: str = "Demasiadas solicitudes. Intente de nuevo en unos minutos.") -> None:
    """Aplica el límite y lanza HTTP 429 si se excede. Para usar dentro de un endpoint."""
    ip = _ip_cliente(request)
    if not verificar(clave, ip, limite, ventana_seg):
        raise HTTPException(status_code=429, detail=mensaje)
