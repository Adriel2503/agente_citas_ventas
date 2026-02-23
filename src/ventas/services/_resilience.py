"""
Helper de resiliencia compartido: retry con backoff exponencial + circuit breaker.

Uso:
    _failures: TTLCache = TTLCache(maxsize=500, ttl=300)

    data = await resilient_call(
        lambda: post_informacion(payload),
        failures=_failures,
        circuit_key=id_empresa,
        service_name="MI_SERVICIO",
    )
    # Lanza RuntimeError si circuit abierto, o la última excepción si agotó reintentos.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable

from cachetools import TTLCache

logger = logging.getLogger(__name__)

FAILURE_THRESHOLD = 3


async def resilient_call(
    coro_factory: Callable[[], Awaitable[Any]],
    failures: TTLCache,
    circuit_key: Any,
    service_name: str,
    max_retries: int = 2,
    backoff_base: float = 1.0,
) -> Any:
    """
    Ejecuta coro_factory() con retry exponencial y circuit breaker.

    - Circuit breaker abierto  → RuntimeError inmediato, sin tocar la red.
    - Retry                    → hasta max_retries intentos con espera
                                 backoff_base * 2^attempt segundos entre ellos.
    - Éxito                    → resetea el contador de fallos.
    - Fallo total              → incrementa el contador; si alcanza
                                 FAILURE_THRESHOLD, loguea apertura del circuito.

    Args:
        coro_factory:  Callable sin argumentos que retorna una coroutine.
        failures:      TTLCache compartido por todos los callers del mismo servicio.
                       Key = circuit_key, value = int (contador de fallos).
        circuit_key:   Clave de partición del circuit breaker (ej: id_empresa).
        service_name:  Nombre para logs (ej: "CATEGORIAS").
        max_retries:   Número máximo de intentos (default 2).
        backoff_base:  Base del backoff exponencial en segundos (default 1.0).

    Raises:
        RuntimeError: si el circuit breaker está abierto.
        Exception:    la última excepción si todos los reintentos fallaron.
    """
    if failures.get(circuit_key, 0) >= FAILURE_THRESHOLD:
        logger.warning(
            "[%s] Circuit ABIERTO key=%s — llamada rechazada sin tocar la red",
            service_name, circuit_key,
        )
        raise RuntimeError(
            f"[{service_name}] Circuit breaker abierto para key={circuit_key}"
        )

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            result = await coro_factory()
            # Éxito: resetear circuit breaker
            failures.pop(circuit_key, None)
            return result
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "[%s] Error intento %d/%d key=%s: %s: %s",
                service_name, attempt + 1, max_retries, circuit_key,
                type(exc).__name__, exc,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(backoff_base * (2 ** attempt))

    # Todos los intentos fallaron → actualizar circuit breaker
    current = failures.get(circuit_key, 0) + 1
    failures[circuit_key] = current
    if current >= FAILURE_THRESHOLD:
        logger.warning(
            "[%s] Circuit breaker ABIERTO key=%s (fallos acumulados=%d/%d)",
            service_name, circuit_key, current, FAILURE_THRESHOLD,
        )

    raise last_exc  # type: ignore[misc]


__all__ = ["resilient_call", "FAILURE_THRESHOLD"]
