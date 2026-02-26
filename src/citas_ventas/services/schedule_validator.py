"""
Validador de horarios para citas/reuniones.
Versión mejorada con async, cache global y logging.
"""

import json
import httpx
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

try:
    from ..logger import get_logger
    from ..metrics import track_api_call
    from .. import config as app_config
    from .http_client import post_with_retry
    from .horario_cache import get_horario, clear_horario_cache
except ImportError:
    from citas_ventas.logger import get_logger
    from citas_ventas.metrics import track_api_call
    from citas_ventas import config as app_config
    from citas_ventas.services.http_client import post_with_retry
    from citas_ventas.services.horario_cache import get_horario, clear_horario_cache

logger = get_logger(__name__)

# Mapeo de día de la semana a campo de la base de datos
DAY_MAPPING = {
    0: "reunion_lunes",
    1: "reunion_martes",
    2: "reunion_miercoles",
    3: "reunion_jueves",
    4: "reunion_viernes",
    5: "reunion_sabado",
    6: "reunion_domingo"
}

# Días en español para formateo de sugerencias
DIAS_ESPANOL = {
    "Monday": "Lunes",
    "Tuesday": "Martes",
    "Wednesday": "Miércoles",
    "Thursday": "Jueves",
    "Friday": "Viernes",
    "Saturday": "Sábado",
    "Sunday": "Domingo"
}

_DIAS_NOMBRE = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_ZONA_PERU = ZoneInfo(app_config.TIMEZONE)


# ========== VALIDADOR DE HORARIOS ==========

class ScheduleValidator:
    """Validador de horarios para citas/reuniones con async y cache."""

    def __init__(
        self,
        id_empresa: int,
        duracion_cita_minutos: int = 60,
        slots: int = 60,
        es_cita: bool = True,
        agendar_usuario: int = 0,
        agendar_sucursal: int = 0,
        log_create_booking_apis: bool = False,
    ):
        self.id_empresa = id_empresa
        self.duracion_cita = timedelta(minutes=duracion_cita_minutos)
        self.duracion_minutos = duracion_cita_minutos
        self.slots = slots
        self.es_cita = es_cita
        self.agendar_usuario = agendar_usuario
        self.agendar_sucursal = agendar_sucursal
        self.log_create_booking_apis = log_create_booking_apis

    def _parse_time(self, time_str: str) -> datetime | None:
        """
        Parsea una hora en formato HH:MM AM/PM o HH:MM.

        Args:
            time_str: String con la hora

        Returns:
            Objeto datetime con la hora parseada o None si hay error
        """
        time_str = time_str.strip().upper()

        # Intentar formato 12 horas (HH:MM AM/PM)
        for fmt in ["%I:%M %p", "%I:%M%p", "%H:%M"]:
            try:
                return datetime.strptime(time_str, fmt)
            except ValueError:
                continue

        return None

    def _parse_time_range(self, range_str: str) -> tuple[datetime, datetime] | None:
        """
        Parsea un rango de horario como '09:00-18:00' o '9:00 AM - 6:00 PM'.

        Args:
            range_str: String con el rango de horas

        Returns:
            Tupla (hora_inicio, hora_fin) o None si hay error
        """
        if not range_str:
            return None

        # Separar por guión
        parts = range_str.replace(" ", "").split("-")
        if len(parts) != 2:
            # Intentar con " - " con espacios
            parts = range_str.split(" - ")
            if len(parts) != 2:
                return None

        start = self._parse_time(parts[0].strip())
        end = self._parse_time(parts[1].strip())

        if start and end:
            return (start, end)
        return None

    def _is_time_blocked(self, fecha: datetime, hora: datetime, horarios_bloqueados: str) -> bool:
        """
        Verifica si la hora está en los horarios bloqueados.

        Args:
            fecha: Fecha de la cita
            hora: Hora de la cita
            horarios_bloqueados: String JSON o CSV con horarios bloqueados

        Returns:
            True si está bloqueado, False en caso contrario
        """
        if not horarios_bloqueados:
            return False

        try:
            # Formato esperado: JSON array o string separado por comas
            try:
                bloqueados = json.loads(horarios_bloqueados)
            except json.JSONDecodeError:
                bloqueados = [b.strip() for b in horarios_bloqueados.split(",")]

            fecha_str = fecha.strftime("%Y-%m-%d")

            for bloqueo in bloqueados:
                if isinstance(bloqueo, dict):
                    if bloqueo.get("fecha") == fecha_str:
                        inicio = self._parse_time(bloqueo.get("inicio", ""))
                        fin = self._parse_time(bloqueo.get("fin", ""))
                        if inicio and fin:
                            if inicio.time() <= hora.time() < fin.time():
                                logger.debug("[BLOCKED] Hora %s está bloqueada", hora.time())
                                return True
                elif isinstance(bloqueo, str):
                    if fecha_str in bloqueo:
                        time_part = bloqueo.replace(fecha_str, "").strip()
                        rango = self._parse_time_range(time_part)
                        if rango:
                            inicio, fin = rango
                            if inicio.time() <= hora.time() < fin.time():
                                logger.debug("[BLOCKED] Hora %s está bloqueada", hora.time())
                                return True

        except Exception as e:
            logger.warning("[SCHEDULE] Error parseando horarios bloqueados: %s", e)

        return False

    async def _check_availability(self, fecha_str: str, hora_str: str) -> dict[str, Any]:
        """
        Verifica disponibilidad contra citas existentes.

        Args:
            fecha_str: Fecha en formato YYYY-MM-DD
            hora_str: Hora en formato HH:MM AM/PM

        Returns:
            Dict con:
            - available: bool
            - error: str (mensaje si no está disponible)
        """
        try:
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d")
            hora = self._parse_time(hora_str)
            if not hora:
                return {"available": True, "error": None}

            fecha_hora_inicio = fecha.replace(hour=hora.hour, minute=hora.minute)
            fecha_hora_fin = fecha_hora_inicio + self.duracion_cita

            payload = {
                "codOpe": "CONSULTAR_DISPONIBILIDAD",
                "id_empresa": self.id_empresa,
                "fecha_inicio": fecha_hora_inicio.strftime("%Y-%m-%d %H:%M:%S"),
                "fecha_fin": fecha_hora_fin.strftime("%Y-%m-%d %H:%M:%S"),
                "slots": self.slots,
                "agendar_usuario": self.agendar_usuario,
                "agendar_sucursal": self.agendar_sucursal
            }

            if self.log_create_booking_apis:
                logger.info("[create_booking] API 2: ws_agendar_reunion.php - CONSULTAR_DISPONIBILIDAD")
                logger.info("  URL: %s", app_config.API_AGENDAR_REUNION_URL)
                logger.info("  Enviado: %s", json.dumps(payload, ensure_ascii=False))
            logger.debug("[AVAILABILITY] Consultando: %s %s", fecha_str, hora_str)
            logger.debug("[AVAILABILITY] JSON enviado a ws_agendar_reunion.php (CONSULTAR_DISPONIBILIDAD): %s", json.dumps(payload, ensure_ascii=False, indent=2))

            with track_api_call("consultar_disponibilidad"):
                data = await post_with_retry(app_config.API_AGENDAR_REUNION_URL, json=payload)

            if self.log_create_booking_apis:
                logger.info("  Respuesta: %s", json.dumps(data, ensure_ascii=False))
            logger.debug("[AVAILABILITY] Disponible: %s", data.get("disponible"))

            if not data.get("success"):
                logger.warning("[AVAILABILITY] Respuesta sin éxito: %s", data)
                return {"available": True, "error": None}  # Graceful degradation

            if data.get("disponible"):
                return {"available": True, "error": None}
            else:
                return {
                    "available": False,
                    "error": "El horario seleccionado ya está ocupado. Por favor elige otra hora o fecha."
                }

        except httpx.TimeoutException:
            logger.warning("[AVAILABILITY] Timeout - graceful degradation")
            return {"available": True, "error": None}
        except httpx.HTTPError as e:
            logger.warning("[AVAILABILITY] Error HTTP: %s - graceful degradation", e)
            return {"available": True, "error": None}
        except Exception as e:
            logger.warning("[AVAILABILITY] Error inesperado: %s - graceful degradation", e)
            return {"available": True, "error": None}

    async def validate(self, fecha_str: str, hora_str: str) -> dict[str, Any]:
        """
        Valida si la fecha y hora son válidas para agendar.

        Args:
            fecha_str: Fecha en formato YYYY-MM-DD
            hora_str: Hora en formato HH:MM AM/PM

        Returns:
            Dict con:
            - valid: bool
            - error: str (mensaje de error si no es válido)
        """
        # 1. Parsear fecha
        try:
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d")
        except ValueError:
            return {"valid": False, "error": f"Formato de fecha inválido. Usa el formato YYYY-MM-DD (ejemplo: 2026-01-25)."}

        # 2. Parsear hora
        hora = self._parse_time(hora_str)
        if not hora:
            return {"valid": False, "error": f"Formato de hora inválido. Usa el formato HH:MM AM/PM (ejemplo: 10:30 AM)."}

        # 3. Combinar fecha y hora
        fecha_hora_cita = fecha.replace(hour=hora.hour, minute=hora.minute)

        # 4. Validar que no sea en el pasado (zona horaria Lima, no la del servidor)
        ahora = datetime.now(_ZONA_PERU).replace(tzinfo=None)
        if fecha_hora_cita <= ahora:
            return {"valid": False, "error": "La fecha y hora seleccionada ya pasó. Por favor elige una fecha y hora futura."}

        # 5. Obtener horario de reuniones (cache compartida con horario_reuniones)
        schedule = await get_horario(self.id_empresa)
        if not schedule:
            logger.warning("[SCHEDULE] No se pudo obtener horario, permitiendo cita")
            return {"valid": True, "error": None}

        # 6. Obtener el día de la semana
        dia_semana = fecha.weekday()  # 0=Lunes, 6=Domingo
        campo_dia = DAY_MAPPING.get(dia_semana)
        horario_dia = schedule.get(campo_dia)
        dias_semana = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        nombre_dia = dias_semana[dia_semana]

        if not horario_dia:
            return {"valid": False, "error": f"No hay horario disponible para el día {nombre_dia}. Por favor elige otro día."}

        # 7. Verificar si el día está marcado como no disponible
        horario_dia_upper = horario_dia.strip().upper()
        if horario_dia_upper in ["NO DISPONIBLE", "CERRADO", "NO ATIENDE", "-", "N/A", ""]:
            return {"valid": False, "error": f"No hay atención el día {nombre_dia}. Por favor elige otro día."}

        # 8. Parsear el rango de horario del día
        rango = self._parse_time_range(horario_dia)
        if not rango:
            logger.warning("[SCHEDULE] No se pudo parsear horario del día: %s", horario_dia)
            return {"valid": True, "error": None}

        hora_inicio, hora_fin = rango
        horario_formateado = f"{hora_inicio.strftime('%I:%M %p')} a {hora_fin.strftime('%I:%M %p')}"

        # 9. Validar que la hora esté dentro del rango
        if hora.time() < hora_inicio.time():
            return {"valid": False, "error": f"La hora seleccionada es antes del horario de atención. El horario del {nombre_dia} es de {horario_formateado}."}

        if hora.time() >= hora_fin.time():
            return {"valid": False, "error": f"La hora seleccionada es después del horario de atención. El horario del {nombre_dia} es de {horario_formateado}."}

        # 10. Validar que la cita + duración no exceda la hora de cierre
        hora_fin_cita = fecha_hora_cita + self.duracion_cita
        hora_cierre = fecha.replace(hour=hora_fin.hour, minute=hora_fin.minute)

        if hora_fin_cita > hora_cierre:
            return {
                "valid": False,
                "error": f"La cita de {self.duracion_cita.seconds // 60} minutos excedería el horario de atención (cierre: {hora_fin.strftime('%I:%M %p')}). El horario del {nombre_dia} es de {horario_formateado}. Por favor elige una hora más temprana."
            }

        # 11. Validar horarios bloqueados
        horarios_bloqueados = schedule.get("horarios_bloqueados", "")
        if self._is_time_blocked(fecha, hora, horarios_bloqueados):
            return {"valid": False, "error": "El horario seleccionado está bloqueado. Por favor elige otra hora."}

        # 12. Verificar disponibilidad contra citas existentes
        availability = await self._check_availability(fecha_str, hora_str)
        if not availability["available"]:
            return {"valid": False, "error": availability["error"]}

        logger.debug("[VALIDATION] Horario válido: %s %s", fecha_str, hora_str)
        return {"valid": True, "error": None}

    async def recommendation(
        self,
        fecha_solicitada: str | None = None,
        hora_solicitada: str | None = None,
    ) -> dict[str, Any]:
        """
        Genera recomendaciones de horarios disponibles.
        Si el cliente dio fecha Y hora concretas, primero consulta CONSULTAR_DISPONIBILIDAD para ese slot.
        Si solo fecha (o hoy/mañana sin hora), usa SUGERIR_HORARIOS o horario del día.

        Args:
            fecha_solicitada: Fecha en YYYY-MM-DD que el cliente está consultando. Opcional.
            hora_solicitada: Hora en HH:MM AM/PM que el cliente indicó. Opcional.

        Returns:
            Dict con "text" y opcionalmente "recommendations", "total", "message"
        """
        now_peru = datetime.now(_ZONA_PERU)
        hoy_iso = now_peru.strftime("%Y-%m-%d")
        manana_iso = (now_peru + timedelta(days=1)).strftime("%Y-%m-%d")

        # Si el cliente indicó fecha Y hora concretas, consultar disponibilidad exacta primero
        if fecha_solicitada and hora_solicitada and hora_solicitada.strip():
            try:
                availability = await self._check_availability(fecha_solicitada.strip(), hora_solicitada.strip())
                if availability.get("available"):
                    return {
                        "text": f"El {fecha_solicitada} a las {hora_solicitada.strip()} está disponible. ¿Confirmamos la cita?"
                    }
                error_msg = availability.get("error") or "Ese horario no está disponible."
                return {
                    "text": f"{error_msg} ¿Te gustaría que te sugiera otros horarios?"
                }
            except Exception as e:
                logger.warning("[RECOMMENDATION] Error al consultar disponibilidad para slot concreto: %s", e)
                # Sigue con flujo normal (SUGERIR_HORARIOS)

        # Si el cliente preguntó por una fecha que NO es hoy ni mañana, no usar SUGERIR_HORARIOS
        if fecha_solicitada:
            try:
                fecha_obj = datetime.strptime(fecha_solicitada.strip(), "%Y-%m-%d")
                fecha_iso = fecha_obj.strftime("%Y-%m-%d")
                if fecha_iso != hoy_iso and fecha_iso != manana_iso:
                    return {"text": "Para esa fecha indica una hora que prefieras y la verifico."}
            except ValueError:
                pass

        # 1. Intentar SUGERIR_HORARIOS (hoy y mañana)
        payload = {
            "codOpe": "SUGERIR_HORARIOS",
            "id_empresa": self.id_empresa,
            "duracion_minutos": self.duracion_minutos,
            "slots": self.slots,
            "agendar_usuario": self.agendar_usuario,
            "agendar_sucursal": self.agendar_sucursal,
        }

        logger.debug("[RECOMMENDATION] JSON enviado a ws_agendar_reunion.php (SUGERIR_HORARIOS): %s", json.dumps(payload, ensure_ascii=False, indent=2))
        try:
            with track_api_call("sugerir_horarios"):
                data = await post_with_retry(app_config.API_AGENDAR_REUNION_URL, json=payload)

            if data.get("success"):
                sugerencias = data.get("sugerencias", [])
                mensaje = data.get("mensaje", "Horarios disponibles encontrados")
                total = data.get("total", 0)
                if sugerencias and total > 0:
                    sugerencias_texto = []
                    for i, sugerencia in enumerate(sugerencias, 1):
                        dia = sugerencia.get("dia", "")
                        hora_legible = sugerencia.get("hora_legible", "")
                        disponible = sugerencia.get("disponible", True)
                        fecha_inicio = sugerencia.get("fecha_inicio", "")
                        if dia and hora_legible:
                            if dia == "hoy":
                                texto = f"Hoy a las {hora_legible}"
                            elif dia == "mañana":
                                texto = f"Mañana a las {hora_legible}"
                            elif fecha_inicio:
                                try:
                                    fecha_obj = datetime.strptime(fecha_inicio, "%Y-%m-%d %H:%M:%S")
                                    dia_ingles = fecha_obj.strftime("%A")
                                    dia_nombre = DIAS_ESPANOL.get(dia_ingles, dia_ingles)
                                    texto = f"{dia_nombre} {fecha_obj.strftime('%d/%m')} a las {hora_legible}"
                                except ValueError:
                                    texto = f"{dia} a las {hora_legible}"
                            else:
                                texto = f"{dia} a las {hora_legible}"
                            if not disponible:
                                texto += " (ocupado)"
                            sugerencias_texto.append(f"{i}. {texto}")
                    if sugerencias_texto:
                        texto_final = f"{mensaje}\n\n" + "\n".join(sugerencias_texto) if mensaje else "Horarios sugeridos:\n\n" + "\n".join(sugerencias_texto)
                        return {
                            "text": texto_final,
                            "recommendations": sugerencias,
                            "total": total,
                            "message": mensaje,
                        }
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            logger.warning("[RECOMMENDATION] Error en SUGERIR_HORARIOS, usando fallback: %s", e)
        except Exception as e:
            logger.warning("[RECOMMENDATION] Error inesperado en SUGERIR_HORARIOS: %s", e)

        # 2. Fallback: sin llamar API
        return {"text": "No pude obtener sugerencias ahora. Indica una fecha y hora que prefieras y la verifico."}


__all__ = ["ScheduleValidator", "clear_horario_cache"]
