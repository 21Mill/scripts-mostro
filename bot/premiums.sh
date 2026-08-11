#!/usr/bin/env bash
# premiums.sh — Genera data/premiums.json con premiums anonimizados y lo sube a GitHub Pages.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../admin/env.sh"

DB_PATH="${MOSTRO_DB_RO:-/var/lib/mostro-snapshot/mostro.db}"
WEB_REPO="${NOSTROMOSTRO_WEB_REPO:-$HOME/nostromostro.github.io}"

run_sql() {
    local db="$1"
    shift
    # Lee la instantánea de solo lectura que publica mostro-snapshot.timer. Antes esto
    # recurría a 'sudo -u mostro sqlite3', una regla NOPASSWD que en la práctica daba shell
    # completa como mostro: sqlite3 ejecuta órdenes con .shell y, aun con -readonly,
    # SELECT writefile() escribe ficheros desde SQL puro.
    sqlite3 -readonly "$db" "$@" 2>/dev/null
}

# Verificar acceso
if ! run_sql "$DB_PATH" "SELECT 1" >/dev/null 2>&1; then
    echo "Error: No se pudo acceder a la base de datos en $DB_PATH" >&2
    exit 1
fi

if [ ! -d "$WEB_REPO/.git" ]; then
    echo "Error: Repo web no encontrado en $WEB_REPO" >&2
    exit 1
fi

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# --- Query 1: Trades individuales (últimos 30 días) ---
trades_json=$(run_sql "$DB_PATH" -json "
    SELECT
        premium,
        fiat_code,
        CAST(ROUND(fiat_amount * 100000000.0 / amount) AS INTEGER) as btc_price,
        STRFTIME('%Y-%m-%dT%H:%M:%SZ', created_at, 'unixepoch') as timestamp
    FROM orders
    WHERE status = 'success'
      AND amount > 0
      AND created_at >= CAST(STRFTIME('%s', 'now', '-30 days') AS INTEGER)
    ORDER BY created_at DESC
")
[ -z "$trades_json" ] && trades_json="[]"

# --- Query 2: Premium medio diario (para el gráfico) ---
# avg_btc_price se restringe a EUR: promediar precios de distintas divisas no significa
# nada (un trade en CUP disparaba la media del día a millones).
daily_json=$(run_sql "$DB_PATH" -json "
    SELECT
        DATE(created_at, 'unixepoch') as date,
        ROUND(AVG(premium), 1) as avg_premium,
        CAST(ROUND(AVG(CASE WHEN fiat_code = 'EUR'
                            THEN fiat_amount * 100000000.0 / amount END)) AS INTEGER) as avg_btc_price,
        COUNT(*) as trades
    FROM orders
    WHERE status = 'success'
      AND amount > 0
      AND created_at >= CAST(STRFTIME('%s', 'now', '-30 days') AS INTEGER)
    GROUP BY DATE(created_at, 'unixepoch')
    ORDER BY date
")
[ -z "$daily_json" ] && daily_json="[]"

# --- Query 3: Stats agregados ---
stats_raw=$(run_sql "$DB_PATH" -separator '|' "
    SELECT
        ROUND(AVG(CASE WHEN created_at >= CAST(STRFTIME('%s','now','-1 day') AS INTEGER) THEN premium END), 1),
        ROUND(AVG(CASE WHEN created_at >= CAST(STRFTIME('%s','now','-7 days') AS INTEGER) THEN premium END), 1),
        ROUND(AVG(premium), 1),
        SUM(CASE WHEN created_at >= CAST(STRFTIME('%s','now','-1 day') AS INTEGER) THEN 1 ELSE 0 END),
        SUM(CASE WHEN created_at >= CAST(STRFTIME('%s','now','-7 days') AS INTEGER) THEN 1 ELSE 0 END),
        COUNT(*)
    FROM orders
    WHERE status = 'success'
      AND amount > 0
      AND created_at >= CAST(STRFTIME('%s', 'now', '-30 days') AS INTEGER)
")

IFS='|' read -r avg_24h avg_7d avg_30d trades_24h trades_7d trades_30d <<< "$stats_raw"

avg_24h="${avg_24h:-null}"
avg_7d="${avg_7d:-null}"
avg_30d="${avg_30d:-null}"
trades_24h="${trades_24h:-0}"
trades_7d="${trades_7d:-0}"
trades_30d="${trades_30d:-0}"

# --- Query 4: Mediana del precio BTC/EUR del último día con trades ---
# Acotado a 30 días como el resto: sin esta ventana el precio se quedaba congelado y la web
# mostraba como actual una cotización de semanas atrás durante las rachas sin trades.
last_price=$(run_sql "$DB_PATH" -separator '|' "
    WITH reciente AS (
      SELECT DATE(created_at, 'unixepoch') AS d,
             CAST(ROUND(fiat_amount * 100000000.0 / amount) AS INTEGER) AS precio,
             fiat_amount * 1.0 / amount AS ratio
      FROM orders
      WHERE status = 'success' AND amount > 0 AND fiat_code = 'EUR'
        AND created_at >= CAST(STRFTIME('%s', 'now', '-30 days') AS INTEGER)
    ),
    last_day AS (SELECT MAX(d) AS d FROM reciente)
    SELECT precio FROM reciente
    WHERE d = (SELECT d FROM last_day)
    ORDER BY ratio
    LIMIT 1 OFFSET (
      SELECT COUNT(*) / 2 FROM reciente WHERE d = (SELECT d FROM last_day)
    )
")
last_price="${last_price:-null}"

# --- Query 5: Métodos de pago (normalizado a categorías) ---
payment_raw=$(run_sql "$DB_PATH" -separator '|' "
    SELECT payment_method FROM orders
    WHERE status = 'success' AND amount > 0
      AND created_at >= CAST(STRFTIME('%s', 'now', '-30 days') AS INTEGER)
      AND LOWER(payment_method) NOT LIKE '%test%'
      AND LOWER(payment_method) NOT LIKE '%prueba%'
      AND LOWER(payment_method) NOT LIKE '%oferta%'
")

normalize_method() {
    local ml
    ml=$(echo "$1" | tr '[:upper:]' '[:lower:]')
    case "$ml" in
        *bizum*)           echo "Bizum" ;;
        *revolut*)         echo "Revolut" ;;
        *halcash*)         echo "HalCash" ;;
        *sepa*)            echo "SEPA Instant" ;;
        *transferencia*)   echo "Transferencia Bancaria" ;;
        *wise*)            echo "Wise" ;;
        *payoneer*)        echo "Payoneer" ;;
        *cash*)            echo "Cash" ;;
        *n26*)             echo "N26" ;;
        *)                 echo "Otros" ;;
    esac
}

payments_json="[]"
if [ -n "$payment_raw" ]; then
    payments_json=$(echo "$payment_raw" | tr ',' '\n' | sed 's/^ *//;s/ *$//' | grep -v '^$' | \
        while IFS= read -r m; do normalize_method "$m"; done | \
        sort | uniq -c | sort -rn | \
        awk '{count=$1; $1=""; sub(/^ /,""); printf "{\"method\":\"%s\",\"count\":%d}\n", $0, count}' | \
        jq -s '.')
    [ -z "$payments_json" ] && payments_json="[]"
fi

# --- Construir JSON final ---
mkdir -p "$WEB_REPO/data"
jq -n \
    --arg updated_at "$NOW" \
    --argjson trades "$trades_json" \
    --argjson daily "$daily_json" \
    --argjson avg_24h "$avg_24h" \
    --argjson avg_7d "$avg_7d" \
    --argjson avg_30d "$avg_30d" \
    --argjson trades_24h "$trades_24h" \
    --argjson trades_7d "$trades_7d" \
    --argjson trades_30d "$trades_30d" \
    --argjson last_btc_price "$last_price" \
    --argjson payments "$payments_json" \
    '{
        updated_at: $updated_at,
        trades: $trades,
        daily: $daily,
        stats: {
            avg_premium_24h: $avg_24h,
            avg_premium_7d: $avg_7d,
            avg_premium_30d: $avg_30d,
            trades_24h: $trades_24h,
            trades_7d: $trades_7d,
            trades_30d: $trades_30d,
            last_btc_price: $last_btc_price
        },
        payment_methods: $payments
    }' > "$WEB_REPO/data/premiums.json"

echo "$(date '+%Y-%m-%d %H:%M:%S') - JSON generado: $trades_30d trades en 30d, BTC/EUR: $last_price"

# --- Push a GitHub ---
cd "$WEB_REPO"
git pull --rebase --quiet 2>/dev/null || true
git add data/premiums.json
if git diff --cached --quiet; then
    echo "Sin cambios, no se hace push"
else
    git commit -m "Update premiums data ($NOW)" --quiet
    # Un push fallido (GitHub caído, sin red) no debe abortar el script bajo 'set -e':
    # el resumen de Telegram se intenta igualmente.
    if git push --quiet 2>&1; then
        echo "Push completado"
    else
        echo "Aviso: push fallido, el JSON local sí se ha generado"
    fi
fi

# --- Resumen en Telegram ---
# Va después del push y fuera de cualquier salida temprana: el resumen debe intentarse
# aunque no haya habido cambios que publicar. Un fallo aquí no invalida el trabajo ya
# hecho, así que no se propaga pese al 'set -e'.
python3 "$SCRIPT_DIR/resumen.py" || echo "Aviso: resumen de Telegram no enviado"
