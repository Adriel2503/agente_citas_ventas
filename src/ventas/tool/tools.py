"""
Tools del agente de ventas.
Versión mínima: búsqueda de productos/servicios (BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS).
"""

import logging
from typing import Optional

from langchain.tools import tool, ToolRuntime

try:
    from ..services.busqueda_productos import buscar_productos_servicios, format_productos_para_respuesta
except ImportError:
    from ventas.services.busqueda_productos import buscar_productos_servicios, format_productos_para_respuesta

logger = logging.getLogger(__name__)


@tool
async def search_productos_servicios(
    busqueda: str,
    limite: int = 10,
    runtime: Optional[ToolRuntime] = None
) -> str:
    """
    Busca productos y servicios del catálogo por nombre o descripción (ventas directas).
    Úsala cuando el cliente pregunte por precios, descripción o detalles de un producto o servicio.

    Args:
        busqueda: Término de búsqueda (ej: "Juego", "laptop", "consulta")
        limite: Cantidad máxima de resultados (opcional, default 10)
        runtime: Contexto automático (inyectado por LangChain)

    Returns:
        Texto con los productos/servicios encontrados (precio, categoría, descripción)
    """
    logger.debug("[TOOL] search_productos_servicios - busqueda: %s, limite: %s", busqueda, limite)

    # id_empresa debe venir del contexto (orquestador envía config.id_empresa)
    if not (runtime and getattr(runtime, "context", None) and hasattr(runtime.context, "id_empresa")):
        logger.error("[TOOL] id_empresa no disponible en contexto. El orquestador debe enviar config.id_empresa.")
        return "No se pudo completar la búsqueda: configuración de empresa no disponible. Por favor, intenta de nuevo."

    id_empresa = runtime.context.id_empresa

    try:
        result = await buscar_productos_servicios(
            id_empresa=id_empresa,
            busqueda=busqueda,
            limite=limite,
            log_search_apis=True,
        )

        if not result["success"]:
            return result.get("error", "No se pudo completar la búsqueda.")

        productos = result.get("productos", [])
        if not productos:
            return f"No encontré productos o servicios que coincidan con '{busqueda}'. Prueba con otros términos."

        lineas = [f"Encontré {len(productos)} resultado(s) para '{busqueda}':\n"]
        lineas.append(format_productos_para_respuesta(productos))
        return "\n".join(lineas)

    except Exception as e:
        logger.error("[TOOL] search_productos_servicios - Error: %s", e, exc_info=True)
        return f"Error al buscar: {str(e)}. Intenta de nuevo."


AGENT_TOOLS = [search_productos_servicios]

__all__ = ["search_productos_servicios", "AGENT_TOOLS"]
