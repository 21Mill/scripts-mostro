#!/usr/bin/env bash
# report.sh — Estado del nodo e informe financiero de la instancia Mostro
#
# Arriba, un bloque fijo con la foto del instante: que hay vivo, cuanto dinero
# hay en juego y que esta encallado. Debajo, el informe por periodo de siempre.
# Uso: report.sh [today|week|month|year|all] [YYYY-MM-DD YYYY-MM-DD]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../admin/env.sh"

DB_PATH="$MOSTRO_DB_RO"

# ── Colores ───────────────────────────────────────────────────────────────────
BOLD='\033[1m'; NC='\033[0m'
ORANGE='\033[38;5;214m'; CYAN='\033[0;36m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; RED='\033[0;31m'; DIM='\033[2m'

# ── SQL helper ────────────────────────────────────────────────────────────────
# sql_ro() y el porqué de la instantánea están en admin/env.sh.
sql() { sql_ro "$DB_PATH" "$@"; }

# ── Formateo ──────────────────────────────────────────────────────────────────
fmt_sats() { local n="${1:-0}"; [ "$n" = "NULL" ] || [ -z "$n" ] && n=0; printf "%'d" "$n"; }
fmt_pct()  { local num="${1:-0}" den="${2:-1}"; [ "$den" -eq 0 ] && { echo "0.00"; return; }; awk "BEGIN { printf \"%.2f\", ($num/$den)*100 }"; }

# ── Periodo ───────────────────────────────────────────────────────────────────
PERIODO="${1:-month}"
TS_FROM=""; TS_TO=""; LABEL=""; SHOW_TREND=1

case "$PERIODO" in
    today)
        TS_FROM=$(date -d "today 00:00:00" +%s 2>/dev/null || date -v0H -v0M -v0S +%s)
        TS_TO=$(date +%s); LABEL="Hoy ($(date '+%d/%m/%Y'))"; SHOW_TREND=0 ;;
    week)
        TS_FROM=$(date -d "7 days ago" +%s 2>/dev/null || date -v-7d +%s)
        TS_TO=$(date +%s); LABEL="Últimos 7 días" ;;
    month)
        TS_FROM=$(date -d "30 days ago" +%s 2>/dev/null || date -v-30d +%s)
        TS_TO=$(date +%s); LABEL="Últimos 30 días" ;;
    year)
        TS_FROM=$(date -d "365 days ago" +%s 2>/dev/null || date -v-365d +%s)
        TS_TO=$(date +%s); LABEL="Último año" ;;
    all)
        TS_FROM=0; TS_TO=9999999999; LABEL="Todo el historial" ;;
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9])
        [ -n "${2:-}" ] || { echo "Error: Especifica dos fechas: YYYY-MM-DD YYYY-MM-DD" >&2; exit 1; }
        TS_FROM=$(date -d "${1} 00:00:00" +%s 2>/dev/null || date -jf "%Y-%m-%d %H:%M:%S" "${1} 00:00:00" +%s)
        TS_TO=$(date -d "${2} 23:59:59" +%s 2>/dev/null || date -jf "%Y-%m-%d %H:%M:%S" "${2} 23:59:59" +%s)
        LABEL="Del $1 al $2" ;;
    *)
        echo "Uso: $0 [today|week|month|year|all] o $0 YYYY-MM-DD YYYY-MM-DD" >&2; exit 1 ;;
esac

WHERE="created_at >= $TS_FROM AND created_at <= $TS_TO"
WHERE_OK="$WHERE AND status = 'success' AND amount > 0"

# ── Cabecera ──────────────────────────────────────────────────────────────────
echo ""
printf "${BOLD}${ORANGE}╔══════════════════════════════════════════════════════════════╗${NC}\n"
printf "${BOLD}${ORANGE}║         INFORME FINANCIERO · NOSTROMOSTRO                    ║${NC}\n"
printf "${BOLD}${ORANGE}╚══════════════════════════════════════════════════════════════╝${NC}\n"
printf "  ${DIM}Periodo:${NC} ${BOLD}$LABEL${NC}    ${DIM}Generado:${NC} $(date '+%Y-%m-%d %H:%M:%S')\n"
echo ""

# ── 0. ESTADO DEL NODO AHORA ──────────────────────────────────────────────────
#
# Bloque fijo: el informe de abajo mira un periodo, esto mira el instante. Son
# las preguntas de guardia — que hay vivo, cuanto dinero hay en juego y si algo
# esta encallado — y no se responden con los agregados del periodo.
#
# Todo sale de la instantanea y del log, nunca de la base en uso. Cuidado al
# tocar esto: el script corre con `set -euo pipefail`, asi que cualquier grep
# que pueda no encontrar nada necesita `|| true` o aborta el informe entero.

fmt_edad() {
    local s="${1:-0}"
    [ -z "$s" ] || [ "$s" = "NULL" ] && s=0
    if   [ "$s" -ge 86400 ]; then echo "$((s/86400)) d"
    elif [ "$s" -ge 3600 ];  then echo "$((s/3600)) h"
    elif [ "$s" -ge 60 ];    then echo "$((s/60)) m"
    else echo "${s} s"; fi
}

AVISOS=()

printf "${BOLD}${CYAN}▌ ESTADO DEL NODO AHORA${NC}\n"
echo "  ─────────────────────────────────────────────────────────────"

# Cuanto se ha alejado la instantanea de la realidad. Sin esto no se sabe si lo
# que se esta leyendo es de ahora o de hace un minuto largo.
snap_age=$(( $(date +%s) - $(stat -c %Y "$DB_PATH" 2>/dev/null || echo 0) ))
printf "  ${DIM}Instantánea de hace %s${NC}\n\n" "$(fmt_edad $snap_age)"

# ── Ordenes vivas ─────────────────────────────────────────────────────────────
# La antiguedad de la mas vieja es lo que distingue una orden sana de una
# encallada; sin esa columna la lista no dice nada.
printf "  ${BOLD}Órdenes vivas${NC}\n"
NOW=$(date +%s)
vivas=$(sql "
    SELECT status, COUNT(*), COALESCE(SUM(amount),0),
           MAX($NOW - COALESCE(NULLIF(taken_at,0), created_at))
    FROM orders
    WHERE status IN ('pending','waiting-payment','waiting-buyer-invoice',
                     'active','fiat-sent','settled-hold-invoice','dispute','payment-failed')
    GROUP BY status ORDER BY COUNT(*) DESC" | tr '|' ' ') || true

total_vivas=0
if [ -n "$vivas" ]; then
    while read -r st n sats edad; do
        [ -z "${st:-}" ] && continue
        total_vivas=$(( total_vivas + n ))
        if [ "${sats:-0}" -gt 0 ]; then
            printf "    %-22s %3s  %12s sats   ${DIM}más antigua: %s${NC}\n" \
                "$st" "$n" "$(fmt_sats "$sats")" "$(fmt_edad "$edad")"
        else
            printf "    %-22s %3s  %12s        ${DIM}más antigua: %s${NC}\n" \
                "$st" "$n" "-" "$(fmt_edad "$edad")"
        fi
        # Un escrow liquidado es dinero ya cobrado al vendedor y aun no entregado
        # al comprador: pasada una hora deja de ser un trade en curso.
        if [ "$st" = "settled-hold-invoice" ] && [ "${edad:-0}" -gt 3600 ]; then
            AVISOS+=("$n orden(es) con escrow liquidado sin pagar — la más antigua, $(fmt_edad "$edad")")
        fi
        if [ "$st" = "dispute" ] && [ "${edad:-0}" -gt 604800 ]; then
            AVISOS+=("$n disputa(s) abierta(s) — la más antigua, $(fmt_edad "$edad")")
        fi
    done <<< "$vivas"
else
    printf "    ${DIM}Ninguna${NC}\n"
fi
printf "    ${DIM}%-22s %3s${NC}\n" "total" "$total_vivas"
echo ""

# ── Dinero en juego ───────────────────────────────────────────────────────────
printf "  ${BOLD}Dinero en juego${NC}\n"
IFS='|' read -r m_pagar m_hold m_bonds <<< "$(sql "SELECT
    (SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='settled-hold-invoice'),
    (SELECT COALESCE(SUM(amount),0) FROM orders WHERE status IN ('active','fiat-sent')),
    (SELECT COALESCE(SUM(amount_sats),0) FROM bonds WHERE state='locked')")" || true
printf "    %-38s %12s sats\n" "Escrow liquidado, pendiente de pagar" "$(fmt_sats "${m_pagar:-0}")"
printf "    %-38s %12s sats\n" "Retenido en hold invoice (en curso)"  "$(fmt_sats "${m_hold:-0}")"
printf "    %-38s %12s sats\n" "Fianzas bloqueadas"                   "$(fmt_sats "${m_bonds:-0}")"
echo ""

# ── Pagos ─────────────────────────────────────────────────────────────────────
printf "  ${BOLD}Pagos${NC} ${DIM}(últimas 24 h)${NC}\n"
fallidos=$(sql "SELECT COUNT(*) FROM orders WHERE failed_payment=1
                AND status NOT IN ('success','canceled','expired','canceled-by-admin')") || fallidos=0
printf "    %-38s %12s\n" "Órdenes con pago fallido pendiente" "${fallidos:-0}"

rechazadas="n/d"; colgadas="n/d"; colgadas_n=0
if [ -n "${MOSTRO_LOG:-}" ] && [ -r "$MOSTRO_LOG" ]; then
    CUT=$(date -u -d "24 hours ago" +%Y-%m-%dT%H:%M:%S 2>/dev/null || echo "")
    if [ -n "$CUT" ]; then
        LOG24=$(mktemp); trap 'rm -f "$LOG24"' EXIT
        # Solo la cola: el log pasa de 500 MB y recorrerlo entero cuesta 5 s
        # frente a 1,3. El grep de la marca de tiempo NO es decorativo: sin el,
        # una linea de continuacion como "Edited order: ..." empieza por 'E',
        # que es mayor que '2' al comparar texto, y se cuela en la ventana.
        tail -n 800000 "$MOSTRO_LOG" 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' \
            | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' > "$LOG24.raw" || true
        # Si la cola empieza despues del corte, la ventana estaria truncada y la
        # cifra mentiria por abajo: se recorre el fichero entero.
        primera=$(head -1 "$LOG24.raw" 2>/dev/null | cut -c1-19 || true)
        if [ -n "$primera" ] && [ "$primera" \> "$CUT" ]; then
            sed 's/\x1b\[[0-9;]*m//g' "$MOSTRO_LOG" \
                | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' > "$LOG24.raw" || true
        fi
        awk -v c="$CUT" 'substr($0,1,19) >= c' "$LOG24.raw" > "$LOG24" || true
        rm -f "$LOG24.raw"
        rechazadas=$(grep -c 'invalid_invoice' "$LOG24" || true)
        colgadas_n=$(grep 'keeping payout claim' "$LOG24" 2>/dev/null \
            | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
            | sort -u | wc -l || true)
        colgadas="$colgadas_n"
    fi
fi
printf "    %-38s %12s\n" "Facturas rechazadas" "$rechazadas"
printf "    %-38s %12s\n" "Órdenes con HTLC colgado sin resolver" "$colgadas"
echo ""

[ "${fallidos:-0}" -gt 0 ] && AVISOS+=("${fallidos} orden(es) con el pago al comprador fallido")
[ "${colgadas_n:-0}" -gt 0 ] && AVISOS+=("${colgadas_n} orden(es) con HTLC colgado: no reintentar a mano, riesgo de doble pago")

# ── Relays ────────────────────────────────────────────────────────────────────
# Un relay que deja de aceptar publicaciones no se nota en ningun otro sitio:
# las ordenes se crean bien y simplemente no le llegan a nadie.
printf "  ${BOLD}Relays${NC}\n"
if [ -n "${MOSTRO_LOG:-}" ] && [ -r "$MOSTRO_LOG" ]; then
    ultima=$(grep 'failed: {' "$MOSTRO_LOG" 2>/dev/null | tail -1 | sed 's/\x1b\[[0-9;]*m//g' || true)
    if [ -n "$ultima" ]; then
        hora=$(echo "$ultima" | grep -oE '^[0-9-]+T[0-9:]{8}' | cut -dT -f2 || true)
        n_ok=$(echo "$ultima" | grep -oE 'RelayUrl\("[^"]+"\)' | sort -u | wc -l || true)
        if echo "$ultima" | grep -q 'failed: {}'; then
            printf "    Última publicación: %s · %s relays · ${GREEN}sin fallos${NC}\n" "${hora:-?}" "$n_ok"
        else
            fallidos_relay=$(echo "$ultima" | sed 's/.*failed: //' | grep -oE 'RelayUrl\("[^"]+"\)' | sed 's/RelayUrl("//;s/")//' | tr '\n' ' ' || true)
            printf "    Última publicación: %s · ${RED}fallaron: %s${NC}\n" "${hora:-?}" "${fallidos_relay:-?}"
            AVISOS+=("Relays rechazando publicaciones: ${fallidos_relay}")
        fi
    else
        printf "    ${DIM}Sin publicaciones registradas${NC}\n"
    fi
else
    printf "    ${DIM}Log no accesible${NC}\n"
fi
echo ""

# ── Avisos ────────────────────────────────────────────────────────────────────
if [ ${#AVISOS[@]} -gt 0 ]; then
    printf "  ${BOLD}${YELLOW}⚠ Requiere atención${NC}\n"
    for a in "${AVISOS[@]}"; do printf "    ${YELLOW}·${NC} %s\n" "$a"; done
else
    printf "  ${GREEN}✓ Nada que requiera atención${NC}\n"
fi
echo ""

# ── 1. RESUMEN DE ACTIVIDAD ───────────────────────────────────────────────────
printf "${BOLD}${CYAN}▌ 1. RESUMEN DE ACTIVIDAD${NC}\n"
echo "  ─────────────────────────────────────────────────────────────"

IFS='|' read -r total completadas canceladas expiradas c_admin c_cooper <<< $(sql "
    SELECT COUNT(*),
        SUM(CASE WHEN status='success' THEN 1 ELSE 0 END),
        SUM(CASE WHEN status='canceled' THEN 1 ELSE 0 END),
        SUM(CASE WHEN status='expired' THEN 1 ELSE 0 END),
        SUM(CASE WHEN status='canceled-by-admin' THEN 1 ELSE 0 END),
        SUM(CASE WHEN status='cooperatively-canceled' THEN 1 ELSE 0 END)
    FROM orders WHERE $WHERE")

IFS='|' read -r buys sells <<< $(sql "
    SELECT SUM(CASE WHEN kind='buy' AND status='success' THEN 1 ELSE 0 END),
        SUM(CASE WHEN kind='sell' AND status='success' THEN 1 ELSE 0 END)
    FROM orders WHERE $WHERE")

IFS='|' read -r failed_pay fiat_sent_stuck pending_ord active_ord <<< $(sql "
    SELECT SUM(CASE WHEN failed_payment=1 THEN 1 ELSE 0 END),
        SUM(CASE WHEN status='fiat-sent' THEN 1 ELSE 0 END),
        SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),
        SUM(CASE WHEN status='active' THEN 1 ELSE 0 END)
    FROM orders WHERE $WHERE")

total="${total:-0}"; completadas="${completadas:-0}"; canceladas="${canceladas:-0}"
expiradas="${expiradas:-0}"; c_admin="${c_admin:-0}"; c_cooper="${c_cooper:-0}"
buys="${buys:-0}"; sells="${sells:-0}"; failed_pay="${failed_pay:-0}"
fiat_sent_stuck="${fiat_sent_stuck:-0}"; pending_ord="${pending_ord:-0}"; active_ord="${active_ord:-0}"
tasa_exito=$(fmt_pct "$completadas" "$total")

printf "  ${BOLD}Órdenes totales:${NC}     %s\n" "$(fmt_sats $total)"
printf "  ${GREEN}✓ Completadas:${NC}       %-8s  ${DIM}(buy: %s  |  sell: %s)${NC}\n" "$completadas" "$buys" "$sells"
printf "  ${YELLOW}↩ Canceladas:${NC}        %-8s  ${DIM}(cooperativas: %s  |  por admin: %s)${NC}\n" "$canceladas" "$c_cooper" "$c_admin"
printf "  ${DIM}⏱ Expiradas:${NC}         %s\n" "$expiradas"
[ "$pending_ord" -gt 0 ]     && printf "  ${DIM}📢 En mercado:${NC}        %-8s  ${DIM}(publicadas, esperando tomador)${NC}\n" "$pending_ord"
[ "$active_ord" -gt 0 ]      && printf "  ${YELLOW}⚡ En curso:${NC}          %-8s  ${DIM}(tomador asignado)${NC}\n" "$active_ord"
[ "$fiat_sent_stuck" -gt 0 ] && printf "  ${YELLOW}⚠ Fiat enviado:${NC}      %-8s  ${DIM}(esperando confirmación)${NC}\n" "$fiat_sent_stuck"
[ "$failed_pay" -gt 0 ]      && printf "  ${YELLOW}⚠ Incidencias de pago:${NC}  %s\n" "$failed_pay"
printf "  ${DIM}Tasa de éxito:${NC}       ${BOLD}%s%%${NC}\n" "$tasa_exito"
echo ""

# ── 2. VOLUMEN DE TRADING ─────────────────────────────────────────────────────
printf "${BOLD}${CYAN}▌ 2. VOLUMEN DE TRADING${NC}\n"
echo "  ─────────────────────────────────────────────────────────────"

IFS='|' read -r vol_sats avg_sats med_sats max_sats min_sats <<< $(sql "
    SELECT COALESCE(SUM(amount),0),
        COALESCE(CAST(AVG(amount) AS INTEGER),0),
        COALESCE((SELECT amount FROM orders WHERE $WHERE_OK ORDER BY amount
            LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM orders WHERE $WHERE_OK)),0),
        COALESCE(MAX(amount),0),
        COALESCE(MIN(amount),0)
    FROM orders WHERE $WHERE_OK")

printf "  ${BOLD}Sats totales negociados:${NC}  %s sats\n" "$(fmt_sats ${vol_sats:-0})"
printf "  ${DIM}Trade medio:${NC}              %s sats\n" "$(fmt_sats ${avg_sats:-0})"
printf "  ${DIM}Trade mediano:${NC}            %s sats\n" "$(fmt_sats ${med_sats:-0})"
printf "  ${DIM}Trade mayor:${NC}              %s sats\n" "$(fmt_sats ${max_sats:-0})"
printf "  ${DIM}Trade menor:${NC}              %s sats\n" "$(fmt_sats ${min_sats:-0})"
echo ""
printf "  ${BOLD}Volumen por moneda fiat:${NC}\n"
sql "SELECT fiat_code, COUNT(*), SUM(fiat_amount), SUM(amount)
    FROM orders WHERE $WHERE_OK
    GROUP BY fiat_code ORDER BY SUM(amount) DESC" | \
while IFS='|' read -r fcode n tfiat tsats; do
    printf "    ${BOLD}%-5s${NC}  %s trades   %s %s   %s sats\n" \
        "$fcode" "$n" "$(fmt_sats $tfiat)" "$fcode" "$(fmt_sats $tsats)"
done
echo ""

# ── 3. FLUJO DE SATS (LIGHTNING) ──────────────────────────────────────────────
printf "${BOLD}${CYAN}▌ 3. FLUJO DE SATS (LIGHTNING)${NC}\n"
echo "  ─────────────────────────────────────────────────────────────"

IFS='|' read -r recv_vendor enviados comision <<< $(sql "
    SELECT COALESCE(SUM(amount+fee),0), COALESCE(SUM(amount-fee),0), COALESCE(SUM(fee*2),0)
    FROM orders WHERE $WHERE_OK")
recv_vendor="${recv_vendor:-0}"; enviados="${enviados:-0}"; comision="${comision:-0}"

printf "  ${GREEN}↓ Recibido del vendedor:${NC}   %s sats\n" "$(fmt_sats $recv_vendor)"
printf "  ${RED}↑ Enviado al comprador:${NC}    %s sats\n" "$(fmt_sats $enviados)"
printf "  ${DIM}  Comisión retenida:${NC}        %s sats\n" "$(fmt_sats $comision)"
echo ""

# ── 4. INGRESOS Y BENEFICIO ───────────────────────────────────────────────────
printf "${BOLD}${CYAN}▌ 4. INGRESOS Y BENEFICIO${NC}\n"
echo "  ─────────────────────────────────────────────────────────────"

IFS='|' read -r fee_total dev_total dev_paid dev_pending <<< $(sql "
    SELECT COALESCE(SUM(fee)*2,0), COALESCE(SUM(dev_fee),0),
        COALESCE(SUM(CASE WHEN dev_fee_paid=1 THEN dev_fee ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN dev_fee_paid=0 THEN dev_fee ELSE 0 END),0)
    FROM orders WHERE $WHERE_OK")

fee_total="${fee_total:-0}"; dev_total="${dev_total:-0}"
dev_paid="${dev_paid:-0}"; dev_pending="${dev_pending:-0}"
fee_operador=$((fee_total - dev_total))

precio_eur=$(sql "
    SELECT CAST(ROUND(fiat_amount * 100000000.0 / amount) AS INTEGER)
    FROM orders WHERE $WHERE_OK AND fiat_code = 'EUR'
    ORDER BY fiat_amount * 1.0 / amount
    LIMIT 1 OFFSET (SELECT COUNT(*)/2 FROM orders WHERE $WHERE_OK AND fiat_code='EUR')")
precio_eur="${precio_eur:-0}"

eur_suffix() {
    local sats="$1"
    [ "$precio_eur" -gt 0 ] 2>/dev/null || { echo ""; return; }
    awk "BEGIN { printf \"  \033[2m≈ %.2f EUR\033[0m\", ($sats / 100000000) * $precio_eur }"
}

printf "  ${BOLD}Fee total cobrado:${NC}         %s sats%b\n" "$(fmt_sats $fee_total)" "$(eur_suffix $fee_total)"
printf "  ${DIM}  → Tu parte (70%%):${NC}        %s sats\n" "$(fmt_sats $fee_operador)"
printf "  ${DIM}  → Dev fee (30%%):${NC}         %s sats\n" "$(fmt_sats $dev_total)"
printf "  ${DIM}    · Pagado:${NC}               %s sats\n" "$(fmt_sats $dev_paid)"
[ "$dev_pending" -gt 0 ] && \
printf "  ${YELLOW}    · Pendiente:${NC}            %s sats\n" "$(fmt_sats $dev_pending)"
echo "  ─────────────────────────────────────────────────────────────"
printf "  ${BOLD}${GREEN}BENEFICIO NETO:${NC}            ${BOLD}%s sats%b${NC}\n" "$(fmt_sats $fee_operador)" "$(eur_suffix $fee_operador)"
echo ""

# ── 5. DISPUTAS ───────────────────────────────────────────────────────────────
printf "${BOLD}${CYAN}▌ 5. DISPUTAS${NC}\n"
echo "  ─────────────────────────────────────────────────────────────"

disp_total=$(sql "SELECT COUNT(*) FROM disputes WHERE created_at >= $TS_FROM AND created_at <= $TS_TO")
disp_total="${disp_total:-0}"
tasa_disp=$(fmt_pct "$disp_total" "$completadas")

printf "  Disputas abiertas:     %s\n" "$disp_total"
printf "  Tasa de disputa:       ${BOLD}%s%%${NC} de los trades completados\n" "$tasa_disp"
echo ""

# ── 6. TENDENCIA DIARIA ───────────────────────────────────────────────────────
if [ "$SHOW_TREND" -eq 1 ]; then
    printf "${BOLD}${CYAN}▌ 6. TENDENCIA DIARIA${NC}\n"
    echo "  ─────────────────────────────────────────────────────────────"

    # Tabla
    printf "  ${DIM}%-12s  %-8s  %-18s  %-10s${NC}\n" "Fecha" "Trades" "Volumen (sats)" "Fee (sats)"
    sql "SELECT DATE(created_at,'unixepoch'), COUNT(*),
            COALESCE(SUM(amount),0), COALESCE(SUM(fee)*2,0)
        FROM orders WHERE $WHERE_OK
        GROUP BY DATE(created_at,'unixepoch') ORDER BY 1" | \
    while IFS='|' read -r dia trades vol fee_dia; do
        printf "  %-12s  %-8s  %-18s  %s\n" "$dia" "$trades" "$(fmt_sats $vol)" "$(fmt_sats $fee_dia)"
    done
    echo ""

    # Gráfico de barras: volumen diario
    printf "  ${DIM}Volumen diario (sats)${NC}\n"
    echo ""
    BAR_WIDTH=36
    daily_data=$(sql "
        SELECT DATE(created_at,'unixepoch'), COALESCE(SUM(amount),0), COUNT(*)
        FROM orders WHERE $WHERE_OK
        GROUP BY DATE(created_at,'unixepoch') ORDER BY 1")
    max_vol=$(echo "$daily_data" | cut -d'|' -f2 | sort -n | tail -1)
    max_vol="${max_vol:-1}"; [ "$max_vol" -eq 0 ] && max_vol=1
    echo "$daily_data" | while IFS='|' read -r dia vol ntrades; do
        bar_len=$(awk "BEGIN{x=int(($vol/$max_vol)*$BAR_WIDTH); print (x<1)?1:x}")
        bar=$(printf '█%.0s' $(seq 1 "$bar_len"))
        printf "  ${DIM}%s${NC}  ${ORANGE}%-36s${NC}  ${DIM}%s sats (%s t)${NC}\n" \
            "$dia" "$bar" "$(fmt_sats $vol)" "$ntrades"
    done
    echo ""
fi

# ── Pie ───────────────────────────────────────────────────────────────────────
printf "  ${DIM}Instancia: NostroMostro  |  DB: $DB_PATH${NC}\n"
echo ""
