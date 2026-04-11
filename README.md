# Scripts Mostro

Scripts de gestión, monitorización y automatización para un nodo [Mostro](https://mostro.network) P2P.

## Instalación rápida

```bash
git clone git@github.com:21Mill/scripts-mostro.git
cd scripts-mostro
./admin/setup.sh
```

El asistente interactivo te guiará para configurar todas las rutas y credenciales. Genera un archivo `.env` que todos los scripts leen automáticamente.

## Configuración manual

Si prefieres configurar a mano:

```bash
cp .env.example .env
# Edita .env con tus valores
```

Los valores comentados en `.env.example` muestran los defaults. Solo necesitas descomentar y cambiar los que difieran en tu instalación. Las variables de Telegram y Nostr sí son obligatorias si usas los bots.

### Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `MOSTROD_SRC` | `/opt/mostro` | Directorio de fuentes de mostrod |
| `MOSTROD_CONFIG` | `$MOSTROD_SRC/settings.toml` | Configuración de mostrod |
| `MOSTROD_BIN` | `/usr/local/bin/mostrod` | Binario de mostrod |
| `MOSTROD_SERVICE` | `mostro.service` | Servicio systemd |
| `MOSTRIX_SRC` | `~/mostro-sources/mostrix` | Fuentes de mostrix |
| `MOSTRIX_CONFIG` | `~/.mostrix/settings.toml` | Configuración de mostrix |
| `MOSTRIX_BIN` | `/usr/local/bin/mostrix` | Binario de mostrix |
| `WATCHDOG_SRC` | `~/mostro-sources/mostro-watchdog` | Fuentes del watchdog |
| `WATCHDOG_CONFIG` | `$MOSTROD_SRC/config.toml` | Configuración del watchdog |
| `WATCHDOG_BIN` | `/usr/local/bin/mostro-watchdog` | Binario del watchdog |
| `WATCHDOG_SERVICE` | `mostro-watchdog.service` | Servicio systemd |
| `BACKUP_DIR` | `~/mostro-sources/backups` | Directorio de backups |
| `MOSTRO_DB` | `$MOSTROD_SRC/mostro.db` | Base de datos SQLite de órdenes |
| `MOSTRO_DISPUTES_DB` | `/opt/mostro/disputes.db` | Base de datos SQLite de disputas |
| `MOSTRO_USER` | `mostro` | Usuario del sistema que ejecuta mostrod |
| `MOSTRO_LOG` | *(vacío = journalctl)* | Archivo de log |
| `BOT_SERVICE` | `mostrobot.service` | Servicio del bot de Telegram |
| `TELEGRAM_TOKEN` | — | Token del bot de Telegram |
| `TELEGRAM_CHAT_ID` | — | Chat ID para ofertas |
| `TELEGRAM_TEST_CHAT_ID` | — | Chat ID para pruebas |
| `MOSTRO_PUBKEY` | — | Clave pública del nodo Mostro |
| `MOSTRO_RELAY` | `wss://relay.mostro.network` | URL del relay Nostr |
| `NOSTR_BOT_NSEC` | *(se genera automáticamente)* | Clave privada del bot de Nostr |
| `NOSTR_BOT_RELAYS` | `wss://relay.damus.io,wss://nos.lol,wss://relay.mostro.network` | Relays donde publicar ofertas |

## Scripts

### admin/setup.sh

Asistente interactivo de configuración. Pregunta las rutas, valida que existen, permite probar Telegram y genera el `.env`.

```bash
./admin/setup.sh
```

### admin/update.sh

Actualización segura de componentes Mostro (mostrod, mostrix, mostro-watchdog). Descarga binarios precompilados desde GitHub Releases y verifica su integridad con GPG (doble firma: negrunch + arkanoider) y SHA256 antes de instalar.

Antes de cada actualización:
- Hace backup del binario, la configuración **y la base de datos** (`mostro.db`)
- Detecta si la nueva versión incluye migraciones de esquema SQLite y avisa
- Muestra commits incluidos en la actualización y cambios en la plantilla de config

Tras instalar:
- Verifica que el servicio arranca correctamente esperando los mensajes de conexión a LND y relays (hasta 30s)
- Si el servicio falla, hace **rollback automático** al binario anterior

```bash
./admin/update.sh              # Comprobar y actualizar todos
./admin/update.sh mostrod      # Solo mostrod
./admin/update.sh mostrix      # Solo mostrix
./admin/update.sh watchdog     # Solo mostro-watchdog
./admin/update.sh --check      # Solo comprobar versiones, sin actualizar
```

![admin/update.sh](images/mostro-update.png)

### admin/check_channels.sh

Comprueba el número de canales LND inactivos y envía una alerta por Telegram (al mismo canal que el watchdog) si se supera el umbral. Usa el `bot_token` y `chat_id` del archivo de configuración del watchdog (`/opt/mostro/config.toml`), sin necesidad de configuración adicional.

```bash
./admin/check_channels.sh            # Comprueba y alerta si > 2 canales caídos
./admin/check_channels.sh --status   # Muestra estado sin enviar alerta
```

La alerta incluye el número de canales caídos, el total de canales y la lista de canales inactivos con alias y capacidad.

**Cron recomendado** (cada 10 minutos):
```
*/10 * * * * /home/admin/mostro-sources/scripts/admin/check_channels.sh >> /var/log/check_channels.log 2>&1
```

### admin/rollback.sh

Restaura una versión anterior de cualquier componente desde los backups creados por `update.sh`.

```bash
./admin/rollback.sh            # Lista backups disponibles
./admin/rollback.sh mostrod    # Restaurar mostrod del último backup
```

### admin/status.sh

Muestra el estado completo del nodo: servicios activos, versiones instaladas vs disponibles, base de datos y backups.

```bash
./admin/status.sh
```

![admin/status.sh](images/mostro-status.png)

### tools/order.sh

Consulta todos los datos de una orden en la base de datos de Mostro. Soporta búsqueda por UUID completo o parcial, y modos especiales para listar órdenes recientes, pendientes, en curso o estadísticas generales.

Muestra: tipo, estado, montos (incluyendo fiat final en órdenes con rango), comisiones (fee/routing/dev con totales), participantes (pubkeys), datos Lightning (hash/preimage/invoice), disputas, valoraciones, tiempos (con duración del trade) y trade index.

```bash
./tools/order.sh <order_id>       # Consultar una orden (UUID completo)
./tools/order.sh 7361b8fe         # Buscar por UUID parcial
./tools/order.sh --recent         # Últimas 10 órdenes
./tools/order.sh --pending        # Órdenes pendientes activas
./tools/order.sh --active         # Órdenes en curso (tomadas, no finalizadas)
./tools/order.sh --stats          # Estadísticas generales (todo el historial)
./tools/order.sh --stats 7d       # Estadísticas de la última semana
./tools/order.sh --stats 30d      # Estadísticas del último mes
./tools/order.sh --stats 2026-03-01..2026-03-23  # Entre dos fechas
```

Periodos disponibles para `--stats`: `today`/`hoy`, `24h`, `7d`/`week`/`semana`, `30d`/`month`/`mes`, `90d`/`trimestre`, `year`/`año`, `YYYY-MM-DD` (desde fecha), `YYYY-MM-DD..YYYY-MM-DD` (rango).

### tools/report.sh

Genera un informe financiero de la actividad del nodo: volumen de trading, flujo de sats, ingresos, disputas y tendencia diaria con gráfico ASCII.

```bash
./tools/report.sh              # Últimos 30 días (default)
./tools/report.sh today        # Hoy
./tools/report.sh week         # Últimos 7 días
./tools/report.sh month        # Últimos 30 días
./tools/report.sh year         # Último año
./tools/report.sh all          # Todo el historial
./tools/report.sh 2026-03-01 2026-03-31  # Rango de fechas
```

### tools/logs.sh

Busca y formatea logs de Mostro por order ID. Usa `journalctl` por defecto o un archivo de log si `MOSTRO_LOG` está configurado.

```bash
./tools/logs.sh a179dca3
```

![tools/logs.sh](images/mostro_log_search.png)

### tools/monitor.sh

Monitoriza una transacción Bitcoin hasta su confirmación y notifica por Telegram.

```bash
./tools/monitor.sh <txid>
```

### bot/premiums.sh

Genera `data/premiums.json` con los premiums anonimizados y lo sube a GitHub Pages. Se ejecuta automáticamente cada noche vía cron.

```bash
./bot/premiums.sh
```

### bot/bot.py

Bot que escucha nuevas ofertas en el relay de Mostro y las publica en un canal de Telegram. Cuando una oferta es tomada, cancelada o expira, el mensaje se borra automáticamente del canal. Al arrancar, escanea todas las órdenes pendientes de las últimas 24h para publicar las que no hayan sido vistas.

**Dependencias:** `pip install websocket-client requests python-dotenv`

```bash
python3 bot/bot.py
```

### bot/bot-nostr.py

Bot que publica las ofertas como notas (kind 1) en Nostr desde un pubkey dedicado. Cuando una oferta deja de estar pendiente, envía un evento de borrado (NIP-09, kind 5). Si no existe un `NOSTR_BOT_NSEC` en el `.env`, genera las claves automáticamente. Al arrancar, escanea todas las órdenes pendientes para no perder ofertas creadas antes del inicio del bot.

**Dependencias:** `pip install websocket-client pynostr python-dotenv`

```bash
python3 bot/bot-nostr.py
```

### bot/test-telegram.py

Script de prueba para verificar las credenciales de Telegram.

```bash
python3 bot/test-telegram.py
```

## Arquitectura de los bots

Los bots de Telegram y Nostr comparten un módulo común (`bot/common.py`) que contiene:

- Conexión WebSocket al relay de Mostro con keepalive y reconexión automática
- Parsing de eventos kind 38383 (ofertas)
- Formateo de texto (HTML para Telegram, plano para Nostr)
- Persistencia de órdenes publicadas (JSON)

Cada bot se ejecuta como un servicio systemd independiente:

| Servicio | Bot | Plataforma |
|----------|-----|------------|
| `mostrobot.service` | `bot/bot.py` | Telegram |
| `mostrobot-nostr.service` | `bot/bot-nostr.py` | Nostr |

## Estructura

```
.
├── .env.example            # Plantilla de configuración
├── .gitignore              # Excluye .env, logs, orders y cache
├── images/                 # Capturas de pantalla
├── admin/
│   ├── env.sh              # Configuración compartida (cargado por todos los .sh)
│   ├── setup.sh            # Asistente de configuración interactivo
│   ├── status.sh           # Estado del nodo
│   ├── update.sh           # Actualización de componentes (GPG+SHA256, backup BD, rollback)
│   ├── rollback.sh         # Rollback de componentes
│   └── check_channels.sh   # Alerta Telegram si >2 canales LND caídos
├── tools/
│   ├── order.sh            # Consulta de órdenes en base de datos
│   ├── report.sh           # Informe financiero de actividad
│   ├── logs.sh             # Búsqueda en logs
│   └── monitor.sh          # Monitor de transacciones BTC
└── bot/
    ├── premiums.sh         # Generador de datos para GitHub Pages
    ├── bot.py              # Bot de ofertas para Telegram
    ├── bot-nostr.py        # Bot de ofertas para Nostr
    ├── common.py           # Módulo compartido por los bots Python
    └── test-telegram.py    # Test de Telegram
```
