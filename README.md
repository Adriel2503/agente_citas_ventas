# Agente Citas Ventas — MaravIA

Agente especializado en **citas y ventas directas** diseñado para operar dentro del ecosistema de **MaravIA**. El API gateway lo consume directamente (sin orquestador) y maneja dos flujos: agendamiento de citas/reuniones y venta directa completa (búsqueda de productos, selección, entrega, pago y confirmación).

## Tabla de contenidos

- [Descripción general](#descripción-general)
- [Arquitectura](#arquitectura)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Requisitos](#requisitos)
- [Configuración](#configuración)
- [Ejecución](#ejecución)
- [API HTTP expuesta](#api-http-expuesta)
- [Herramientas del agente (Tools)](#herramientas-del-agente-tools)
  - [search_productos_servicios](#1-search_productos_servicios)
  - [registrar_pedido](#2-registrar_pedido)
  - [check_availability](#3-check_availability)
  - [create_booking](#4-create_booking)
- [Capa de servicios](#capa-de-servicios)
  - [Infraestructura HTTP](#infraestructura-http)
  - [Servicios de lectura (system prompt)](#servicios-de-lectura-system-prompt)
  - [Servicios de citas](#servicios-de-citas)
  - [Contratos de API externos](#contratos-de-api-externos)
- [Capa del agente](#capa-del-agente)
- [System prompt dinámico](#system-prompt-dinámico)
- [Validación](#validación)
- [Resiliencia](#resiliencia)
- [Concurrencia y locks](#concurrencia-y-locks)
- [Observabilidad](#observabilidad)

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
┌──────────────────────────────────────────────────────────────────┐
│  FastAPI Service  (puerto 8004)                                  │
│                                                                  │
│  main.py → process_venta_message()                               │
│    ├─ Validar contexto (id_empresa obligatorio)                  │
│    ├─ Obtener/crear agente (cache por id_empresa)                │
│    │    └─ Build system prompt (Jinja2, 7 fetches en paralelo)   │
│    │         ├─ obtener_categorias()         ──► ws_informacion  │
│    │         ├─ obtener_sucursales()         ──► ws_informacion  │
│    │         ├─ obtener_metodos_pago()       ──► ws_informacion  │
│    │         ├─ fetch_contexto_negocio()     ──► ws_informacion  │
│    │         ├─ fetch_preguntas_frecuentes() ──► ws_preguntas    │
│    │         ├─ obtener_costos_envio()       ──► ws_informacion  │
│    │         └─ fetch_horario_reuniones()    ──► ws_informacion  │
│    │                                                             │
│    └─ agent.ainvoke(message, thread_id=session_id)               │
│         │                                                        │
│         │  El LLM decide qué tools invocar                       │
│         ▼                                                        │
│    ┌─ Tools ───────────────────────────────────────────────┐     │
│    │  search_productos_servicios ──► ws_informacion        │     │
│    │  registrar_pedido           ──► ws_informacion (WRITE)│     │
│    │  check_availability         ──► ws_agendar_reunion    │     │
│    │  create_booking             ──► ws_calendario         │     │
│    └───────────────────────────────────────────────────────┘     │
│                                                                  │
│  Capas de resiliencia (por request):                             │
│    post_with_logging → post_with_retry (tenacity) → httpx POST   │
│    resilient_call (circuit breaker) ─┘                           │
└──────────────────────────────────────────────────────────────────┘
    │
    │  { reply: "...", url: null }
    ▼
API Gateway
```

---

## Estructura del proyecto

```
agent_citas_ventas/
├── src/citas_ventas/
│   ├── main.py                         # Servidor FastAPI, POST /api/chat, GET /health, GET /metrics
│   ├── logger.py                       # Logging estructurado con prefijos por módulo
│   ├── metrics.py                      # Métricas Prometheus (requests, tools, cache, booking)
│   ├── validation.py                   # Validación de datos (Pydantic: fechas, nombres, contacto)
│   ├── agent/
│   │   └── agent.py                    # Construcción, cache y ejecución del agente LangGraph
│   ├── config/
│   │   ├── __init__.py                 # Re-export de todas las variables de configuración
│   │   └── config.py                   # Lectura de env vars con validación de tipos y rangos
│   ├── tool/
│   │   ├── __init__.py                 # Exports: search, registrar, check, create + AGENT_TOOLS
│   │   └── tools.py                    # 4 herramientas del agente (funciones async decoradas)
│   ├── prompts/
│   │   ├── __init__.py                 # build_ventas_system_prompt() — fetches paralelos + Jinja2
│   │   └── citas_ventas_system.j2      # Template del system prompt (flujo ventas + citas)
│   └── services/
│       ├── http_client.py              # httpx.AsyncClient singleton + post_with_retry + post_with_logging
│       ├── _resilience.py              # resilient_call() — wrapper de circuit breaker
│       ├── circuit_breaker.py          # 4 CBs: informacion, preguntas, calendario, agendar_reunion
│       ├── busqueda_productos.py       # Búsqueda de productos (cache 15min + anti-thundering herd)
│       ├── categorias.py               # Categorías del catálogo (cache 1h)
│       ├── contexto_negocio.py         # Contexto libre del negocio (cache 1h)
│       ├── costo_envio.py              # Costos de envío por zona (cache 1h)
│       ├── metodos_pago.py             # Métodos de pago: bancos + billeteras (cache 1h)
│       ├── preguntas_frecuentes.py     # FAQs por id_chatbot (cache 1h)
│       ├── sucursales.py               # Sucursales con dirección y horarios (cache 1h)
│       ├── registrar_pedido.py         # Registro de pedidos (WRITE, sin retry, sin CB)
│       ├── horario_cache.py            # Cache compartido de horarios de reuniones (cache 5min)
│       ├── horario_reuniones.py        # Formatea horarios para el system prompt
│       ├── schedule_validator.py       # ScheduleValidator: validación completa de disponibilidad
│       └── booking.py                  # confirm_booking(): creación de evento en calendario
├── run.py                              # Script de entrada local
├── requirements.txt                    # Dependencias Python
├── Dockerfile                          # Imagen Python 3.12 slim, usuario no-root
├── compose.yaml                        # Docker Compose
├── .env.example                        # Plantilla de configuración (todas las variables)
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

| Variable | Default | Rango | Descripción |
|---|---|---|---|
| `OPENAI_API_KEY` | *(requerido)* | — | Clave de API de OpenAI |
| `OPENAI_MODEL` | `gpt-4o-mini` | — | Modelo de OpenAI a usar |
| `OPENAI_TEMPERATURE` | `0.5` | 0.0–2.0 | Temperatura del modelo |
| `OPENAI_TIMEOUT` | `60` | 1–300 | Timeout por llamada a OpenAI (segundos) |
| `MAX_TOKENS` | `2048` | 1–128000 | Máximo de tokens por respuesta |
| `SERVER_HOST` | `0.0.0.0` | — | Host del servidor |
| `SERVER_PORT` | `8004` | 1–65535 | Puerto del servidor |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL | Nivel de logging |
| `LOG_FILE` | *(vacío)* | — | Ruta de archivo de log (vacío = solo consola) |

### Timeouts y retry

| Variable | Default | Rango | Descripción |
|---|---|---|---|
| `API_TIMEOUT` | `10` | 1–120 | Timeout por request HTTP a la API (segundos) |
| `CHAT_TIMEOUT` | `120` | 30–300 | Timeout global por mensaje (debe ser >= OPENAI_TIMEOUT) |
| `HTTP_RETRY_ATTEMPTS` | `3` | 1–10 | Intentos HTTP con tenacity (1 = sin retry) |
| `HTTP_RETRY_WAIT_MIN` | `1` | 0–30 | Backoff exponencial mínimo (segundos) |
| `HTTP_RETRY_WAIT_MAX` | `4` | 1–60 | Backoff exponencial máximo (segundos) |

### Circuit Breaker

| Variable | Default | Rango | Descripción |
|---|---|---|---|
| `CB_THRESHOLD` | `3` | 1–20 | Fallos consecutivos (TransportError) para abrir el circuito |
| `CB_RESET_TTL` | `300` | 60–3600 | Segundos hasta auto-reset del circuito |

### APIs MaravIA

| Variable | Default | Descripción |
|---|---|---|
| `API_INFORMACION_URL` | `https://api.maravia.pe/.../ws_informacion_ia.php` | Productos, categorías, sucursales, contexto, etc. |
| `API_PREGUNTAS_FRECUENTES_URL` | `https://api.maravia.pe/.../ws_preguntas_frecuentes.php` | FAQs por chatbot |
| `API_CALENDAR_URL` | `https://api.maravia.pe/.../ws_calendario.php` | Creación de eventos (CREAR_EVENTO) |
| `API_AGENDAR_REUNION_URL` | `https://api.maravia.pe/.../ws_agendar_reunion.php` | Validación de horarios y disponibilidad |

### Agendamiento y cache

| Variable | Default | Rango | Descripción |
|---|---|---|---|
| `SCHEDULE_CACHE_TTL_MINUTES` | `5` | 1–60 | Minutos de cache de horarios disponibles |
| `TIMEZONE` | `America/Lima` | — | Zona horaria para validación de fechas |
| `AGENT_CACHE_TTL_MINUTES` | `60` | 5–1440 | Minutos que vive el agente en cache por empresa |
| `AGENT_CACHE_MAXSIZE` | `500` | 10–5000 | Número máximo de empresas en cache simultáneamente |

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
      "propuesta_valor": "Los mejores productos al mejor precio",
      "duracion_cita_minutos": 30,
      "slots": 30,
      "agendar_usuario": 5,
      "agendar_sucursal": 1,
      "id_prospecto": 999,
      "usuario_id": 5,
      "correo_usuario": "vendedor@mitienda.com",
      "archivo_saludo": "https://cdn.example.com/saludo.mp4"
    }
  }
}
```

| Campo | Tipo | Origen | Descripción |
|---|---|---|---|
| `message` | `str` (1–4096) | Usuario | Mensaje del usuario (puede incluir URLs de imágenes) |
| `session_id` | `int` | Gateway | ID de sesión (mantiene contexto de conversación) |
| `context.config.id_empresa` | `int` | Gateway | **Requerido.** ID de la empresa |
| `context.config.id_chatbot` | `int` | Gateway | Para cargar FAQs del chatbot |
| `context.config.nombre_bot` | `str` | Gateway | Nombre del asistente virtual |
| `context.config.personalidad` | `str` | Gateway | Instrucciones de personalidad para el prompt |
| `context.config.nombre_negocio` | `str` | Gateway | Nombre del negocio |
| `context.config.propuesta_valor` | `str` | Gateway | Propuesta de valor del negocio |
| `context.config.duracion_cita_minutos` | `int` | Gateway | Duración de cada cita en minutos (default: 60) |
| `context.config.slots` | `int` | Gateway | Intervalo entre slots en minutos (default: 60) |
| `context.config.agendar_usuario` | `int` | Gateway | ID del usuario del calendario (default: 1) |
| `context.config.agendar_sucursal` | `int` | Gateway | ID de la sucursal para agendar (default: 0) |
| `context.config.id_prospecto` | `int` | Gateway | ID del prospecto/cliente |
| `context.config.usuario_id` | `int` | Gateway | ID del usuario vendedor |
| `context.config.correo_usuario` | `str` | Gateway | Email del vendedor (para evento de calendario) |
| `context.config.archivo_saludo` | `str` | Gateway | URL de imagen/video de saludo (primer mensaje) |

**Response:**

```json
{
  "reply": "¡Tu cita quedó confirmada para el lunes 27 a las 2:00 PM!",
  "url": null
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `reply` | `str` | Texto de respuesta del agente |
| `url` | `str \| null` | URL de imagen/video de saludo (solo en primer mensaje si `archivo_saludo` está configurado) |

**Códigos HTTP:**

| Código | Situación |
|---|---|
| `200` | Respuesta exitosa (incluso si hubo degradación interna) |
| `422` | Validación fallida (falta `id_empresa`, mensaje vacío, etc.) |
| `504` | `CHAT_TIMEOUT` excedido |
| `500` | Error interno no controlado |

### `GET /health`

```json
{ "status": "ok", "agent": "citas_ventas", "version": "2.0.0", "issues": [] }
```

Retorna `503` con `"status": "degraded"` si algún circuit breaker está abierto o falta la API key.

### `GET /metrics`

Métricas en formato Prometheus (HTTP requests, duración, tool calls, cache hits/misses, bookings, etc.).

---

## Herramientas del agente (Tools)

El agente cuenta con 4 herramientas async que el LLM invoca según el contexto de la conversación. Cada tool recibe parámetros de dos fuentes:

- **IA**: parámetros que el LLM completa a partir de la conversación con el usuario
- **Contexto gateway**: parámetros inyectados automáticamente desde `context.config` del request (el LLM no los ve ni los decide)

### 1. `search_productos_servicios`

Busca productos y servicios en el catálogo de la empresa.

**`codOpe`:** `BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS`
**API:** `ws_informacion_ia.php`
**Resiliencia:** Cache TTL 15min + Lock anti-thundering herd + Retry tenacity + Circuit breaker `informacion_cb`

| Parámetro | Tipo | Origen | Descripción |
|---|---|---|---|
| `busqueda` | `str` | **IA** | Término de búsqueda (ej: "laptop", "consulta dental") |
| `id_empresa` | `int` | Contexto gateway | ID de la empresa (inyectado automáticamente) |

**Payload enviado a la API:**
```json
{
  "codOpe": "BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS",
  "id_empresa": 123,
  "busqueda": "laptop",
  "limite": 10
}
```

**Formato de respuesta al agente:**
```
### Laptop HP Pavilion
- ID: 3344
- Precio: S/. 1999.99 por unidad
- Categoría: Electrónica
- Descripción: Laptop con procesador Intel Core i5, 8GB RAM...

### Monitor Samsung 24"
- ID: 3345
- Precio: S/. 599.00 por unidad
- Categoría: Electrónica
- Descripción: Monitor LED Full HD, HDMI, VGA...
```

> El ID es crítico: se usa después en `registrar_pedido` para identificar cada producto.

---

### 2. `registrar_pedido`

Registra el pedido confirmado del cliente en el sistema. Se invoca **una sola vez** cuando el agente ya recopiló todos los datos: productos con ID, comprobante de pago, datos del cliente, modalidad de entrega y monto.

**`codOpe`:** `REGISTRAR_PEDIDO`
**API:** `ws_informacion_ia.php`
**Resiliencia:** Sin retry (operación de escritura — riesgo de duplicados). Sin circuit breaker. POST único directo.

| Parámetro | Tipo | Origen | Descripción |
|---|---|---|---|
| `productos` | `list[dict]` | **IA** | Lista de `{"id_catalogo": int, "cantidad": int}` |
| `operacion` | `str` | **IA** | Número de operación del comprobante de pago |
| `modalidad` | `str` | **IA** | `"Delivery"` o `"Recojo"` |
| `tipo_envio` | `str` | **IA** | Tipo de envío (ej: "Express", "Normal", "Recojo") |
| `nombre` | `str` | **IA** | Nombre completo del cliente |
| `dni` | `str` | **IA** | DNI del cliente |
| `celular` | `str` | **IA** | Celular del cliente |
| `direccion` | `str` | **IA** | Dirección de entrega (vacío si es recojo) |
| `costo_envio` | `float` | **IA** | Costo del envío (0 si es recojo) |
| `sucursal` | `str` | **IA** | Nombre de sucursal de recojo (vacío si es delivery) |
| `medio_pago` | `str` | **IA** | Medio de pago (ej: "yape", "transferencia BCP") |
| `monto_pagado` | `float` | **IA** | Monto total pagado por el cliente |
| `fecha_entrega_estimada` | `str` | **IA** | Fecha estimada de entrega `YYYY-MM-DD` |
| `email` | `str` | **IA** | Email del cliente (opcional) |
| `observacion` | `str` | **IA** | Observaciones adicionales (opcional) |
| `id_empresa` | `int` | Contexto gateway | ID de la empresa |
| `id_prospecto` | `int` | Contexto gateway | ID del prospecto (session_id) |

**Payload enviado a la API:**
```json
{
  "codOpe": "REGISTRAR_PEDIDO",
  "id_empresa": 123,
  "id_moneda": 1,
  "id_prospecto": 1234,
  "productos": [{"id_catalogo": 3344, "cantidad": 2}],
  "operacion": "YAPE12345",
  "modalidad": "Delivery",
  "tipo_envio": "Express",
  "nombre": "Juan Pérez",
  "dni": "12345678",
  "celular": "987654321",
  "direccion": "Av. Principal 456, San Isidro",
  "costo_envio": 5.0,
  "sucursal": "",
  "medio_pago": "yape",
  "monto_pagado": 4003.0,
  "fecha_entrega_estimada": "2026-01-28",
  "email": "",
  "observacion": ""
}
```

**Respuesta al agente:**
- Exitoso: `"Pedido registrado exitosamente. Número de pedido: PED-2026-001."`
- Fallido: `"No se pudo registrar el pedido: <mensaje de error>"`

---

### 3. `check_availability`

Consulta horarios disponibles para una cita. Opera en dos modos según si se pasa `time` o no.

**API:** `ws_agendar_reunion.php`
**Resiliencia:** Circuit breaker `agendar_reunion_cb` + Retry tenacity + Cache de horarios 5min

| Parámetro | Tipo | Origen | Descripción |
|---|---|---|---|
| `date` | `str` | **IA** | Fecha en formato `YYYY-MM-DD` |
| `time` | `str \| None` | **IA** | Hora opcional (ej: `"2:00 PM"`). Si se pasa, verifica ese slot exacto; si no, sugiere horarios |
| `id_empresa` | `int` | Contexto gateway | ID de la empresa |
| `duracion_cita_minutos` | `int` | Contexto gateway | Duración de la cita |
| `slots` | `int` | Contexto gateway | Intervalo entre slots |
| `agendar_usuario` | `int` | Contexto gateway | ID del usuario del calendario |
| `agendar_sucursal` | `int` | Contexto gateway | ID de la sucursal |

**Modo 1 — Con `time` (verificar slot exacto):**

`codOpe`: `CONSULTAR_DISPONIBILIDAD`

```json
{
  "codOpe": "CONSULTAR_DISPONIBILIDAD",
  "id_empresa": 123,
  "fecha_inicio": "2026-01-27 14:00:00",
  "fecha_fin": "2026-01-27 15:00:00",
  "slots": 30,
  "agendar_usuario": 5,
  "agendar_sucursal": 1
}
```

**Modo 2 — Sin `time` (sugerir horarios):**

`codOpe`: `SUGERIR_HORARIOS`

```json
{
  "codOpe": "SUGERIR_HORARIOS",
  "id_empresa": 123,
  "duracion_minutos": 30,
  "slots": 30,
  "agendar_usuario": 5,
  "agendar_sucursal": 1
}
```

**Respuesta al agente:**
```
Horarios disponibles para el 2026-01-27:
1. Hoy a las 09:00 AM
2. Hoy a las 10:00 AM
3. Mañana a las 02:00 PM
```

**Validación previa:** `validate_date_format()` verifica formato YYYY-MM-DD antes de llamar a la API.

---

### 4. `create_booking`

Crea una cita/evento en el calendario con validación completa de múltiples capas.

**APIs:** `ws_agendar_reunion.php` (validación) → `ws_calendario.php` (creación)
**Resiliencia:** CB `agendar_reunion_cb` (validación) + CB `calendario_cb` (creación)

| Parámetro | Tipo | Origen | Descripción |
|---|---|---|---|
| `date` | `str` | **IA** | Fecha `YYYY-MM-DD` |
| `time` | `str` | **IA** | Hora (ej: `"02:00 PM"`) |
| `customer_name` | `str` | **IA** | Nombre completo del cliente |
| `customer_contact` | `str` | **IA** | Email del cliente |
| `id_empresa` | `int` | Contexto gateway | ID de la empresa |
| `duracion_cita_minutos` | `int` | Contexto gateway | Duración de la cita |
| `slots` | `int` | Contexto gateway | Intervalo entre slots |
| `agendar_usuario` | `int` | Contexto gateway | ID del usuario del calendario |
| `agendar_sucursal` | `int` | Contexto gateway | ID de la sucursal |
| `usuario_id` | `int` | Contexto gateway | ID del vendedor |
| `correo_usuario` | `str` | Contexto gateway | Email del vendedor |
| `id_prospecto` | `int` | Contexto gateway | ID del prospecto (session_id) |

**Flujo interno de validación (en orden):**

```
1. validate_date_format(date)         → ¿formato YYYY-MM-DD correcto?
2. validate_booking_data(date, time,  → ¿fecha no es pasada? ¿hora válida?
   customer_name, customer_contact)      ¿nombre válido? ¿email válido?
3. ScheduleValidator.validate()       → ¿día tiene horario? ¿hora dentro del rango?
                                         ¿duración no excede cierre? ¿no bloqueado?
4. CONSULTAR_DISPONIBILIDAD (API)     → ¿slot realmente libre en el calendario?
5. CREAR_EVENTO (API)                 → Crear evento en ws_calendario.php
```

**Payload de creación (CREAR_EVENTO):**
```json
{
  "codOpe": "CREAR_EVENTO",
  "usuario_id": 5,
  "id_prospecto": 1234,
  "titulo": "Reunion para el usuario: María García",
  "fecha_inicio": "2026-01-27 14:00:00",
  "fecha_fin": "2026-01-27 14:30:00",
  "correo_cliente": "maria@example.com",
  "correo_usuario": "vendedor@mitienda.com",
  "agendar_usuario": 1
}
```

**Respuesta al agente:**
```
Evento agregado correctamente.
Detalles:
• Fecha: 2026-01-27
• Hora: 02:00 PM
• Nombre: María García

La reunión será por videollamada. Enlace: https://meet.google.com/abc-defg-hij
```

> El enlace de Google Meet solo aparece si el calendario del vendedor está sincronizado con Google Calendar.

---

## Capa de servicios

### Infraestructura HTTP

#### `http_client.py` — Cliente HTTP compartido

Un único `httpx.AsyncClient` singleton (lazy-init) reutilizado por todos los servicios:

```
Connection pool: 50 max conexiones, 20 keep-alive, 30s keep-alive expiry
Timeouts:
  connect: 5.0s
  read:    API_TIMEOUT (10s default)
  write:   5.0s
  pool:    2.0s
```

Tres niveles de función:

| Función | Responsabilidad |
|---|---|
| `get_client()` | Retorna el AsyncClient singleton (lo crea si no existe) |
| `post_with_retry(url, json)` | POST con tenacity: reintenta solo `TransportError`, backoff exponencial |
| `post_with_logging(url, payload)` | Wrapper sobre `post_with_retry` que loguea request/response en DEBUG |

El retry de tenacity **no reintenta** errores HTTP 4xx/5xx — esos se retornan al caller.

#### `_resilience.py` — Wrapper de circuit breaker

```python
resilient_call(coro_factory, cb, circuit_key, service_name)
```

1. Verifica si el CB está abierto → `RuntimeError` si lo está (fast fail sin HTTP)
2. Ejecuta `coro_factory()` (la llamada HTTP real)
3. En `TransportError`: registra fallo en el CB + re-lanza
4. En otros errores: re-lanza sin afectar el CB

#### `circuit_breaker.py` — 4 circuit breakers

| CB | Key | Servicios protegidos |
|---|---|---|
| `informacion_cb` | `id_empresa` | categorías, sucursales, métodos de pago, contexto, costos envío, búsqueda, horarios |
| `preguntas_cb` | `id_chatbot` | preguntas frecuentes |
| `calendario_cb` | `"global"` | creación de eventos (endpoint compartido entre empresas) |
| `agendar_reunion_cb` | `id_empresa` | validación de disponibilidad, sugerencia de horarios |

Todos configurados con `CB_THRESHOLD` (3 fallos) y `CB_RESET_TTL` (300s). Particionados por key: si la API falla para una empresa, las demás no se ven afectadas.

**Stack completo por request de lectura:**
```
resilient_call (circuit breaker check)
  └─ post_with_logging (logging DEBUG)
       └─ post_with_retry (tenacity retry en TransportError)
            └─ httpx.AsyncClient.post (HTTP real)
```

---

### Servicios de lectura (system prompt)

Estos servicios se invocan al construir el agente (cache miss) y alimentan el system prompt vía Jinja2. Todos usan `TTLCache` + `asyncio.Lock` anti-thundering herd + `resilient_call`.

| Servicio | Archivo | `codOpe` | API | Cache | Formato de salida |
|---|---|---|---|---|---|
| `obtener_categorias()` | `categorias.py` | `OBTENER_CATEGORIAS` | ws_informacion | TTL 1h, max 500 | `"1) Nombre: descripción. (N productos)"` |
| `obtener_sucursales()` | `sucursales.py` | `OBTENER_SUCURSALES_PUBLICAS` | ws_informacion | TTL 1h, max 500 | `"1) Tienda Centro, Av. Principal. Horario: Lun-Vie 09-18"` |
| `obtener_metodos_pago()` | `metodos_pago.py` | `OBTENER_METODOS_PAGO` | ws_informacion | TTL 1h, max 500 | Dos secciones: `Bancos:` + `Billeteras digitales:` |
| `fetch_contexto_negocio()` | `contexto_negocio.py` | `OBTENER_CONTEXTO_NEGOCIO` | ws_informacion | TTL 1h, max 500 | Texto libre o `None` |
| `obtener_costos_envio()` | `costo_envio.py` | `OBTENER_COSTO_ENVIO` | ws_informacion | TTL 1h, max 500 | `"- Zona: San Isidro — Costo: S/ 20, Tipo: Delivery, Tiempo: 4 dias"` |
| `fetch_preguntas_frecuentes()` | `preguntas_frecuentes.py` | *(implícito)* | ws_preguntas | TTL 1h, max 500 | `"Pregunta: ...\nRespuesta: ..."` |
| `fetch_horario_reuniones()` | `horario_reuniones.py` | `OBTENER_HORARIO_REUNIONES` | ws_informacion | TTL 5min (compartido) | `"- Lunes: 09:00 - 19:00"` |

Todos los payloads siguen la estructura `{"codOpe": "...", "id_empresa": N}` (excepto preguntas que usa `{"id_chatbot": N}`).

Si un servicio falla, el system prompt se construye con defaults seguros (ej: "No hay información de productos disponible, usa la herramienta de búsqueda").

---

### Servicios de citas

| Servicio | Archivo | Función |
|---|---|---|
| `horario_cache.py` | `get_horario(id_empresa)` | Cache compartido de horarios. Lo usan tanto `horario_reuniones` (prompt) como `schedule_validator` (validación). TTL 5min. Retorna dict con `reunion_lunes..reunion_domingo` (ej: `"09:00-19:00"` o `null`) + `horarios_bloqueados` |
| `schedule_validator.py` | `ScheduleValidator` | Clase que valida disponibilidad. Métodos: `validate()` (validación completa paso a paso), `recommendation()` (sugerir horarios), `_check_availability()` (consulta API específica) |
| `booking.py` | `confirm_booking()` | Crea evento en `ws_calendario.php`. Convierte 12h→24h, calcula `fecha_fin`. Retorna dict con `success`, `message`, `google_meet_link` |

---

### Contratos de API externos

| API | `codOpe` | Payload | Respuesta |
|---|---|---|---|
| `ws_informacion_ia.php` | `OBTENER_CATEGORIAS` | `{codOpe, id_empresa}` | `{success, categorias: [{nombre, descripcion, cantidad_productos}]}` |
| `ws_informacion_ia.php` | `OBTENER_SUCURSALES_PUBLICAS` | `{codOpe, id_empresa}` | `{success, sucursales: [{nombre, direccion, horario_lunes...}]}` |
| `ws_informacion_ia.php` | `OBTENER_METODOS_PAGO` | `{codOpe, id_empresa}` | `{success, metodos_pago: {bancos: [...], yape: {...}, plin: {...}}}` |
| `ws_informacion_ia.php` | `OBTENER_CONTEXTO_NEGOCIO` | `{codOpe, id_empresa}` | `{success, contexto_negocio: "texto libre"}` |
| `ws_informacion_ia.php` | `OBTENER_COSTO_ENVIO` | `{codOpe, id_empresa}` | `{success, zonas_costos: "{\"zonas\":[...]}"}` |
| `ws_informacion_ia.php` | `OBTENER_HORARIO_REUNIONES` | `{codOpe, id_empresa}` | `{success, horario_reuniones: {reunion_lunes..., horarios_bloqueados}}` |
| `ws_informacion_ia.php` | `BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS` | `{codOpe, id_empresa, busqueda, limite}` | `{success, productos: [{id, nombre, precio_unitario, nombre_unidad, nombre_categoria, descripcion}]}` |
| `ws_informacion_ia.php` | `REGISTRAR_PEDIDO` | `{codOpe, id_empresa, id_moneda, id_prospecto, productos, operacion, modalidad, ...}` | `{success, id_pedido: "PED-..."}` |
| `ws_preguntas_frecuentes.php` | *(implícito)* | `{id_chatbot}` | `{success, preguntas_frecuentes: [{pregunta, respuesta}]}` |
| `ws_agendar_reunion.php` | `CONSULTAR_DISPONIBILIDAD` | `{codOpe, id_empresa, fecha_inicio, fecha_fin, slots, agendar_usuario, agendar_sucursal}` | `{success, disponible: bool}` |
| `ws_agendar_reunion.php` | `SUGERIR_HORARIOS` | `{codOpe, id_empresa, duracion_minutos, slots, agendar_usuario, agendar_sucursal}` | `{success, sugerencias: [{dia, hora_legible, disponible, fecha_inicio}], total, mensaje}` |
| `ws_calendario.php` | `CREAR_EVENTO` | `{codOpe, usuario_id, id_prospecto, titulo, fecha_inicio, fecha_fin, correo_cliente, correo_usuario, agendar_usuario}` | `{success, message, google_meet_link?, google_calendar_synced?}` |

---

## Capa del agente

### Construcción (`agent.py`)

El agente se construye con LangGraph `create_agent()`:

```
LLM:         OpenAI (gpt-4o-mini, temperature=0.5, max_tokens=2048)
Tools:       [search_productos_servicios, registrar_pedido, check_availability, create_booking]
Prompt:      System prompt dinámico (Jinja2 con datos de la empresa)
Checkpointer: InMemorySaver (historial de conversación por session_id)
Response:    VentasStructuredResponse {reply: str, url: str | None}
```

### Cache de agentes

- **Key:** `id_empresa` (un agente por empresa)
- **TTL:** `AGENT_CACHE_TTL_MINUTES` (60 min por defecto)
- **Maxsize:** `AGENT_CACHE_MAXSIZE` (500 empresas)
- **Anti-thundering herd:** `asyncio.Lock` por `id_empresa` con double-check post-lock

Flujo:
```
cache hit  → retorna agente inmediatamente (O(1))
cache miss → Lock(id_empresa) → double-check → build_agent_for_empresa() → store
```

### Session locks

Un `asyncio.Lock` por `session_id` serializa llamadas concurrentes del mismo usuario, evitando race conditions en el checkpointer `InMemorySaver`.

### Visión multimodal

El agente detecta URLs de imágenes en los mensajes (jpg, jpeg, png, gif, webp) y las envía como bloques de visión de OpenAI. Permite validar comprobantes de pago enviados como capturas. Máximo 10 imágenes por mensaje.

### Extracción de respuesta

1. Intenta: `result["structured_response"]` → `VentasStructuredResponse` (preferido)
2. Fallback: texto de `result["messages"][-1].content`
3. Default: `"Lo siento, no pude procesar tu solicitud."`

---

## System prompt dinámico

### Template: `prompts/citas_ventas_system.j2`

Se construye con `build_ventas_system_prompt(config)` que hace 7 fetches en paralelo (`asyncio.gather`) y renderiza el template Jinja2.

**Variables del template:**

| Variable | Origen | Descripción |
|---|---|---|
| `personalidad` | Gateway config | Instrucciones de tono y estilo |
| `nombre_asistente` | Gateway config | Nombre del bot |
| `nombre_negocio` | Gateway config | Nombre de la empresa |
| `propuesta_valor` | Gateway config | Propuesta de valor |
| `archivo_saludo` | Gateway config | URL de media de saludo |
| `informacion_productos_servicios` | `obtener_categorias()` | Categorías del catálogo |
| `informacion_sucursales` | `obtener_sucursales()` | Sucursales con horarios |
| `medios_pago` | `obtener_metodos_pago()` | Bancos y billeteras |
| `contexto_negocio` | `fetch_contexto_negocio()` | Contexto libre del negocio |
| `preguntas_frecuentes` | `fetch_preguntas_frecuentes()` | FAQs |
| `informacion_costos_envio` | `obtener_costos_envio()` | Zonas y costos de envío |
| `horario_reuniones` | `fetch_horario_reuniones()` | Horarios de citas |

**Secciones principales del prompt:**

1. **Rol y personalidad** — Agente de venta directa con tono configurable
2. **Formato de respuesta** — `reply` (texto) + `url` (media de saludo en primer mensaje)
3. **Reglas WhatsApp** — Bold con `*asterisco simple*`, URLs sin markdown
4. **Flujo de ventas completo:**
   - Pasos comunes (1–4): saludo → info productos → confirmar selección → preguntar modalidad
   - Rama Delivery (10 pasos): distrito → envío → resumen → pago → comprobante → dirección → referencia → confirmar → boleta/factura → cierre
   - Rama Recojo (8 pasos): sucursal → resumen → pago → comprobante → confirmar → boleta/factura → cierre
5. **Documentación de tools** — Cuándo y cómo usar cada herramienta
6. **Reglas generales** — Una cosa a la vez, brevedad, nunca inventar datos, usar nombre del cliente

---

## Validación

### `validation.py` — Modelos Pydantic

| Función | Qué valida | Reglas |
|---|---|---|
| `validate_date_format(date)` | Formato de fecha | YYYY-MM-DD (solo sintaxis, no verifica si es pasada) |
| `validate_booking_data(date, time, name, contact)` | Datos completos de booking | Fecha no pasada (con timezone), hora válida (12h/24h), nombre sin dígitos (2-100 chars), email RFC 5322 |
| `validate_contact(contact)` | Email | Patrón RFC 5322 simplificado, retorna lowercase |
| `validate_customer_name(name)` | Nombre | 2-100 chars, sin dígitos, acepta acentos/ñ, retorna titlecase |
| `validate_datetime(date, time)` | Fecha + hora | Fecha no pasada (ZoneInfo America/Lima), hora 12h o 24h |

Todas retornan `(bool, str | None)` — `(True, None)` si es válido, `(False, "mensaje de error en español")` si no.

---

## Resiliencia

El sistema implementa múltiples capas de protección, cada una con responsabilidad distinta:

### Stack de resiliencia por request

```
┌─ resilient_call ─────────────────────────┐
│  Circuit breaker: ¿está abierto?         │
│  Si abierto → RuntimeError (fast fail)   │
│                                          │
│  ┌─ post_with_logging ────────────────┐  │
│  │  Log DEBUG request/response        │  │
│  │                                    │  │
│  │  ┌─ post_with_retry ───────────┐   │  │
│  │  │  tenacity retry:            │   │  │
│  │  │  3 intentos, backoff 1-4s   │   │  │
│  │  │  Solo TransportError        │   │  │
│  │  │                             │   │  │
│  │  │  ┌─ httpx POST ──────────┐  │   │  │
│  │  │  │  connect: 5s          │  │   │  │
│  │  │  │  read: 10s            │  │   │  │
│  │  │  │  write: 5s            │  │   │  │
│  │  │  └───────────────────────┘  │   │  │
│  │  └─────────────────────────────┘   │  │
│  └────────────────────────────────────┘  │
│                                          │
│  Si TransportError → CB.record_failure   │
│  Si OK → CB.record_success              │
└──────────────────────────────────────────┘
```

### Excepción: `registrar_pedido`

Usa `get_client().post()` directo, sin retry ni circuit breaker. Motivo: es una operación de escritura — si el servidor recibió el pedido pero el response se perdió por timeout, reintentar crearía un duplicado.

### Cache TTL por servicio

| Servicio | TTL | Motivo |
|---|---|---|
| Categorías, sucursales, métodos de pago, contexto, costos, FAQs | 1 hora | Datos estables que cambian raramente |
| Búsqueda de productos | 15 min | Pueden agregarse productos frecuentemente |
| Horarios de reuniones | 5 min | Citas se agendan en tiempo real, necesita frescura |

### Degradación graceful

Si un servicio falla al construir el system prompt, el agente se crea igualmente con defaults seguros. Ejemplo: si `obtener_categorias()` falla, el prompt dice "No hay información de productos disponible, usa la herramienta search_productos_servicios para buscar".

---

## Concurrencia y locks

Arquitectura 100% async (httpx, LangChain ainvoke, servicios). Cinco patrones de lock:

| Lock | Key | Propósito | Cleanup |
|---|---|---|---|
| Agent cache | `id_empresa` | Serializa construcción de agente en cache miss | Stale locks cuando count > 750 |
| Session | `session_id` | Serializa ainvoke del mismo usuario (protege InMemorySaver) | Stale locks cuando count > 500 |
| Búsqueda | `(id_empresa, término)` | Anti-thundering herd en cache miss de búsqueda | En finally block |
| Contexto negocio | `id_empresa` | Anti-thundering herd en cache miss de contexto | En finally block |
| Horario cache | `id_empresa` | Anti-thundering herd en cache miss de horarios | En finally block |

Todos usan el patrón **Lock + double-check**: adquirir lock → verificar cache de nuevo → solo si sigue vacío, ejecutar fetch.

---

## Observabilidad

### Logging estructurado

Prefijos por módulo para rastreo en producción:

| Prefijo | Módulo |
|---|---|
| `[HTTP]` | main.py (requests entrantes) |
| `[AGENT]` | agent.py (lógica del agente) |
| `[TOOL]` | tools.py (ejecución de herramientas) |
| `[API]` | http_client.py (requests salientes) |
| `[CB:servicio]` | circuit_breaker.py (cambios de estado) |
| `[BUSQUEDA]` | busqueda_productos.py |
| `[REGISTRAR_PEDIDO]` | registrar_pedido.py |
| `[HORARIO_CACHE]` | horario_cache.py |
| `[AVAILABILITY]` | schedule_validator.py |
| `[BOOKING]` | booking.py |
| `[CONTEXTO_NEGOCIO]`, `[CATEGORIAS]`, etc. | Servicios específicos |

Nivel y destino configurables con `LOG_LEVEL` y `LOG_FILE`. Third-party (httpx, openai, langchain) silenciados a WARNING.

### Métricas Prometheus (`GET /metrics`)

| Métrica | Labels | Descripción |
|---|---|---|
| `ventas_http_requests_total` | status | Requests HTTP entrantes |
| `ventas_http_duration_seconds` | — | Latencia de requests (histogram 0.25–120s) |
| `ventas_llm_requests_total` | status | Llamadas al LLM |
| `ventas_llm_duration_seconds` | — | Latencia del LLM (histogram 0.5–90s) |
| `ventas_chat_response_duration_seconds` | status | Duración de respuesta completa |
| `ventas_agent_cache_total` | result (hit/miss) | Cache de agentes |
| `ventas_tool_calls_total` | tool, status | Invocaciones de tools |
| `ventas_search_cache_total` | result (hit/miss/circuit_open) | Cache de búsqueda |
| `ventas_chat_requests_total` | empresa_id | Requests por empresa |
| `ventas_chat_errors_total` | error_type | Errores por tipo |
| `ventas_api_calls_total` | endpoint, status | Calls a APIs externas |
| `ventas_api_call_duration_seconds` | endpoint | Latencia de APIs externas |
| `ventas_booking_attempts_total` | — | Intentos de agendar cita |
| `ventas_booking_success_total` | — | Citas agendadas exitosamente |
| `ventas_booking_failed_total` | reason | Citas fallidas por razón |
| `ventas_cache_size` | cache_name | Tamaño actual de caches |
| `agent_citas_ventas_info` | version, model, agent_type | Metadata del agente |
