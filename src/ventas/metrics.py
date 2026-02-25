"""
Métricas y observabilidad para el servicio de ventas.
Expone contadores, histogramas e info estática para Prometheus.
/metrics montado en main.py.
"""

import time
from contextlib import contextmanager

from prometheus_client import Counter, Histogram, Info

# ---------------------------------------------------------------------------
# Info estática (versión, modelo)
# ---------------------------------------------------------------------------

agent_info = Info(
    "agent_ventas_info",
    "Información del servicio de ventas",
)


def initialize_agent_info(model: str, version: str = "1.0.0") -> None:
    """Inicializa la información estática del agente para métricas."""
    agent_info.info({
        "version": version,
        "model": model,
        "agent_type": "ventas",
    })


# ---------------------------------------------------------------------------
# Capa HTTP (/api/chat)
# ---------------------------------------------------------------------------

HTTP_REQUESTS = Counter(
    "ventas_http_requests_total",
    "Total de requests al endpoint /api/chat por resultado",
    ["status"],  # success | timeout | error
)

HTTP_DURATION = Histogram(
    "ventas_http_duration_seconds",
    "Latencia total del endpoint /api/chat (incluye LLM y tools)",
    buckets=[0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 90, 120],
)

# ---------------------------------------------------------------------------
# Capa LLM (agent.ainvoke — turno completo incluye tool calls internos)
# ---------------------------------------------------------------------------

LLM_REQUESTS = Counter(
    "ventas_llm_requests_total",
    "Total de invocaciones al agente LLM por resultado",
    ["status"],  # success | error
)

LLM_DURATION = Histogram(
    "ventas_llm_duration_seconds",
    "Latencia de agent.ainvoke (LLM + tool calls dentro de LangGraph)",
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60, 90],
)

# ---------------------------------------------------------------------------
# Cache del agente (por empresa)
# ---------------------------------------------------------------------------

AGENT_CACHE = Counter(
    "ventas_agent_cache_total",
    "Hits y misses del cache de agente por empresa",
    ["result"],  # hit | miss
)

# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------

TOOL_CALLS = Counter(
    "ventas_tool_calls_total",
    "Invocaciones de tools del agente por herramienta y resultado",
    ["tool", "status"],  # tool: nombre de la tool; status: ok | error
)

# ---------------------------------------------------------------------------
# Cache de búsqueda de productos
# ---------------------------------------------------------------------------

SEARCH_CACHE = Counter(
    "ventas_search_cache_total",
    "Resultados del cache de búsqueda de productos",
    ["result"],  # hit | miss | circuit_open
)


# ---------------------------------------------------------------------------
# Por empresa (como agent_citas)
# ---------------------------------------------------------------------------

chat_requests_total = Counter(
    "ventas_chat_requests_total",
    "Total de requests de chat por empresa",
    ["empresa_id"],
)

chat_errors_total = Counter(
    "ventas_chat_errors_total",
    "Total de errores de chat por tipo",
    ["error_type"],
)

# ---------------------------------------------------------------------------
# API calls por endpoint
# ---------------------------------------------------------------------------

API_CALLS = Counter(
    "ventas_api_calls_total",
    "Total de llamadas a APIs externas por endpoint y estado",
    ["endpoint", "status"],
)

api_call_duration = Histogram(
    "ventas_api_call_duration_seconds",
    "Latencia de llamadas a APIs externas por endpoint",
    ["endpoint"],
    buckets=[0.1, 0.25, 0.5, 1, 2.5, 5, 10],
)


# ---------------------------------------------------------------------------
# Context managers (como agent_citas)
# ---------------------------------------------------------------------------

@contextmanager
def track_chat_response():
    """Context manager para medir la duración de una respuesta LLM completa."""
    status = "success"
    start = time.perf_counter()
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        LLM_REQUESTS.labels(status=status).inc()
        LLM_DURATION.observe(time.perf_counter() - start)


@contextmanager
def track_tool_execution(tool_name: str):
    """Context manager para medir la duración de ejecución de una tool."""
    status = "ok"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        TOOL_CALLS.labels(tool=tool_name, status=status).inc()


@contextmanager
def track_api_call(endpoint: str):
    """Context manager para medir la duración de una llamada a API externa."""
    status = "ok"
    start = time.perf_counter()
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        API_CALLS.labels(endpoint=endpoint, status=status).inc()
        api_call_duration.labels(endpoint=endpoint).observe(time.perf_counter() - start)


def record_chat_error(error_type: str) -> None:
    """Registra un error de chat por tipo."""
    chat_errors_total.labels(error_type=error_type).inc()


__all__ = [
    "initialize_agent_info",
    "agent_info",
    "HTTP_REQUESTS",
    "HTTP_DURATION",
    "LLM_REQUESTS",
    "LLM_DURATION",
    "AGENT_CACHE",
    "TOOL_CALLS",
    "SEARCH_CACHE",
    "chat_requests_total",
    "chat_errors_total",
    "API_CALLS",
    "api_call_duration",
    "track_chat_response",
    "track_tool_execution",
    "track_api_call",
    "record_chat_error",
]
