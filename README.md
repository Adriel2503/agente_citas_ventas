# Agente Ventas — MaravIA

Agente especializado en ventas directas diseñado para operar dentro del ecosistema multiagente de **MaravIA**. Recibe delegaciones del orquestador principal y guía al cliente a través del flujo completo de compra: búsqueda de productos, selección, modalidad de entrega, pago y confirmación.

## Tabla de contenidos

- [Descripción general](#descripción-general)
- [Arquitectura](#arquitectura)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Requisitos](#requisitos)
- [Configuración](#configuración)
- [Ejecución](#ejecución)
  - [Local](#local)
  - [Docker](#docker)
- [API / Tool MCP expuesta](#api--tool-mcp-expuesta)
- [Flujo de ventas](#flujo-de-ventas)
- [Herramientas internas del agente](#herramientas-internas-del-agente)
- [Servicios externos](#servicios-externos)
- [Características destacadas](#características-destacadas)

---

## Descripción general

El Agente Ventas es un microservicio que expone una única tool MCP (`venta_chat`) al orquestador. Internamente utiliza:

- **LangChain + LangGraph** para la lógica del agente con memoria de sesión
- **OpenAI** (GPT-4o-mini por defecto) como LLM principal
- **FastMCP / FastAPI** como servidor MCP
- **Jinja2** para la generación dinámica del system prompt
- **API MaravIA** para obtener categorías, sucursales, métodos de pago y búsqueda de productos

---

## Arquitectura

```
Orquestador
    │
    │  MCP call: venta_chat(message, session_id, context)
    ▼
┌─────────────────────────────────────────┐
│  FastMCP Server  (puerto 8001)          │
│                                         │
│  process_venta_message()                │
│    ├─ Validar contexto (id_empresa)     │
│    ├─ Build system prompt (Jinja2)      │
│    │    ├─ obtener_categorias()  ──────►│ API MaravIA
│    │    ├─ obtener_sucursales()  ──────►│ API MaravIA
│    │    └─ obtener_metodos_pago() ─────►│ API MaravIA
│    │                                   │
│    └─ LangChain Agent                  │
│         ├─ OpenAI Chat Model           │
│         ├─ InMemorySaver (sesión)      │
│         └─ Tool: search_productos ────►│ API MaravIA
└─────────────────────────────────────────┘
    │
    │  Respuesta al orquestador
    ▼
```

---

## Estructura del proyecto

```
agent_ventas/
├── src/ventas/
│   ├── main.py                    # Servidor FastMCP, tool venta_chat
│   ├── logger.py                  # Configuración de logging
│   ├── agent/
│   │   └── agent.py               # Lógica del agente LangChain/LangGraph
│   ├── config/
│   │   └── config.py              # Variables de entorno y configuración
│   ├── tool/
│   │   └── tools.py               # Herramientas del agente (search_productos_servicios)
│   ├── prompts/
│   │   ├── __init__.py            # build_ventas_system_prompt()
│   │   └── ventas_system.j2       # Template Jinja2 del system prompt
│   └── services/
│       ├── api_informacion.py     # Cliente HTTP compartido (httpx)
│       ├── busqueda_productos.py  # Búsqueda y formateo de productos
│       ├── categorias.py          # Obtención de categorías
│       ├── metodos_pago.py        # Métodos de pago
│       └── sucursales.py          # Sucursales y horarios
├── run.py                         # Script de entrada
├── requirements.txt               # Dependencias Python
├── Dockerfile                     # Imagen Python 3.12 slim
├── compose.yaml                   # Docker Compose
├── .env.example                   # Plantilla de configuración
└── .gitignore
```

---

## Requisitos

- Python **3.12+**
- Cuenta de **OpenAI** con API Key
- Acceso a la **API MaravIA** (`ws_informacion_ia.php`)
- Docker (opcional, para despliegue en contenedor)

---

## Configuración

Copia `.env.example` a `.env` y completa los valores:

```bash
cp .env.example .env
```

| Variable | Default | Descripción |
|---|---|---|
| `OPENAI_API_KEY` | *(requerido)* | Clave de API de OpenAI |
| `OPENAI_MODEL` | `gpt-4o-mini` | Modelo de OpenAI a usar |
| `OPENAI_TEMPERATURE` | `0.5` | Temperatura del modelo (0.0–2.0) |
| `OPENAI_TIMEOUT` | `90` | Timeout por llamada a OpenAI (segundos) |
| `MAX_TOKENS` | `2048` | Máximo de tokens por respuesta |
| `SERVER_HOST` | `0.0.0.0` | Host del servidor MCP |
| `SERVER_PORT` | `8001` | Puerto del servidor MCP |
| `LOG_LEVEL` | `INFO` | Nivel de logging (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FILE` | *(vacío)* | Ruta de archivo de log (vacío = solo consola) |
| `API_TIMEOUT` | `10` | Timeout por request HTTP a la API (segundos) |
| `CHAT_TIMEOUT` | `120` | Timeout global por mensaje (debe ser ≥ OPENAI_TIMEOUT) |
| `API_INFORMACION_URL` | `https://api.maravia.pe/servicio/ws_informacion_ia.php` | Endpoint de la API MaravIA |

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

El servidor arrancará en `http://0.0.0.0:8001` y registrará en consola:

```
INICIANDO AGENTE VENTAS - MaravIA
Host: 0.0.0.0:8001
Modelo: gpt-4o-mini
Timeouts: OPENAI=90s, API=10s, CHAT=120s
Tool expuesta al orquestador: venta_chat
Tools internas: search_productos_servicios
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

## API / Tool MCP expuesta

El servidor expone una única tool al orquestador vía MCP:

### `venta_chat`

| Parámetro | Tipo | Descripción |
|---|---|---|
| `message` | `str` | Mensaje del usuario (puede incluir URLs de imágenes) |
| `session_id` | `int` | ID de sesión (mantiene contexto de conversación) |
| `context` | `dict` | Contexto del negocio (ver campos abajo) |

**Campos del objeto `context`:**

```json
{
  "config": {
    "id_empresa": 123,              // REQUERIDO - ID de la empresa
    "personalidad": "...",          // Instrucciones de personalidad del asistente
    "nombre_negocio": "Mi Tienda",  // Nombre del negocio
    "nombre_asistente": "Valeria",  // Nombre del asistente virtual
    "propuesta_valor": "...",       // Propuesta de valor del negocio
    "medios_pago": "..."            // Info adicional de medios de pago
  }
}
```

**Respuesta:** `str` con la respuesta del agente al usuario.

---

## Flujo de ventas

El system prompt define un flujo de ventas estructurado en dos ramas:

```
Inicio
  └─► Relevamiento de necesidades
        └─► Búsqueda / selección de productos
              ├─► RAMA A: Delivery
              │     ├─ Distrito de entrega
              │     ├─ Tipo de delivery
              │     ├─ Resumen y confirmación
              │     ├─ Método de pago
              │     ├─ Validación de comprobante (imagen)
              │     ├─ Dirección y referencia
              │     └─► Confirmación final
              │
              └─► RAMA B: Recojo en tienda
                    ├─ Selección de sucursal
                    ├─ Resumen y confirmación
                    ├─ Método de pago
                    ├─ Validación de comprobante (imagen)
                    └─► Confirmación final
```

En ambas ramas se captura al final: tipo de comprobante (boleta/factura), nombre y DNI del cliente.

---

## Herramientas internas del agente

### `search_productos_servicios`

Busca productos y servicios en el catálogo de la empresa.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `busqueda` | `str` | Término de búsqueda |
| `limite` | `int` | Máximo de resultados a retornar |

Retorna una lista formateada en markdown con nombre, precio, descripción y código de cada producto.

---

## Servicios externos

Todos los servicios consumen el mismo endpoint `API_INFORMACION_URL` mediante un código de operación (`codOpe`):

| Servicio | `codOpe` | Descripción |
|---|---|---|
| `obtener_categorias()` | `OBTENER_CATEGORIAS` | Categorías del catálogo de productos |
| `obtener_sucursales()` | `OBTENER_SUCURSALES_PUBLICAS` | Sucursales con dirección y horarios |
| `obtener_metodos_pago()` | `OBTENER_METODOS_PAGO` | Bancos y billeteras digitales (Yape, Plin) |
| `fetch_contexto_negocio()` | `OBTENER_CONTEXTO_NEGOCIO` | Contexto del negocio (se inyecta en el system prompt cuando está disponible; cache TTL, circuit breaker y retry) |
| `buscar_productos_servicios()` | `BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS` | Búsqueda en catálogo |

El cliente HTTP es un `httpx.AsyncClient` compartido (lazy-init) que se cierra limpiamente al apagar el servidor.

---

## Características destacadas

### Visión multimodal
El agente detecta automáticamente URLs de imágenes en los mensajes del usuario (formatos: jpg, jpeg, png, gif, webp) y las envía al modelo como bloques de visión de OpenAI. Esto permite validar comprobantes de pago enviados como capturas de pantalla.

### System prompt dinámico con Jinja2
El prompt del sistema se genera en cada sesión con datos reales del negocio: categorías actualizadas, sucursales con horarios compactos, métodos de pago vigentes y contexto de negocio (cuando la API lo devuelve). Si alguna API falla, el agente continúa funcionando con valores por defecto (degradación graceful).

### Memoria de sesión
Usa `InMemorySaver` de LangGraph para mantener el historial de conversación dentro de una sesión. El `thread_id` se deriva del `session_id` recibido del orquestador, garantizando continuidad de contexto entre mensajes.

### Gestión de timeouts en capas
- `API_TIMEOUT`: límite por cada request HTTP individual
- `OPENAI_TIMEOUT`: límite por llamada al LLM
- `CHAT_TIMEOUT`: límite global por mensaje completo (incluye tool calls y razonamiento)

### Logging estructurado
Prefijos por módulo (`[MCP]`, `[AGENT]`, `[TOOL]`, `[API_INFORMACION]`, `[CONTEXTO_NEGOCIO]`) para facilitar el rastreo de flujos en producción. Nivel y destino configurables vía variables de entorno.
