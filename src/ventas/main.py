"""
Servidor MCP del agente especializado en venta directa.
Usa FastMCP para exponer la herramienta chat según el protocolo MCP.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastmcp import FastMCP

try:
    from . import config as app_config
    from .agent import process_venta_message
    from .logger import setup_logging, get_logger
    from .services.api_informacion import close_http_client
except ImportError:
    from ventas import config as app_config
    from ventas.agent import process_venta_message
    from ventas.logger import setup_logging, get_logger
    from ventas.services.api_informacion import close_http_client

setup_logging(level=app_config.LOG_LEVEL, log_file=app_config.LOG_FILE or None)

logger = get_logger(__name__)


@asynccontextmanager
async def app_lifespan(server=None):
    """Lifespan del servidor: cierra el cliente HTTP compartido al apagar (compatible FastMCP v2)."""
    try:
        yield {}
    finally:
        await close_http_client()


mcp = FastMCP(
    name="Agente Ventas - MaravIA",
    instructions="Agente especializado en venta directa por chat",
    lifespan=app_lifespan,
)


@mcp.tool(name="venta_chat")
async def chat(
    message: str,
    session_id: int,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Agente especializado en venta directa.

    Es la herramienta que el orquestador debe llamar para flujos de compra.
    El agente usa tools internas (búsqueda de productos, etc.) y maneja
    el flujo: información, pedido, envío o recojo, pago, comprobante, confirmación.

    Args:
        message: Mensaje del cliente
        session_id: ID de sesión (int, unificado con orquestador)
        context: Contexto con config.id_empresa (requerido), y opcionalmente
                 personalidad, nombre_negocio, nombre_asistente, propuesta_valor, medios_pago

    Returns:
        Respuesta del agente de ventas
    """
    if context is None:
        context = {}

    logger.info("[MCP] Mensaje recibido - Session: %s, Length: %s chars", session_id, len(message))

    try:
        reply = await asyncio.wait_for(
            process_venta_message(
                message=message,
                session_id=session_id,
                context=context,
            ),
            timeout=app_config.CHAT_TIMEOUT,
        )
        logger.info("[MCP] Respuesta generada - Length: %s chars", len(reply))
        return reply
    except asyncio.TimeoutError:
        error_msg = (
            f"La solicitud tardó más de {app_config.CHAT_TIMEOUT}s. Por favor, intenta de nuevo."
        )
        logger.error("[MCP] Timeout en process_venta_message (CHAT_TIMEOUT=%s)", app_config.CHAT_TIMEOUT)
        return error_msg
    except ValueError as e:
        error_msg = f"Error de configuración: {str(e)}"
        logger.error("[MCP] %s", error_msg)
        return error_msg
    except asyncio.CancelledError:
        raise
    except Exception as e:
        error_msg = f"Error procesando mensaje: {str(e)}"
        logger.error("[MCP] %s", error_msg, exc_info=True)
        return error_msg


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("INICIANDO AGENTE VENTAS - MaravIA")
    logger.info("=" * 60)
    logger.info("Host: %s:%s", app_config.SERVER_HOST, app_config.SERVER_PORT)
    logger.info("Modelo: %s", app_config.OPENAI_MODEL)
    logger.info("Timeouts: OPENAI=%ss, API=%ss, CHAT=%ss", app_config.OPENAI_TIMEOUT, app_config.API_TIMEOUT, app_config.CHAT_TIMEOUT)
    logger.info("Tool expuesta al orquestador: venta_chat")
    logger.info("Tools internas: search_productos_servicios")
    logger.info("=" * 60)

    try:
        mcp.run(
            transport="http",
            host=app_config.SERVER_HOST,
            port=app_config.SERVER_PORT,
        )
    except KeyboardInterrupt:
        logger.info("Servidor detenido por el usuario")
    except Exception as e:
        logger.critical("Error crítico en el servidor: %s", e, exc_info=True)
        raise
