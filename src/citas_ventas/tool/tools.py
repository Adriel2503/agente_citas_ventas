"""
Tools del agente de citas y ventas.
Versión mínima: búsqueda de productos/servicios (BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS).
"""

import logging
from typing import Any

from langchain.tools import tool, ToolRuntime

try:
    from ..metrics import TOOL_CALLS, track_tool_execution
    from ..services.busqueda_productos import buscar_productos_servicios, format_productos_para_respuesta
    from ..services.registrar_pedido import registrar_pedido as _svc_registrar_pedido
    from ..services.schedule_validator import ScheduleValidator
    from ..services.booking import confirm_booking
    from ..validation import validate_booking_data, validate_date_format
except ImportError:
    from citas_ventas.metrics import TOOL_CALLS, track_tool_execution
    from citas_ventas.services.busqueda_productos import buscar_productos_servicios, format_productos_para_respuesta
    from citas_ventas.services.registrar_pedido import registrar_pedido as _svc_registrar_pedido
    from citas_ventas.services.schedule_validator import ScheduleValidator
    from citas_ventas.services.booking import confirm_booking
    from citas_ventas.validation import validate_booking_data, validate_date_format

logger = logging.getLogger(__name__)


@tool
async def search_productos_servicios(
    busqueda: str,
    runtime: ToolRuntime = None
) -> str:
    """
    Busca productos y servicios del catálogo por nombre o descripción (ventas directas).
    Úsala cuando el cliente pregunte por precios, descripción o detalles de un producto o servicio.

    Args:
        busqueda: Término de búsqueda (ej: "Juego", "laptop", "consulta")
        runtime: Contexto automático (inyectado por LangChain)

    Returns:
        Texto con los productos/servicios encontrados (precio, categoría, descripción)
    """
    logger.debug("[TOOL] search_productos_servicios - busqueda: %s", busqueda)

    ctx = runtime.context if runtime else None
    if not ctx or getattr(ctx, "id_empresa", None) is None:
        logger.warning("[TOOL] search_productos_servicios - llamada sin contexto de empresa")
        return "No tengo el contexto de empresa para buscar productos; no puedo mostrar el catálogo en este momento."
    id_empresa = ctx.id_empresa

    _tool_status = "ok"
    try:
        result = await buscar_productos_servicios(
            id_empresa=id_empresa,
            busqueda=busqueda,
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
        _tool_status = "error"
        logger.error(
            "[TOOL] search_productos_servicios - %s: %s (busqueda=%r, id_empresa=%s)",
            type(e).__name__,
            e,
            busqueda,
            id_empresa,
            exc_info=True,
        )
        return f"Error al buscar: {str(e)}. Intenta de nuevo."

    finally:
        TOOL_CALLS.labels(tool="search_productos_servicios", status=_tool_status).inc()


@tool
async def registrar_pedido(
    productos: list[dict[str, Any]],
    operacion: str,
    modalidad: str,
    tipo_envio: str,
    nombre: str,
    dni: str,
    celular: str,
    medio_pago: str,
    monto_pagado: float,
    direccion: str = "",
    costo_envio: float = 0,
    observacion: str = "",
    fecha_entrega_estimada: str = "",
    email: str = "",
    sucursal: str = "",
    runtime: ToolRuntime = None,
) -> str:
    """
    Registra el pedido del cliente en el sistema una vez confirmado.

    Úsala SOLO cuando el cliente haya confirmado el pedido Y tengas todos los datos
    obligatorios: productos elegidos, número de operación del comprobante, datos del
    cliente (nombre, DNI, celular) y datos de entrega o recojo.

    Campos clave:
    - productos: lista de objetos con "id_catalogo" (el ID devuelto por
      search_productos_servicios) y "cantidad". Ejemplo:
      [{"id_catalogo": 3344, "cantidad": 2}]
    - operacion: número/código de transacción leído de la imagen del comprobante
      (Yape, BCP, transferencia, etc.).
    - modalidad: "Delivery" si el cliente quiere envío a domicilio, "Recojo" si retira
      en tienda.
    - tipo_envio: tipo de envío acordado según las zonas del negocio (ej. "Delivery",
      "Recojo", "rapidito"). Si es recojo en tienda puedes usar "Recojo".
    - direccion: dirección completa de entrega. Vacío si es recojo en sucursal.
    - costo_envio: monto numérico del costo de envío (0 si recojo).
    - sucursal: nombre de la sucursal de recojo. Vacío si es delivery.
    - nombre, dni, celular: datos del cliente (obligatorios).
    - email: correo del cliente (opcional).
    - medio_pago: medio con el que pagó (ej. "yape", "transferencia").
    - monto_pagado: monto total pagado.
    - fecha_entrega_estimada: fecha estimada de entrega en formato "YYYY-MM-DD"
      (usa la información del sistema de envíos del negocio).
    - observacion: notas adicionales (opcional).

    IMPORTANTE: No inventes IDs de producto. El id_catalogo debe ser el ID que apareció
    en la respuesta de search_productos_servicios. Si no tienes algún dato obligatorio,
    pídelo al cliente antes de llamar esta herramienta.

    Args:
        productos:              Lista de {"id_catalogo": int, "cantidad": int}.
        operacion:              Número de operación del comprobante.
        modalidad:              "Delivery" o "Recojo".
        tipo_envio:             Tipo de envío (ej. "Delivery", "Recojo").
        nombre:                 Nombre completo del cliente.
        dni:                    DNI del cliente.
        celular:                Teléfono del cliente.
        medio_pago:             Medio de pago (ej. "yape").
        monto_pagado:           Monto pagado.
        direccion:              Dirección de entrega (vacío si recojo).
        costo_envio:            Costo de envío (0 si recojo).
        observacion:            Nota adicional (opcional).
        fecha_entrega_estimada: Fecha estimada de entrega "YYYY-MM-DD" (opcional).
        email:                  Correo del cliente (opcional).
        sucursal:               Sucursal de recojo (vacío si delivery).
        runtime:                Contexto automático inyectado por LangChain.

    Returns:
        Mensaje de éxito con número de pedido, o mensaje de error.
    """
    logger.debug(
        "[TOOL] registrar_pedido - modalidad=%s productos=%s operacion=%s",
        modalidad, productos, operacion,
    )

    ctx = runtime.context if runtime else None
    if not ctx or getattr(ctx, "id_empresa", None) is None:
        logger.warning("[TOOL] registrar_pedido - llamada sin contexto de empresa")
        return "No tengo el contexto de empresa; no puedo registrar el pedido en este momento."

    id_empresa = ctx.id_empresa
    id_prospecto = getattr(ctx, "session_id", 0)

    _tool_status = "ok"
    try:
        return await _svc_registrar_pedido(
            id_empresa=id_empresa,
            id_prospecto=id_prospecto,
            productos=productos,
            operacion=operacion,
            modalidad=modalidad,
            tipo_envio=tipo_envio,
            nombre=nombre,
            dni=dni,
            celular=celular,
            medio_pago=medio_pago,
            monto_pagado=monto_pagado,
            direccion=direccion,
            costo_envio=costo_envio,
            observacion=observacion,
            fecha_entrega_estimada=fecha_entrega_estimada,
            email=email,
            sucursal=sucursal,
        )
    except Exception as e:
        _tool_status = "error"
        logger.error(
            "[TOOL] registrar_pedido - %s: %s (id_empresa=%s, operacion=%r)",
            type(e).__name__,
            e,
            id_empresa,
            operacion,
            exc_info=True,
        )
        return f"Error al registrar el pedido: {str(e)}. Intenta de nuevo."

    finally:
        TOOL_CALLS.labels(tool="registrar_pedido", status=_tool_status).inc()


@tool
async def check_availability(
    date: str,
    time: str | None = None,
    runtime: ToolRuntime = None
) -> str:
    """
    Consulta horarios disponibles para una cita/reunión en una fecha dada.

    Usa esta herramienta cuando el cliente pregunte por disponibilidad
    o cuando necesites verificar si una fecha/hora específica está libre.

    Si el cliente indicó una hora concreta (ej. "a las 2pm", "a las 14:00"), pásala en time
    para consultar disponibilidad exacta de ese slot (CONSULTAR_DISPONIBILIDAD).
    Si no pasas time, se devuelven sugerencias para hoy/mañana (SUGERIR_HORARIOS).

    Args:
        date: Fecha en formato ISO (YYYY-MM-DD)
        time: Hora opcional en formato HH:MM AM/PM (ej. "2:00 PM") o 24h. Si el cliente dijo una hora concreta, pásala aquí.
        runtime: Runtime context automático (inyectado por LangChain)

    Returns:
        Texto con horarios disponibles o sugerencias para esa fecha/hora

    Examples:
        >>> await check_availability("2026-01-27")
        "Horarios sugeridos: Lunes 27/01 - 09:00 AM, 10:00 AM, 02:00 PM..."
        >>> await check_availability("2026-01-31", "2:00 PM")
        "El 2026-01-31 a las 2:00 PM está disponible. ¿Confirmamos la cita?"
    """
    logger.debug("[TOOL] check_availability - Fecha: %s, Hora: %s", date, time or "no indicada")

    is_valid, error = validate_date_format(date)
    if not is_valid:
        return error

    # Obtener configuración del runtime context
    ctx = runtime.context if runtime else None
    id_empresa = ctx.id_empresa if ctx else 1
    duracion_cita_minutos = ctx.duracion_cita_minutos if ctx else 60
    slots = ctx.slots if ctx else 60
    agendar_usuario = ctx.agendar_usuario if ctx else 1
    agendar_sucursal = ctx.agendar_sucursal if ctx else 0

    try:
        with track_tool_execution("check_availability"):
            validator = ScheduleValidator(
                id_empresa=id_empresa,
                duracion_cita_minutos=duracion_cita_minutos,
                slots=slots,
                es_cita=True,
                agendar_usuario=agendar_usuario,
                agendar_sucursal=agendar_sucursal
            )

            recommendations = await validator.recommendation(
                fecha_solicitada=date,
                hora_solicitada=time.strip() if time and time.strip() else None,
            )

            if recommendations and recommendations.get("text"):
                logger.debug("[TOOL] check_availability - Recomendaciones obtenidas")
                return recommendations["text"]
            else:
                logger.warning("[TOOL] check_availability - Sin recomendaciones, usando fallback")
                return f"Horarios disponibles para el {date}. Consulta directamente para más detalles."

    except Exception as e:
        logger.error("[TOOL] check_availability - Error: %s", e, exc_info=True)
        return "Horarios típicos disponibles:\n• Mañana: 09:00, 10:00, 11:00\n• Tarde: 14:00, 15:00, 16:00"


@tool
async def create_booking(
    date: str,
    time: str,
    customer_name: str,
    customer_contact: str,
    runtime: ToolRuntime = None
) -> str:
    """
    Crea una nueva cita (evento en calendario) con validación y confirmación real.

    Usa esta herramienta SOLO cuando tengas TODOS los datos necesarios:
    - Fecha (YYYY-MM-DD), Hora (HH:MM AM/PM)
    - Nombre completo del cliente, Email del cliente (customer_contact)

    La herramienta validará el horario y creará el evento en ws_calendario (CREAR_EVENTO).
    La respuesta puede incluir enlace de videollamada (Google Meet) o mensaje de cita confirmada.

    Args:
        date: Fecha de la cita (YYYY-MM-DD)
        time: Hora de la cita (HH:MM AM/PM)
        customer_name: Nombre completo del cliente
        customer_contact: Email del cliente (ej: cliente@ejemplo.com)
        runtime: Runtime context automático (inyectado por LangChain)

    Returns:
        Mensaje de confirmación, detalles (fecha, hora, nombre) y, si aplica,
        enlace de videollamada o aviso de "cita confirmada"; o mensaje de error

    Examples:
        >>> await create_booking("2026-01-27", "02:00 PM", "Juan Pérez", "cliente@ejemplo.com")
        "Evento agregado correctamente. Detalles: ... La reunión será por videollamada. Enlace: https://meet.google.com/..."
    """
    logger.debug("[TOOL] create_booking - %s %s | %s", date, time, customer_name)
    logger.info("[create_booking] Tool en uso: create_booking")

    is_valid, error = validate_date_format(date)
    if not is_valid:
        return error

    # Obtener configuración del runtime context
    ctx = runtime.context if runtime else None
    id_empresa = ctx.id_empresa if ctx else 1
    duracion_cita_minutos = ctx.duracion_cita_minutos if ctx else 60
    slots = ctx.slots if ctx else 60
    agendar_usuario = ctx.agendar_usuario if ctx else 1
    agendar_sucursal = ctx.agendar_sucursal if ctx else 0
    id_prospecto = ctx.id_prospecto if ctx else 0
    usuario_id = getattr(ctx, "usuario_id", 1) if ctx else 1
    correo_usuario = getattr(ctx, "correo_usuario", "") or ""

    try:
        with track_tool_execution("create_booking"):
            # 1. VALIDAR datos de entrada
            logger.debug("[TOOL] create_booking - Validando datos de entrada")
            is_valid, error = validate_booking_data(
                date=date,
                time=time,
                customer_name=customer_name,
                customer_contact=customer_contact
            )

            if not is_valid:
                logger.warning("[TOOL] create_booking - Datos inválidos: %s", error)
                return f"Datos inválidos: {error}\n\nPor favor verifica la información."

            # 2. VALIDAR horario con ScheduleValidator
            logger.debug("[TOOL] create_booking - Validando horario")
            validator = ScheduleValidator(
                id_empresa=id_empresa,
                duracion_cita_minutos=duracion_cita_minutos,
                slots=slots,
                es_cita=True,
                agendar_usuario=agendar_usuario,
                agendar_sucursal=agendar_sucursal,
                log_create_booking_apis=True,
            )

            validation = await validator.validate(date, time)
            logger.debug("[TOOL] create_booking - Validación: %s", validation)

            if not validation["valid"]:
                logger.warning("[TOOL] create_booking - Horario no válido: %s", validation["error"])
                return f"{validation['error']}\n\nPor favor elige otra fecha u hora."

            # 3. Crear evento en ws_calendario (CREAR_EVENTO)
            logger.debug("[TOOL] create_booking - Creando evento en API")
            id_prospecto_val = id_prospecto if (id_prospecto and id_prospecto > 0) else (ctx.session_id if ctx else 0)
            booking_result = await confirm_booking(
                usuario_id=usuario_id,
                id_prospecto=id_prospecto_val,
                nombre_completo=customer_name,
                correo_cliente=customer_contact or "",
                fecha=date,
                hora=time,
                agendar_usuario=agendar_usuario,
                duracion_cita_minutos=duracion_cita_minutos,
                correo_usuario=correo_usuario,
                log_create_booking_apis=True,
            )

            logger.debug("[TOOL] create_booking - Resultado: %s", booking_result)

            if booking_result["success"]:
                api_message = booking_result.get("message") or "Evento creado correctamente"
                logger.info("[TOOL] create_booking - Éxito")
                lines = [
                    api_message,
                    "",
                    "Detalles:",
                    f"• Fecha: {date}",
                    f"• Hora: {time}",
                    f"• Nombre: {customer_name}",
                    "",
                ]
                if booking_result.get("google_meet_link"):
                    lines.append(f"La reunión será por videollamada. Enlace: {booking_result['google_meet_link']}")
                elif booking_result.get("google_calendar_synced") is False:
                    lines.append("Tu cita está confirmada. No se pudo generar el enlace de videollamada; te contactaremos con los detalles.")
                lines.append("")
                lines.append("¡Te esperamos!")
                return "\n".join(lines)
            else:
                error_msg = booking_result.get("error") or booking_result.get("message") or "No se pudo confirmar la cita"
                logger.warning("[TOOL] create_booking - Fallo: %s", error_msg)
                return f"{error_msg}\n\nPor favor intenta nuevamente."

    except Exception as e:
        logger.error("[TOOL] create_booking - Error inesperado: %s", e, exc_info=True)
        return f"Error inesperado al crear la cita: {str(e)}\n\nPor favor intenta nuevamente."


AGENT_TOOLS = [
    search_productos_servicios,
    registrar_pedido,
    check_availability,
    create_booking,
]

__all__ = ["search_productos_servicios", "registrar_pedido", "check_availability", "create_booking", "AGENT_TOOLS"]
