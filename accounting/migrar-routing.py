"""
migrar-routing.py — Recalcula con datos de LND el routing de las operaciones ya contabilizadas.

Las filas antiguas se calcularon con las reglas anteriores: solo lnd1, redondeo a sats y 0
cuando no se encontraba el pago. Este script las vuelve a resolver con las nuevas (los dos
nodos, msat, Lightning Address por importe) y rellena las columnas nuevas.

    python3 migrar-routing.py             simulación: no toca nada y enseña el efecto por mes
    python3 migrar-routing.py --aplicar   hace antes una copia de accounting.db y escribe

Antes de aplicar, parar el servicio (sudo systemctl stop mostro-accounting) y arrancarlo
después: escribiría a la vez en la misma base.

Una fila que no se pueda resolver y tenga menos de 30 días queda 'pendiente' (el servicio
la reintenta). Una de más de 30 días conserva los valores antiguos, marcada 'sin_datos':
borrar la historia por no poder verificarla sería peor que dejarla con su aviso.

Es repetible: cada ejecución parte de cero, sin depender de lo que dejó la anterior.
"""

import argparse
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import accounting
from lib.contabilidad import consultar, rango_mes
from lib.formato import formato_sats
from lib.ganancias import guardar_resolucion, migrar_esquema
from lib.lnd import MARGEN_ANTES_SEGUNDOS, cargar_nodos, resolver_orden

SEMANA = 7 * 86400


def meses_con_datos(con):
    minimo, maximo = con.execute("SELECT MIN(completed_at), MAX(completed_at) FROM earnings").fetchone()
    if minimo is None:
        return []
    desde = time.localtime(minimo)
    hasta = time.localtime(maximo)
    anio, mes = desde.tm_year, desde.tm_mon
    meses = []
    while (anio, mes) <= (hasta.tm_year, hasta.tm_mon):
        meses.append((anio, mes))
        anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return meses


def resumen_por_mes(con):
    return {m: consultar(con, *rango_mes(*m)) for m in meses_con_datos(con)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true", help="escribir en accounting.db (con copia previa)")
    args = ap.parse_args()

    nodos = cargar_nodos(os.environ)
    if len(nodos) < 2:
        sys.exit("lnd2 no está configurado (LND2_TLSCERT y LND2_MACAROON en el .env): sin él, "
                 "los pagos de las últimas semanas saldrían como no localizados.")
    propias = accounting.pubkeys_propias(nodos)
    if len(propias) < len(nodos):
        sys.exit("Algún nodo no responde; no se calcula nada a medias.")

    origen = sqlite3.connect(f"file:{accounting.ACCOUNTING_DB}?mode=ro", uri=True)
    antes = resumen_por_mes(origen)

    ahora = int(time.time())
    ordenes = {o["id"]: o for o in accounting.leer_ordenes()}
    filas = origen.execute("SELECT order_id, completed_at FROM earnings ORDER BY completed_at").fetchall()

    # Primero se resuelve todo en memoria: si un nodo falla a mitad, no se ha escrito nada.
    lotes = {}
    sin_orden = 0
    for order_id, completada in filas:
        if order_id in ordenes:
            lotes.setdefault(completada // SEMANA, []).append(ordenes[order_id])
        else:
            sin_orden += 1

    resultados = []
    usados = set()
    for semana in sorted(lotes):
        lote = lotes[semana]
        for orden in lote:
            accounting.preparar(orden, nodos[0])
        desde = min(o["taken_at"] for o in lote) - MARGEN_ANTES_SEGUNDOS
        hasta = min(ahora, max(o["taken_at"] for o in lote) + accounting.MAX_REINTENTO_DIAS * 86400)
        pagos, caidos = accounting.cargar_pagos(nodos, desde, hasta)
        if caidos:
            sys.exit(f"{', '.join(caidos)} no respondió; no se ha escrito nada.")
        por_hash = {p["payment_hash"]: p for p in pagos}
        for orden in sorted(lote, key=lambda o: o["taken_at"]):
            res = resolver_orden(orden, por_hash, pagos, propias, usados)
            if res["hash_comprador"]:
                usados.add(res["hash_comprador"])
            resultados.append((orden, res))
        print(f"  semana {time.strftime('%Y-%m-%d', time.localtime(semana * SEMANA))}: "
              f"{len(lote)} órdenes, {len(pagos)} pagos")

    if args.aplicar:
        copia = accounting.ACCOUNTING_DB.with_name(
            f"accounting.db.pre-routing-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        destino = sqlite3.connect(copia)
        origen.backup(destino)
        destino.close()
        print(f"\n💾 Copia de seguridad: {copia}")
        con = sqlite3.connect(accounting.ACCOUNTING_DB)
    else:
        con = sqlite3.connect(":memory:")
        origen.backup(con)
    migrar_esquema(con)

    estados = Counter()
    for orden, res in resultados:
        vieja_y_sin_resolver = (
            res["estado"] != "ok"
            and ahora - orden["taken_at"] >= accounting.MAX_REINTENTO_DIAS * 86400
        )
        if vieja_y_sin_resolver:
            con.execute(
                "UPDATE earnings SET routing_estado = 'sin_datos', routing_motivo = ?, "
                "routing_revisado_at = ? WHERE order_id = ?",
                (res["motivo"], ahora, orden["id"]),
            )
            con.commit()
            estados["sin_datos"] += 1
        else:
            guardar_resolucion(con, orden, res, ahora)
            estados[res["estado"]] += 1

    despues = resumen_por_mes(con)

    print("\nEfecto por mes (sats)\n")
    print(f"{'mes':8} {'ops':>5} {'neto antes':>12} {'neto ahora':>12} {'routing antes':>14} "
          f"{'routing ahora':>14} {'interno':>8} {'sin resolver':>13}")
    for mes in sorted(despues):
        a, d = antes.get(mes, {}), despues[mes]
        print(f"{mes[0]}-{mes[1]:02d}  {d['operaciones']:>5} {formato_sats(a.get('neto', 0)):>12} "
              f"{formato_sats(d['neto']):>12} {formato_sats(a.get('routing', 0)):>14} "
              f"{formato_sats(d['routing']):>14} {formato_sats(d['routing_interno']):>8} "
              f"{d['pendientes']:>13}")

    print("\nResultado:", ", ".join(f"{k}: {v}" for k, v in sorted(estados.items())) or "nada que hacer")
    if sin_orden:
        print(f"{sin_orden} fila(s) sin su orden en la instantánea de Mostro: no se han tocado")
    if args.aplicar:
        print("\n✅ Aplicado. Arranca de nuevo mostro-accounting.")
    else:
        print("\nSimulación: no se ha modificado nada. Para aplicarlo: --aplicar")
    con.close()
    origen.close()


if __name__ == "__main__":
    main()
