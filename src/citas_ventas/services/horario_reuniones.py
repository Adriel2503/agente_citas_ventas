"""
Horario de reuniones: fetch desde API MaravIA y formateo para system prompt.
Usa OBTENER_HORARIO_REUNIONES (ws_informacion_ia.php) a través de horario_cache.
"""

from typing import Any

try:
    from ..logger import get_logger
    from .horario_cache import get_horario
except ImportError:
    from citas_ventas.logger import get_logger
    from citas_ventas.services.horario_cache import get_horario

logger = get_logger(__name__)

_DIAS_ORDEN = [
    ("Lunes", "reunion_lunes"),
    ("Martes", "reunion_martes"),
    ("Miércoles", "reunion_miercoles"),
    ("Jueves", "reunion_jueves"),
    ("Viernes", "reunion_viernes"),
    ("Sábado", "reunion_sabado"),
    ("Domingo", "reunion_domingo"),
]


def format_horario_for_system_prompt(horario_reuniones: dict[str, Any]) -> str:
    """
    Formatea el horario de reuniones para inyectar en el system prompt.
    Estructura: lista por día con rango de hora o "Cerrado".

    Args:
        horario_reuniones: Dict con reunion_lunes, reunion_martes, etc.
                          Valores: "10:00-19:00" o null.

    Returns:
        String listo para el system prompt.
    """
    if not horario_reuniones:
        return "No hay horario cargado."

    lineas = []
    for nombre_dia, clave in _DIAS_ORDEN:
        valor = horario_reuniones.get(clave)
        if valor and str(valor).strip():
            rango = str(valor).strip().replace("-", " - ")
            lineas.append(f"- {nombre_dia}: {rango}")
        else:
            lineas.append(f"- {nombre_dia}: Cerrado")

    if not lineas:
        return "No hay horario cargado."
    return "\n".join(lineas)


async def fetch_horario_reuniones(id_empresa: Any | None) -> str:
    """
    Obtiene el horario de reuniones desde la cache/API y lo devuelve formateado
    para el system prompt.

    Reutiliza la misma cache TTL que schedule_validator.py, por lo que no
    genera llamadas duplicadas a la API si ambos la necesitan.

    Args:
        id_empresa: ID de la empresa (int o str). Si es None, retorna mensaje por defecto.

    Returns:
        String formateado para el prompt o "No hay horario cargado." si falla.
    """
    if id_empresa is None or id_empresa == "":
        return "No hay horario cargado."

    logger.debug("[HORARIO] Obteniendo horario para id_empresa=%s", id_empresa)
    horario = await get_horario(id_empresa)
    if not horario:
        logger.info("[HORARIO] Sin horario para id_empresa=%s", id_empresa)
        return "No hay horario cargado."

    logger.info("[HORARIO] Horario cargado para id_empresa=%s", id_empresa)
    return format_horario_for_system_prompt(horario)


__all__ = ["fetch_horario_reuniones", "format_horario_for_system_prompt"]
