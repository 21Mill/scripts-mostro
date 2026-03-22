# Scripts Mostro

Scripts de gestión, monitorización y automatización para un nodo [Mostro](https://mostro.network) P2P.

## Configuración

Todos los scripts que requieren credenciales leen de un archivo `.env` en el mismo directorio.

```bash
cp .env.example .env
# Edita .env con tus valores reales
```

### Variables de entorno

| Variable | Descripción |
|----------|-------------|
| `TELEGRAM_TOKEN` | Token del bot de Telegram |
| `TELEGRAM_CHAT_ID` | Chat ID para notificaciones de ofertas |
| `TELEGRAM_TEST_CHAT_ID` | Chat ID para pruebas |
| `MOSTRO_PUBKEY` | Clave pública del nodo Mostro |
| `MOSTRO_RELAY` | URL del relay Nostr de Mostro |

## Scripts

### mostro-update.sh

Actualización segura de componentes Mostro (mostrod, mostrix, mostro-watchdog). Compara versiones locales vs remotas, muestra commits pendientes, hace backup antes de actualizar y recompila desde fuentes.

```bash
./mostro-update.sh              # Comprobar y actualizar todos
./mostro-update.sh mostrod      # Solo mostrod
./mostro-update.sh --check      # Solo comprobar, sin actualizar
```

### mostro-rollback.sh

Restaura una versión anterior de cualquier componente desde los backups creados por `mostro-update.sh`.

```bash
./mostro-rollback.sh            # Lista backups disponibles
./mostro-rollback.sh mostrod    # Restaurar mostrod del último backup
./mostro-rollback.sh watchdog   # Restaurar watchdog
```

### mostro-status.sh

Muestra el estado completo del nodo: servicios activos, versiones instaladas vs disponibles, base de datos y backups.

```bash
./mostro-status.sh
```

### mostro_bot.py

Bot que escucha nuevas ofertas en el relay de Mostro y las publica en un canal de Telegram con formato enriquecido.

**Dependencias:** `pip install websocket-client requests python-dotenv`

```bash
python3 mostro_bot.py
```

### mostro_log_search.sh

Busca y formatea entradas del log de Mostro por order ID. Colorea por nivel (ERROR, WARN, INFO, DEBUG) y muestra un resumen.

```bash
./mostro_log_search.sh a179dca3
./mostro_log_search.sh a179dca3-ce49-4d59-a47b-5627439b41a5
```

### monitor_tx.sh

Monitoriza una transacción Bitcoin hasta su confirmación y notifica por Telegram. Usa mempool.space con fallback a blockstream.info.

```bash
./monitor_tx.sh <txid>
```

### test_telegram.py

Script de prueba para verificar que las credenciales de Telegram funcionan correctamente.

```bash
python3 test_telegram.py
```

## Estructura

```
.
├── .env.example          # Plantilla de configuración
├── .gitignore            # Excluye .env, logs y cache
├── monitor_tx.sh         # Monitor de transacciones BTC
├── mostro-rollback.sh    # Rollback de componentes
├── mostro-status.sh      # Estado del nodo
├── mostro-update.sh      # Actualización de componentes
├── mostro_bot.py         # Bot de ofertas para Telegram
├── mostro_log_search.sh  # Búsqueda en logs
└── test_telegram.py      # Test de Telegram
```
