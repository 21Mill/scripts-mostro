#!/usr/bin/env python3
"""
pago.py — Auditoría del pago de Mostro al comprador de una orden.

    python3 tools/pago.py <order_id>          UUID completo o su principio
    python3 tools/pago.py <order_id> --json   lo mismo, para otro script

Reconstruye todos los intentos del pago de salida en lnd1 y lnd2: hash, preimage, estado,
causa de cada fallo, y para el que salió, la ruta salto a salto con lo que cobró cada nodo y
cuánto de eso se quedó en tus propios nodos. order.sh lo incluye al consultar una orden.

Lee la instantánea de Mostro, LND con macaroons de solo lectura y accounting.db. No escribe
nada. El porqué y los criterios, en lib/auditoria.py.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.auditoria import auditar_pago, texto_auditoria
from lib.contabilidad import abrir, fila_de_orden
from lib.entorno import cargar_env
from lib.lnd import (
    MARGEN_ANTES_SEGUNDOS, VENTANA_PAGO_SEGUNDOS, ErrorLnd, alias_de, cargar_nodos, destino_de,
    hash_de_factura, listar_pagos, pubkey_de,
)

cargar_env()

MOSTRO_DB = os.getenv("MOSTRO_DB_RO", "/var/lib/mostro-snapshot/mostro.db")
ACCOUNTING_DB = Path(__file__).resolve().parent.parent / "accounting" / "accounting.db"

# Hasta que la hold invoice no se liquida no hay pago al comprador que auditar.
ESTADOS_CON_PAGO = ("settled-hold-invoice", "success")


def buscar_orden(texto):
    """La orden cuyo UUID empieza por `texto`. Sale con error si no hay una sola."""
    prefijo = texto.replace("-", "").lower()
    if not prefijo or any(c not in "0123456789abcdef" for c in prefijo):
        sys.exit(f"'{texto}' no es un UUID ni el principio de uno.")
    con = sqlite3.connect(f"file:{MOSTRO_DB}?mode=ro", uri=True, timeout=30)
    try:
        filas = con.execute(
            "SELECT lower(hex(id)), kind, status, amount, fee, buyer_invoice, taken_at, "
            "payment_attempts, failed_payment FROM orders WHERE lower(hex(id)) LIKE ?",
            (prefijo + "%",),
        ).fetchall()
    finally:
        con.close()
    if not filas:
        sys.exit(f"No hay ninguna orden que empiece por {texto}.")
    if len(filas) > 1:
        sys.exit(f"{len(filas)} órdenes empiezan por {texto}: usa más caracteres.")
    id_, kind, status, amount, fee, invoice, taken_at, intentos, fallidos = filas[0]
    return {
        "id": id_, "kind": kind, "status": status, "amount": int(amount or 0), "fee": int(fee or 0),
        "buyer_invoice": invoice or "", "taken_at": int(taken_at or 0),
        "payment_attempts": intentos, "failed_payment": fallidos,
        "destino": destino_de(invoice),
    }


def contar_homonimas(orden):
    """Otras órdenes completadas con el mismo importe neto en la ventana de pago de esta."""
    neto = orden["amount"] - orden["fee"]
    con = sqlite3.connect(f"file:{MOSTRO_DB}?mode=ro", uri=True, timeout=30)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM orders WHERE status IN ('success', 'settled-hold-invoice') "
            "AND lower(hex(id)) != ? AND amount - fee = ? AND taken_at BETWEEN ? AND ?",
            (orden["id"], neto, orden["taken_at"] - VENTANA_PAGO_SEGUNDOS, orden["taken_at"] + VENTANA_PAGO_SEGUNDOS),
        ).fetchone()[0]
    finally:
        con.close()


def contabilidad_de(order_id):
    if not ACCOUNTING_DB.exists():
        return None
    try:
        con = abrir(ACCOUNTING_DB)
    except sqlite3.Error:
        return None
    try:
        return fila_de_orden(con, order_id)
    except sqlite3.Error:
        return None
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="Auditoría del pago de Mostro al comprador de una orden.")
    ap.add_argument("orden", help="UUID de la orden, completo o su principio")
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    args = ap.parse_args()

    orden = buscar_orden(args.orden)
    if orden["status"] not in ESTADOS_CON_PAGO:
        print(f"La orden {orden['id'][:8]} está en '{orden['status']}': todavía no hay pago al comprador.")
        return

    nodos = cargar_nodos(os.environ)
    propias = set()
    for nodo in nodos:
        try:
            propias.add(pubkey_de(nodo))
        except ErrorLnd as e:
            print(f"⚠️  No se pudo leer la identidad de {nodo.nombre}: {e}", file=sys.stderr)

    hash_factura = None
    if orden["destino"] == "bolt11":
        try:
            hash_factura = hash_de_factura(nodos[0], orden["buyer_invoice"])
        except ErrorLnd as e:
            print(f"⚠️  No se pudo decodificar la factura del comprador: {e}", file=sys.stderr)

    desde = orden["taken_at"] - MARGEN_ANTES_SEGUNDOS
    hasta = min(int(time.time()), orden["taken_at"] + VENTANA_PAGO_SEGUNDOS)
    pagos_por_nodo, caidos = {}, []
    for nodo in nodos:
        try:
            pagos_por_nodo[nodo.nombre] = listar_pagos(nodo, desde, hasta, incompletos=True)
        except ErrorLnd as e:
            caidos.append(nodo.nombre)
            print(f"⚠️  {e}", file=sys.stderr)

    # Los alias salen del grafo de lnd1, que es el que ve la red: lnd2 solo habla con él.
    cache = {}

    def alias(pub):
        if pub not in cache:
            cache[pub] = alias_de(nodos[0], pub)
        return cache[pub]

    aud = auditar_pago(
        orden, pagos_por_nodo, propias, alias,
        hash_factura=hash_factura,
        homonimas=contar_homonimas(orden),
        contabilidad=contabilidad_de(orden["id"]),
        nodos_caidos=caidos,
        lnd2_configurado=len(nodos) > 1,
    )

    if args.json:
        aud["orden"] = orden["id"]
        print(json.dumps(aud, ensure_ascii=False, indent=2))
    else:
        print(texto_auditoria(aud, color=sys.stdout.isatty()))


if __name__ == "__main__":
    main()
