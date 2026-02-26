"""
Cache compartida para OBTENER_HORARIO_REUNIONES.

Usada por horario_reuniones.py (system prompt) y schedule_validator.py (validación),
eliminando la duplicación de llamadas a la API cuando ambos necesitan el mismo dato.

TTLCache con asyncio.Lock por empresa para evitar thundering herd:
si N coroutines de la misma empresa llegan con cache vacío, solo la primera
hace el HTTP call; las demás esperan el lock y encuentran el cache ya lleno.
"""

import asyncio
from typing import Any

from cachetools import TTLCache

try:
    from .. import config as app_config
    from ..logger import get_logger
    from ..metrics import track_api_call, update_cache_stats
    from .http_client import post_with_logging
    from .circuit_breaker import informacion_cb
    from ._resilience import resilient_call
except ImportError:
    from citas_ventas import config as app_config
    from citas_ventas.logger import get_logger
    from citas_ventas.metrics import track_api_call, update_cache_stats
    from citas_ventas.services.http_client import post_with_logging
    from citas_ventas.services.circuit_breaker import informacion_cb
    from citas_ventas.services._resilience import resilient_call

logger = get_logger(__name__)

_horario_cache: TTLCache = TTLCache(
    maxsize=500,
    ttl=app_config.SCHEDULE_CACHE_TTL_MINUTES * 60,
)

# Lock por id_empresa para serializar el fetch HTTP cuando el cache está vacío.
_fetch_locks: dict[Any, asyncio.Lock] = {}


def clear_horario_cache() -> None:
    """Limpia la cache de horarios (útil para testing)."""
    _horario_cache.clear()
    update_cache_stats("schedule", 0)
    logger.debug("[HORARIO_CACHE] Cache limpiada")


async def get_horario(id_empresa: Any | None) -> dict[str, Any] | None:
    """
    Obtiene el dict horario_reuniones desde la API con cache TTL.

    Args:
        id_empresa: ID de la empresa (int o str). Si es None o vacío, retorna None.

    Returns:
        Dict con el horario_reuniones o None si no hay datos o falla.
    """
    if id_empresa is None or id_empresa == "":
        return None

    # 1. Fast path: cache hit
    if id_empresa in _horario_cache:
        logger.debug("[HORARIO_CACHE] Cache hit id_empresa=%s", id_empresa)
        return _horario_cache[id_empresa]

    # 2. Fast reject: evita adquirir el lock cuando el circuito está abierto
    if informacion_cb.is_open(id_empresa):
        return None

    # 3. Cache miss: serializar fetch por id_empresa (thundering herd prevention)
    lock = _fetch_locks.setdefault(id_empresa, asyncio.Lock())
    async with lock:
        # 4. Double-check: otra coroutine pudo llenar el cache mientras esperábamos
        if id_empresa in _horario_cache:
            return _horario_cache[id_empresa]

        # 5. Fetch real — solo una coroutine por id_empresa llega aquí a la vez
        payload = {
            "codOpe": "OBTENER_HORARIO_REUNIONES",
            "id_empresa": id_empresa,
        }
        logger.debug("[HORARIO_CACHE] Fetching horario id_empresa=%s", id_empresa)

        async def _fetcher():
            with track_api_call("obtener_horario"):
                return await post_with_logging(app_config.API_INFORMACION_URL, payload)

        try:
            data = await resilient_call(
                _fetcher,
                cb=informacion_cb,
                circuit_key=id_empresa,
                service_name="HORARIO_CACHE",
            )

            if data.get("success") and data.get("horario_reuniones"):
                horario = data["horario_reuniones"]
                _horario_cache[id_empresa] = horario
                update_cache_stats("schedule", len(_horario_cache))
                logger.debug("[HORARIO_CACHE] Horario cacheado id_empresa=%s", id_empresa)
                return horario

            logger.info(
                "[HORARIO_CACHE] Respuesta sin horario id_empresa=%s: %s",
                id_empresa, data.get("error"),
            )
            return None

        except Exception as e:
            logger.info(
                "[HORARIO_CACHE] No se pudo obtener horario id_empresa=%s: %s",
                id_empresa, e,
            )
            return None
        finally:
            # Elimina el lock una vez terminado el fetch (éxito o fallo).
            # Coroutines que ya capturaron la referencia local siguen funcionando
            # porque su variable `lock` mantiene el objeto vivo.
            _fetch_locks.pop(id_empresa, None)


__all__ = ["get_horario", "clear_horario_cache"]
