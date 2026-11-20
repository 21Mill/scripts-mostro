#!/usr/bin/env bash
# test-order-log.sh — Comprueba lo que order.sh lee del log de mostrod.
#
# Ejecutar: bash tools/test-order-log.sh [ruta/a/order.sh]
#
# Sin framework, como el resto: extrae de order.sh las funciones que leen el log y las prueba
# con logs de mentira, sin tocar el real. Qué se fija: una orden se encuentra aunque el log
# se haya rotado, en orden cronológico y sin los códigos de color, un fichero que no puede
# contenerla no se lee, y la nota de cada valoración sale del log y se atribuye a quien la puso.
set -u
NEW="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/order.sh}"; TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
BOLD=''; NC=''; RED=''; GREEN=''; DIM=''
eval "$(sed -n '/^orden_log() {/,/^}/p; /^valor_valoracion() {/,/^}/p; /^linea_valoracion() {/,/^}/p' "$NEW")"

fallos=0
comprobar() {  # descripcion salida fragmento
    if grep -qF -- "$3" <<<"$2"; then echo "  ✅ $1"
    else echo "  ❌ $1"; echo "       falta: $3"; echo "$2" | sed 's/^/       | /'; fallos=$((fallos+1)); fi
}
no_debe() {
    if grep -qF -- "$3" <<<"$2"; then echo "  ❌ $1"; echo "       sobra: $3"; fallos=$((fallos+1))
    else echo "  ✅ $1"; fi
}

ORDEN="97341899-7cda-4cfe-aef9-a82cbaf256ab"
MOSTRO_LOG="$TMP/mostro.log"
ESC=$(printf '\033')

# Tres ficheros como los deja logrotate. El más antiguo, comprimido.
printf '%s\n' "2026-10-20T10:00:00Z ${ESC}[32m INFO${ESC}[0m orden $ORDEN creada" "2026-10-20T10:01:00Z otra orden distinta" > "$TMP/viejo.txt"
gzip -c "$TMP/viejo.txt" > "$MOSTRO_LOG.2.gz"
printf '%s\n' "2026-11-05T10:00:00Z orden $ORDEN en .1" "2026-11-05T10:01:00Z otra orden distinta" > "$MOSTRO_LOG.1"
printf '%s\n' "2026-11-09T10:00:00Z orden $ORDEN en el actual" > "$MOSTRO_LOG"
touch -d '2026-10-27 00:00:00' "$MOSTRO_LOG.2.gz"
touch -d '2026-11-09 10:04:00' "$MOSTRO_LOG.1"
touch -d '2026-11-09 21:00:00' "$MOSTRO_LOG"
epoch() { date -d "$1" +%s; }

echo "orden repartida entre los tres ficheros"
o=$(orden_log "$ORDEN" "$(epoch '2026-10-20 09:00:00')")
comprobar "encuentra la parte del log comprimido" "$o" "creada"
comprobar "la del log rotado" "$o" "en .1"
comprobar "y la del actual" "$o" "en el actual"
comprobar "en orden cronológico" "$(grep -n . <<<"$o" | sed 's/^\([0-9]*\):.*\(creada\|en \.1\|en el actual\).*/\1 \2/')" $'1 creada\n2 en .1\n3 en el actual'
no_debe "sin los códigos de color" "$o" "$ESC"
no_debe "y solo las líneas de esa orden" "$o" "otra orden distinta"

echo "un fichero que no puede contener la orden no se lee"
o=$(orden_log "$ORDEN" "$(epoch '2026-11-01 00:00:00')")
no_debe "una orden creada después de que se cerrara el .2.gz no lo mira" "$o" "creada"
comprobar "pero sí el rotado y el actual" "$o" "en .1"
o=$(orden_log "$ORDEN" "$(epoch '2026-11-09 12:00:00')")
no_debe "una orden de hoy tampoco mira el rotado" "$o" "en .1"
comprobar "solo el actual" "$o" "en el actual"
o=$(orden_log "$ORDEN")
comprobar "sin decir desde cuándo, mira todos" "$o" "creada"

echo "casos límite"
rm -f "$MOSTRO_LOG.2.gz" "$MOSTRO_LOG.1"
o=$(orden_log "$ORDEN" 0)
comprobar "sin logs rotados funciona con el actual" "$o" "en el actual"
o=$(orden_log "no-existe" 0)
comprobar "una orden que no está no devuelve nada" "${o:-vacío}" "vacío"
MOSTRO_LOG="$TMP/no-hay-log"
o=$(orden_log "$ORDEN" 0)
comprobar "sin log no falla" "${o:-vacío}" "vacío"

echo
echo "valoraciones: la nota de cada parte"
PB=$(printf 'b2%.0s' {1..32}); PS=$(printf 'd4%.0s' {1..32})
# El formato real del log: el JSON del mensaje va escapado dentro de una cadena.
rate() { printf '%s\n' "2026-11-09T18:50:44Z  INFO mostrod::util: Sending message, Event ID: aa to $1 with payload: \"{\\\"order\\\":{\\\"id\\\":\\\"$ORDEN\\\",\\\"action\\\":\\\"$2\\\",\\\"payload\\\":$3}}\""; }
LOG="$(rate "$PB" rate-received '{\"rating_user\":5}')
$(rate "$PS" rate-received '{\"rating_user\":3}')
$(rate "$PB" rate null)
$(rate "$PS" new-order null)"
comprobar "la nota del comprador" "$(valor_valoracion "$LOG" "$PB")" "5"
comprobar "la del vendedor, que es otra" "$(valor_valoracion "$LOG" "$PS")" "3"
o=$(valor_valoracion "$LOG" "$(printf 'f6%.0s' {1..32})"); comprobar "una clave que no valoró no devuelve nada" "${o:-vacío}" "vacío"
o=$(valor_valoracion "" "$PB"); comprobar "sin log no devuelve nada" "${o:-vacío}" "vacío"
o=$(valor_valoracion "$LOG" ""); comprobar "sin clave tampoco" "${o:-vacío}" "vacío"
comprobar "con una valoración repetida, la última" "$(valor_valoracion "$LOG
$(rate "$PB" rate-received '{\"rating_user\":2}')" "$PB")" "2"
o=$(valor_valoracion "$(rate "$PB" rate '{\"rating_user\":4}')" "$PB"); comprobar "otras acciones, aunque lleven rating_user, no cuentan" "${o:-vacío}" "vacío"

echo "valoraciones: la línea"
o=$(linea_valoracion "Comprador valoró:" 1 5 vendedor)
comprobar "con nota" "$o" "Comprador valoró: ✅ 5 ⭐  (al vendedor)"
o=$(linea_valoracion "Vendedor valoró: " 1 "" comprador)
comprobar "valoró pero la nota no está en el log" "$o" "Vendedor valoró:  ✅ (la nota no consta en el log)"
o=$(linea_valoracion "Comprador valoró:" 0 "" vendedor)
comprobar "no valoró" "$o" "Comprador valoró: no"
o=$(linea_valoracion "Comprador valoró:" "" "" vendedor)
comprobar "sin dato, también no" "$o" "Comprador valoró: no"
no_debe "el flag no se imprime como si fuera la nota" "$(linea_valoracion x 1 "" vendedor)" "⭐"

echo
if [ "$fallos" -eq 0 ]; then echo "Todo en verde"; else echo "$fallos fallo(s)"; exit 1; fi
