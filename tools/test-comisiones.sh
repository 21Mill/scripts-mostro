#!/usr/bin/env bash
# test-comisiones.sh — Comprueba la sección Comisiones de order.sh.
#
# Ejecutar: bash tools/test-comisiones.sh [ruta/a/order.sh]
#
# Sin framework, como el resto: extrae de order.sh las funciones que pintan la sección y las
# prueba sin colores y contra una accounting.db de mentira. Nada toca LND ni la base real.
# Qué se fija: la fee real es el doble de la guardada, la dev fee no se suma a ella, el
# routing sale en sats enteros y de la contabilidad, y lo que no se sabe no se da por 0.
set -u
NEW="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/order.sh}"; TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/tools" "$TMP/accounting"; SCRIPT_DIR="$TMP/tools"
BOLD=''; NC=''; RED=''; GREEN=''; DIM=''

# sql_ro con el módulo sqlite3 de Python: el mismo motor, y el test no depende del binario.
sql_ro() {
    local db="$1"; shift
    python3 - "$db" "${@: -1}" <<'PY'
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    for fila in con.execute(sys.argv[2]):
        print("|".join("" if v is None else str(v) for v in fila))
except sqlite3.Error:
    pass
PY
}

# format_sats y las funciones nuevas, tal como están en order.sh
eval "$(sed -n '/^format_sats() {/,/^}/p' "$NEW")"
eval "$(sed -n '/^msat_a_sats() {/,/^}/p; /^sats_o_cero() {/,/^}/p; /^mostrar_comisiones() {/,/^}/p' "$NEW")"

python3 - "$TMP/accounting/accounting.db" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.executescript("""
CREATE TABLE earnings (order_id TEXT PRIMARY KEY, routing_buyer INT, routing_devs INT,
  routing_buyer_msat INT, routing_buyer_interno_msat INT, routing_devs_msat INT,
  routing_devs_interno_msat INT, net_profit_msat INT);
-- a1: la orden real, pago al comprador conocido y dev fee sin pagar
INSERT INTO earnings VALUES ('a1', NULL, NULL, 150005, 0, NULL, NULL, NULL);
-- a2: cerrada
INSERT INTO earnings VALUES ('a2', NULL, NULL, 150005, 0, 1013, 0, 479982);
-- a5: routing de verdad a 0
INSERT INTO earnings VALUES ('a5', NULL, NULL, 0, 0, 0, 0, 631000);
-- a6: parte del coste se queda en tus nodos
INSERT INTO earnings VALUES ('a6', NULL, NULL, 2500, 1500, 0, 0, 100000);
-- a7: neto negativo
INSERT INTO earnings VALUES ('a7', NULL, NULL, 1000, 0, 0, 0, -2500);
-- a9: sin dev fee
INSERT INTO earnings VALUES ('a9', NULL, NULL, 1000, 0, 0, 0, 600000);
-- a8: fila anterior a las columnas en msat (sats enteros)
INSERT INTO earnings VALUES ('a8', 7, 1, NULL, NULL, NULL, NULL, NULL);
""")
con.commit()
PY

fallos=0
comprobar() {  # descripcion salida fragmento
    if grep -qF -- "$3" <<<"$2"; then echo "  ✅ $1"
    else echo "  ❌ $1"; echo "       falta: $3"; echo "$2" | sed 's/^/       | /'; fallos=$((fallos+1)); fi
}
no_debe() {
    if grep -qF -- "$3" <<<"$2"; then echo "  ❌ $1"; echo "       sobra: $3"; fallos=$((fallos+1))
    else echo "  ✅ $1"; fi
}

echo "orden real: pago al comprador conocido, dev fee sin pagar"
o=$(mostrar_comisiones a1 451 271 0 0 "")
comprobar "fee real = el doble de lo guardado" "$o" "Fee de Mostro:   902 sats  (451 sats de cada parte)"
comprobar "dev fee con su porcentaje y su estado" "$o" "Dev fee:         271 sats  (30 % de la fee, pendiente de pago)"
comprobar "routing sin decimales" "$o" "Routing:         150 sats"
no_debe "y sin la coma de los msat" "$o" "150,005"
comprobar "avisa de que falta el pago de la dev fee" "$o" "el de la dev fee aún no existe"
comprobar "neto provisional redondeado" "$o" "Neto estimado:   ≈ 481 sats  (provisional"
no_debe "ya no hay un Total que no significa nada" "$o" "Total:"

echo "operación cerrada y dev fee pagada"
o=$(mostrar_comisiones a2 451 271 1 0 "abc123")
comprobar "dev fee pagada" "$o" "(30 % de la fee, pagada)"
comprobar "routing de comprador y dev fee juntos, redondeado" "$o" "Routing:         151 sats  (pago al comprador y dev fee, según LND)"
comprobar "neto exacto de la contabilidad" "$o" "Neto estimado:   ≈ 480 sats"
no_debe "sin la nota de provisional" "$o" "provisional"
comprobar "el hash de la dev fee se conserva" "$o" "Dev hash:        abc123"

echo "sin datos de contabilidad"
o=$(mostrar_comisiones zz 451 271 0 0 "")
comprobar "lo dice en vez de poner 0" "$o" "sin datos: la contabilidad aún no lo ha calculado"
no_debe "y no inventa un neto" "$o" "Neto estimado"
o=$(mostrar_comisiones a8 451 271 0 0 "")
comprobar "una fila antigua usa sus sats enteros" "$o" "Routing:         8 sats"
o=$(mostrar_comisiones zz 451 271 0 12 "")
comprobar "sin fila, usa routing_fee de Mostro si lo trae" "$o" "Routing:         12 sats  (según Mostro)"

echo "casos límite"
o=$(mostrar_comisiones a5 451 271 1 0 "")
comprobar "un routing de verdad a 0 se escribe" "$o" "Routing:         0 sats"
no_debe "sin coste interno no hay línea" "$o" "Entre tus nodos"
o=$(mostrar_comisiones a6 451 271 1 0 "")
comprobar "lo que cobran tus nodos sale aparte" "$o" "Entre tus nodos: 2 sats"
comprobar "y el routing es solo lo que cobra la red" "$o" "Routing:         1 sats"
o=$(mostrar_comisiones a7 451 271 1 0 "")
comprobar "un neto negativo redondea como la contabilidad (-2500 msat = -2)" "$o" "≈ -2 sats"
o=$(mostrar_comisiones zz 0 0 0 5 "")
no_debe "sin fee no hay línea de fee ni de neto" "$o" "Fee de Mostro"
comprobar "pero el routing de Mostro sí" "$o" "Routing:         5 sats"
o=$(mostrar_comisiones a9 451 0 0 0 "")
no_debe "sin dev fee no hay línea de dev fee" "$o" "Dev fee:"
comprobar "y el routing no habla de la dev fee" "$o" "Routing:         1 sats  (pago al comprador, según LND)"

echo
if [ "$fallos" -eq 0 ]; then echo "Todo en verde"; else echo "$fallos fallo(s)"; exit 1; fi
