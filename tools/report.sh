#!/usr/bin/env bash
# report.sh — Informe financiero de actividad de la instancia Mostro
# Uso: report.sh [today|week|month|year|all] [YYYY-MM-DD YYYY-MM-DD]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../admin/env.sh"

DB_PATH="${MOSTRO_DB:-/data/mostro/mostro.db}"
DB_USER="${MOSTRO_USER:-mostro}"

# ── Colores ───────────────────────────────────────────────────────────────────
BOLD='\033[1m'; NC='\033[0m'
ORANGE='\033[38;5;214m'; CYAN='\033[0;36m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; RED='\033[0;31m'; DIM='\033[2m'

# ── SQL helper ────────────────────────────────────────────────────────────────
sql() { sqlite3 "$DB_PATH" "$@" 2>/dev/null || sudo -u "$DB_USER" sqlite3 "$DB_PATH" "$@" 2>/dev/null; }

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
