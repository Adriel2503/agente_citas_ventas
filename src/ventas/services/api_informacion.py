"""
Cliente HTTP para ws_informacion_ia.php.
Helper compartido para categorías, sucursales y búsqueda de productos.
"""

import json
import logging
from typing import Any, Dict

import httpx

try:
    from ..config import config as app_config
except ImportError:
    from ventas.config import config as app_config

logger = logging.getLogger(__name__)


async def post_informacion(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST a ws_informacion_ia.php.

    Raises:
        httpx.HTTPStatusError: Si status code no 2xx
        httpx.RequestError: Error de conexión
        httpx.TimeoutException: Timeout

    Returns:
        Dict parseado del JSON de respuesta
    """
    logger.debug(
        "[API_INFORMACION] POST %s - %s",
        app_config.API_INFORMACION_URL,
        json.dumps(payload, ensure_ascii=False),
    )
    async with httpx.AsyncClient(timeout=app_config.API_TIMEOUT) as client:
        response = await client.post(
            app_config.API_INFORMACION_URL,
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()


__all__ = ["post_informacion"]
