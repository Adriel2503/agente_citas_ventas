# Agente Citas Ventas — MaravIA

Agente especializado en **citas y ventas directas** diseñado para operar dentro del ecosistema de **MaravIA**. El API gateway lo consume directamente (sin orquestador) y maneja dos flujos: agendamiento de citas/reuniones y venta directa completa (búsqueda de productos, selección, entrega, pago y confirmación).

## Tabla de contenidos

- [Descripción general](#descripción-general)
- [Arquitectura](#arquitectura)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Requisitos](#requisitos)
- [Configuración](#configuración)
- [Ejecución](#ejecución)
  - [Local](#local)
  - [Docker](#docker)
- [API HTTP expuesta](#api-http-expuesta)
- [Herramientas del agente](#herramientas-del-agente)
- [Servicios externos](#servicios-externos)
- [Resiliencia](#resiliencia)
- [Características destacadas](#características-destacadas)

---

## Descripción general

El Agente Citas Ventas es un microservicio HTTP que expone `POST /api/chat` al API gateway. Internamente utiliza:

- **LangChain + LangGraph** para la lógica del agente con memoria de sesión
- **OpenAI** (GPT-4o-mini por defecto) como LLM principal
- **FastAPI + uvicorn** como servidor HTTP
- **Jinja2** para la generación dinámica del system prompt
- **API MaravIA** para categorías, sucursales, métodos de pago, búsqueda de productos, horarios y agendamiento de citas

---

## Arquitectura

```
API Gateway
    │
    │  POST /api/chat { message, session_id, context }
    ▼
┌──────────────────────────────────────────────────────────┐
│  FastAPI Service  (puerto 8004)                          │
│                                                          │
│  process_venta_message()                                 │
│    ├─ Validar contexto (id_empresa)                      │
│    ├─ Build system prompt (Jinja2)                       │
│    │    ├─ obtener_categorias()         ──► API MaravIA  │
│    │    ├─ obtener_sucursales()         ──► API MaravIA  │
│    │    ├─ obtener_metodos_pago()       ──► API MaravIA  │
│    │    ├─ fetch_contexto_negocio()     ──► API MaravIA  │
│    │    ├─ fetch_preguntas_frecuentes() ──► API MaravIA  │
│    │    ├─ obtener_costos_envio()       ──► API MaravIA  │
│    │    └─ fetch_horario_reuniones()    ──► API MaravIA  │
│    │                                                     │
│    └─ LangChain Agent                                    │
│         ├─ OpenAI Chat Model                             │
│         ├─ InMemorySaver (sesión)                        │
│         └─ Tools:                                        │
│              ├─ search_productos_servicios  ──► API      │
│              ├─ registrar_pedido            ──► API      │
│              ├─ check_availability          ──► API      │
│              └─ create_booking              ──► API      │
└──────────────────────────────────────────────────────────┘
    │
    │  { reply, url: null }
    ▼
API Gateway
```

---

## Estructura del proyecto

```
agent_citas_ventas/
├── src/citas_ventas/
│   ├── main.py                         # Servidor FastAPI, POST /api/chat
│   ├── logger.py                       # Configuración de logging
│   ├── metrics.py                      # Métricas Prometheus
│   ├── validation.py                   # Validación de datos de booking
│   ├── agent/
│   │   └── agent.py                    # Lógica del agente LangChain/LangGraph
│   ├── config/
│   │   ├── __init__.py                 # Re-export de variables de configuración
│   │   └── config.py                   # Variables de entorno y validación
│   ├── tool/
│   │   └── tools.py                    # Herramientas del agente (4 tools)
│   ├── prompts/
│   │   ├── __init__.py                 # build_ventas_system_prompt()
│   │   └── ventas_system.j2            # Template Jinja2 del system prompt
│   └── services/
│       ├── http_client.py              # Cliente HTTP compartido (httpx + tenacity)
│       ├── _resilience.py              # Wrapper de circuit breaker (resilient_call)
│       ├── circuit_breaker.py          # Circuit breakers por servicio
│       ├── busqueda_productos.py       # Búsqueda de productos (cache + anti-thundering herd)
│       ├── categorias.py               # Categorías del catálogo
│       ├── contexto_negocio.py         # Contexto del negocio
│       ├── costo_envio.py              # Costos de envío por zona
│       ├── metodos_pago.py             # Métodos de pago (bancos, Yape, Plin)
│       ├── preguntas_frecuentes.py     # FAQs por id_chatbot
│       ├── sucursales.py               # Sucursales y horarios
│       ├── registrar_pedido.py         # Registro de pedidos (escritura, sin retry)
│       ├── horario_cache.py            # Cache de horarios de reuniones
│       ├── horario_reuniones.py        # Horario para el system prompt
│       ├── schedule_validator.py       # Validación de disponibilidad de citas
│       └── booking.py                  # Creación de eventos en calendario
├── run.py                              # Script de entrada local
├── requirements.txt                    # Dependencias Python
├── Dockerfile                          # Imagen Python 3.12 slim
├── compose.yaml                        # Docker Compose
├── .env.example                        # Plantilla de configuración
└── .gitignore
```

---

## Requisitos

- Python **3.12+**
- Cuenta de **OpenAI** con API Key
- Acceso a la **API MaravIA** (`ws_informacion_ia.php`, `ws_preguntas_frecuentes.php`, `ws_agendar_reunion.php`, `ws_calendario.php`)
- Docker (opcional, para despliegue en contenedor)

---

## Configuración

Copia `.env.example` a `.env` y completa los valores:

```bash
cp .env.example .env
```

### OpenAI y servidor

| Variable | Default | Descripción |
|---|---|---|
| `OPENAI_API_KEY` | *(requerido)* | Clave de API de OpenAI |
| `OPENAI_MODEL` | `gpt-4o-mini` | Modelo de OpenAI a usar |
| `OPENAI_TEMPERATURE` | `0.5` | Temperatura del modelo (0.0–2.0) |
| `OPENAI_TIMEOUT` | `60` | Timeout por llamada a OpenAI (segundos) |
| `MAX_TOKENS` | `2048` | Máximo de tokens por respuesta |
| `SERVER_HOST` | `0.0.0.0` | Host del servidor |
| `SERVER_PORT` | `8004` | Puerto del servidor |
| `LOG_LEVEL` | `INFO` | Nivel de logging (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FILE` | *(vacío)* | Ruta de archivo de log (vacío = solo consola) |

### Timeouts y retry

| Variable | Default | Descripción |
|---|---|---|
| `API_TIMEOUT` | `10` | Timeout por request HTTP a la API (segundos) |
| `CHAT_TIMEOUT` | `120` | Timeout global por mensaje (debe ser >= OPENAI_TIMEOUT) |
| `HTTP_RETRY_ATTEMPTS` | `3` | Intentos HTTP con tenacity (1 = sin retry) |
| `HTTP_RETRY_WAIT_MIN` | `1` | Backoff exponencial mínimo (segundos) |
| `HTTP_RETRY_WAIT_MAX` | `4` | Backoff exponencial máximo (segundos) |

### APIs MaravIA

| Variable | Default | Descripción |
|---|---|---|
| `API_INFORMACION_URL` | `https://api.maravia.pe/.../ws_informacion_ia.php` | Productos, categorías, sucursales, contexto, etc. |
| `API_PREGUNTAS_FRECUENTES_URL` | `https://api.maravia.pe/.../ws_preguntas_frecuentes.php` | FAQs por chatbot |
| `API_CALENDAR_URL` | `https://api.maravia.pe/.../ws_calendario.php` | Creación de eventos (CREAR_EVENTO) |
| `API_AGENDAR_REUNION_URL` | `https://api.maravia.pe/.../ws_agendar_reunion.php` | Validación de horarios y disponibilidad |

### Agendamiento de citas

| Variable | Default | Descripción |
|---|---|---|
| `SCHEDULE_CACHE_TTL_MINUTES` | `5` | Minutos de cache de horarios disponibles (1–60) |
| `TIMEZONE` | `America/Lima` | Zona horaria para validación de fechas |

### Cache del agente

| Variable | Default | Descripción |
|---|---|---|
| `AGENT_CACHE_TTL_MINUTES` | `60` | Minutos que vive el agente en cache por empresa (5–1440) |
| `AGENT_CACHE_MAXSIZE` | `500` | Número máximo de empresas en cache simultáneamente |

> **Nota:** `CHAT_TIMEOUT` debe ser mayor o igual que `OPENAI_TIMEOUT` para evitar cancelaciones prematuras.

---

## Ejecución

### Local

```bash
# 1. Crear y activar entorno virtual
python -m venv venv
source venv/bin/activate        # Linux/macOS
venv\Scripts\activate           # Windows

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno
cp .env.example .env
# Editar .env con tu OPENAI_API_KEY

# 4. Iniciar el servidor
python run.py
```

El servidor arrancará en `http://0.0.0.0:8004` y registrará en consola:

```
INICIANDO SERVICIO CITAS VENTAS - MaravIA
Host: 0.0.0.0:8004
Modelo: gpt-4o-mini
Endpoint: POST /api/chat
Health:   GET  /health
Metrics:  GET  /metrics
Tools: search_productos_servicios, registrar_pedido, check_availability, create_booking
```

### Docker

```bash
# Construir y levantar el contenedor
docker compose up --build

# En segundo plano
docker compose up -d
```

La imagen usa Python 3.12 slim con usuario no-root y `PYTHONPATH` configurado.

---

## API HTTP expuesta

### `POST /api/chat`

Endpoint principal consumido por el API gateway.

**Request body:**

```json
{
  "message": "Quiero agendar una cita para el lunes",
  "session_id": 1234,
  "context": {
    "config": {
      "id_empresa": 123,
      "id_chatbot": 7,
      "nombre_bot": "Valeria",
      "personalidad": "amable y profesional",
      "nombre_negocio": "Mi Tienda",
      "propuesta_valor": "...",
      "duracion_cita_minutos": 30,
      "slots": 30,
      "agendar_usuario": 5,
      "agendar_sucursal": 1
    }
  }
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `message` | `str` | Mensaje del usuario (puede incluir URLs de imágenes) |
| `session_id` | `int` | ID de sesión (mantiene contexto de conversación) |
| `context.config.id_empresa` | `int` | **Requerido.** ID de la empresa |
| `context.config.id_chatbot` | `int` | Opcional. Para cargar FAQs del chatbot |
| `context.config.nombre_bot` | `str` | Opcional. Nombre del asistente virtual |
| `context.config.personalidad` | `str` | Opcional. Instrucciones de personalidad |
| `context.config.nombre_negocio` | `str` | Opcional. Nombre del negocio |
| `context.config.propuesta_valor` | `str` | Opcional. Propuesta de valor del negocio |
| `context.config.duracion_cita_minutos` | `int` | Opcional. Duración de cada cita en minutos (default: 60) |
| `context.config.slots` | `int` | Opcional. Intervalo entre slots en minutos (default: 60) |
| `context.config.agendar_usuario` | `int` | Opcional. ID del usuario del calendario |
| `context.config.agendar_sucursal` | `int` | Opcional. ID de la sucursal para agendar |

**Response:**

```json
{
  "reply": "¡Tu cita quedó confirmada para el lunes 27 a las 2:00 PM!",
  "url": null
}
```

### `GET /health`

```json
{ "status": "ok", "agent": "citas_ventas", "version": "2.0.0", "issues": [] }
```

Retorna `503` con `"status": "degraded"` si algún circuit breaker está abierto o falta la API key.

### `GET /metrics`

Métricas en formato Prometheus (HTTP requests, duración, tool calls, cache hits/misses, etc.).

---

## Herramientas del agente

El agente cuenta con 4 herramientas (tools) que el LLM invoca según el contexto de la conversación:

### `search_productos_servicios`

Busca productos y servicios en el catálogo de la empresa.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `busqueda` | `str` | Término de búsqueda (ej: "laptop", "consulta") |

Retorna lista formateada con nombre, precio, categoría, descripción e ID de cada producto.

### `registrar_pedido`

Registra el pedido confirmado del cliente en el sistema. Se llama una sola vez cuando se tienen todos los datos: productos con ID, número de operación del comprobante, datos del cliente (nombre, DNI, celular), modalidad de entrega y monto pagado.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `productos` | `list[dict]` | Lista de `{"id_catalogo": int, "cantidad": int}` |
| `operacion` | `str` | Número de operación del comprobante |
| `modalidad` | `str` | `"Delivery"` o `"Recojo"` |
| `nombre`, `dni`, `celular` | `str` | Datos del cliente (obligatorios) |
| `medio_pago` | `str` | Medio de pago (ej: "yape") |
| `monto_pagado` | `float` | Monto total pagado |
| ... | | Otros campos opcionales: `direccion`, `costo_envio`, `sucursal`, `email`, `observacion`, `fecha_entrega_estimada`, `tipo_envio` |

### `check_availability`

Consulta horarios disponibles para una cita en una fecha dada.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `date` | `str` | Fecha en formato ISO `YYYY-MM-DD` |
| `time` | `str \| None` | Hora opcional (ej: `"2:00 PM"`). Si se pasa, verifica ese slot exacto; si no, sugiere horarios |

### `create_booking`

Crea una cita/evento en el calendario con validación completa.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `date` | `str` | Fecha `YYYY-MM-DD` |
| `time` | `str` | Hora (ej: `"02:00 PM"`) |
| `customer_name` | `str` | Nombre completo del cliente |
| `customer_contact` | `str` | Email del cliente |

Flujo interno: valida datos → verifica disponibilidad con `ScheduleValidator` → crea evento en `ws_calendario.php` (CREAR_EVENTO). Puede retornar enlace de Google Meet si el calendario está sincronizado.

---

## Servicios externos

### API `ws_informacion_ia.php`

Endpoint compartido para múltiples operaciones, diferenciadas por `codOpe`:

| Servicio | `codOpe` | Cache | Descripción |
|---|---|---|---|
| `obtener_categorias()` | `OBTENER_CATEGORIAS` | TTL 1h | Categorías del catálogo |
| `obtener_sucursales()` | `OBTENER_SUCURSALES_PUBLICAS` | TTL 1h | Sucursales con dirección y horarios |
| `obtener_metodos_pago()` | `OBTENER_METODOS_PAGO` | TTL 1h | Bancos y billeteras digitales |
| `fetch_contexto_negocio()` | `OBTENER_CONTEXTO_NEGOCIO` | TTL 1h | Contexto libre del negocio |
| `obtener_costos_envio()` | `OBTENER_COSTOS_ENVIO` | TTL 1h | Zonas, costos y plazos de envío |
| `buscar_productos_servicios()` | `BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS` | TTL 15min | Búsqueda en catálogo |
| `get_horario()` | `OBTENER_HORARIO_REUNIONES` | TTL 5min | Horarios de atención para citas |

### API `ws_preguntas_frecuentes.php`

| Servicio | Cache | Descripción |
|---|---|---|
| `fetch_preguntas_frecuentes()` | TTL 1h | FAQs por `id_chatbot` |

### API `ws_agendar_reunion.php`

| Servicio | Operación | Descripción |
|---|---|---|
| `ScheduleValidator.validate()` | `CONSULTAR_DISPONIBILIDAD` | Verifica si un slot está libre |
| `ScheduleValidator.recommendation()` | `SUGERIR_HORARIOS` | Sugiere horarios disponibles |

### API `ws_calendario.php`

| Servicio | Operación | Descripción |
|---|---|---|
| `confirm_booking()` | `CREAR_EVENTO` | Crea evento en calendario (Google Calendar sync opcional) |

### API `ws_informacion_ia.php` (escritura)

| Servicio | `codOpe` | Descripción |
|---|---|---|
| `registrar_pedido()` | `REGISTRAR_PEDIDO` | Registra pedido confirmado (sin retry, operación de escritura) |

---

## Resiliencia

El sistema implementa múltiples capas de protección:

### Cliente HTTP compartido
Un único `httpx.AsyncClient` (lazy-init) reutilizado por todos los servicios. Se cierra limpiamente en el lifespan del servidor. Connection pool: 50 conexiones, 20 keep-alive.

### Retry automático (tenacity)
`post_with_retry`: reintenta solo `httpx.TransportError` (timeouts, errores de conexión) con backoff exponencial. No reintenta errores HTTP 4xx/5xx. Configurable via `HTTP_RETRY_ATTEMPTS`, `HTTP_RETRY_WAIT_MIN`, `HTTP_RETRY_WAIT_MAX`.

### Circuit breakers
Tres circuit breakers independientes (`circuit_breaker.py`):

| Circuit Breaker | Servicios protegidos | Comportamiento |
|---|---|---|
| `informacion_cb` | categorías, sucursales, contexto, metodos_pago, costos_envio, búsqueda, horarios | 3 fallos → abierto 5 min |
| `preguntas_cb` | preguntas frecuentes | 3 fallos → abierto 5 min |
| `calendario_cb` | booking (crear evento) | 3 fallos → abierto 5 min |

Particionados por `id_empresa`: si la API falla para una empresa, las demás no se ven afectadas.

### Cache TTL
Todos los servicios de lectura usan `cachetools.TTLCache` para evitar llamadas repetidas. TTL de 1 hora para datos estables (categorías, sucursales), 15 minutos para búsquedas, 5 minutos para horarios.

### Anti-thundering herd
Los servicios con cache usan `asyncio.Lock` por clave (id_empresa + término) con double-check post-lock. Si N requests concurrentes causan un cache miss, solo la primera llama a la API; las demás esperan el resultado.

### Degradación graceful
Si un servicio falla (API caída, circuit breaker abierto), el agente sigue funcionando con la información que sí pudo obtener. El system prompt se construye con lo disponible.

---

## Características destacadas

### Visión multimodal
El agente detecta automáticamente URLs de imágenes en los mensajes del usuario (jpg, jpeg, png, gif, webp) y las envía al modelo como bloques de visión de OpenAI. Permite validar comprobantes de pago enviados como capturas.

### System prompt dinámico con Jinja2
El prompt se genera en cada sesión con datos reales: categorías, sucursales, horarios, métodos de pago, FAQs, contexto del negocio, costos de envío y horarios de citas. Template: `prompts/ventas_system.j2`.

### Memoria de sesión
Usa `InMemorySaver` de LangGraph para mantener el historial de conversación. El `thread_id` se deriva del `session_id` del gateway.

### Gestión de timeouts en capas
- `API_TIMEOUT`: límite por cada request HTTP individual
- `OPENAI_TIMEOUT`: límite por llamada al LLM
- `CHAT_TIMEOUT`: límite global por mensaje completo (incluye tool calls)

### Métricas Prometheus
Expone en `GET /metrics`: requests HTTP (count, duration), tool calls (por herramienta y status), cache hits/misses, información del agente (modelo, versión).

### Logging estructurado
Prefijos por módulo (`[HTTP]`, `[AGENT]`, `[TOOL]`, `[API]`, `[CONTEXTO_NEGOCIO]`, `[BUSQUEDA]`, etc.) para rastreo en producción. Nivel y destino configurables.
