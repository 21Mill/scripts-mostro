"""
accounting.py — Contabilidad de ganancias de NostroMostro.

Sondea la instantánea de mostro.db cada 60s buscando órdenes completadas (status='success'),
averigua en LND lo que costó pagar al comprador y la comisión de desarrollo, guarda el
beneficio en accounting.db (SQLite) y notifica al operador por Telegram privado.

Beneficio = (fee × 2) - dev_fee - routing que cobró la red (comprador + devs)

Mostro paga desde lnd2 y enruta por lnd1, así que se consultan los dos (lib/lnd.py). Lo que
cobran tus propios nodos entre sí se guarda aparte y no resta: no sale de tu bolsillo.

Lo que LND aún no permite averiguar —un pago que no aparece, una comisión de desarrollo
sin pagar— queda 'pendiente' y se reintenta cada 10 minutos durante 30 días. Antes se daba
por 0, y un 0 inventado no se distingue de uno real.
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.entorno import cargar_env
from lib.formato import formato_msat, formato_sats
from lib.ganancias import (
    caducar_pendientes, guardar_resolucion, hash_asignado, hashes_usados, ids_a_reintentar,
    ids_procesados, migrar_esquema, neto_provisional_msat, redondear_sats, totales,
)
from lib.lnd import (
    MARGEN_ANTES_SEGUNDOS, ErrorLnd, cargar_nodos, destino_de, hash_de_factura, listar_pagos,
    pubkey_de, resolver_orden,
)
from lib.telegram import enviar

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

cargar_env()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_MONITOR_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_MONITOR_CHAT_ID")
# Instantánea de solo lectura publicada por mostro-snapshot.timer: leerla no requiere
# privilegios, así que se pudo retirar la regla sudo que daba shell como mostro.
MOSTRO_DB = os.getenv("MOSTRO_DB_RO", "/var/lib/mostro-snapshot/mostro.db")

ACCOUNTING_DB = Path(__file__).parent / "accounting.db"
POLL_INTERVAL = 60  # segundos
REINTENTO_SEGUNDOS = 600  # cada cuánto se revisan las pendientes
MAX_REINTENTO_DIAS = 30  # pasado esto, una pendiente se da por perdida

# ---------------------------------------------------------------------------
# DB contabilidad
# ---------------------------------------------------------------------------

def init_db():
    con = sqlite3.connect(ACCOUNTING_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS earnings (
            order_id      TEXT PRIMARY KEY,
            completed_at  INTEGER,
            amount        INTEGER,
            fiat_code     TEXT,
            fiat_amount   INTEGER,
            fee           INTEGER,
            dev_fee       INTEGER,
            routing_buyer INTEGER,
            routing_devs  INTEGER,
            net_profit    INTEGER,
            notified_at   INTEGER
        )
    """)
    con.commit()
    migrar_esquema(con)
    return con

# ---------------------------------------------------------------------------
# Mostro DB
# ---------------------------------------------------------------------------

def leer_ordenes():
    """Todas las órdenes completadas, como diccionarios."""
    # id es un BLOB binario en Mostro — usamos lower(hex(id)) para obtener el UUID legible
    con = sqlite3.connect(f"file:{MOSTRO_DB}?mode=ro", uri=True, timeout=30)
    try:
        filas = con.execute(
            "SELECT lower(hex(id)), amount, fee, dev_fee, dev_fee_payment_hash, "
            "buyer_invoice, fiat_code, fiat_amount, taken_at "
            "FROM orders WHERE status='success'"
        ).fetchall()
    finally:
        con.close()

    ordenes = []
    for id_, amount, fee, dev_fee, dev_hash, factura, fiat_code, fiat_amount, taken_at in filas:
        ordenes.append({
            "id": id_,
            "amount": int(amount or 0),
            "fee": int(fee or 0),
            "dev_fee": int(dev_fee or 0),
            "dev_fee_hash": dev_hash or "",
            "buyer_invoice": factura or "",
            "fiat_code": fiat_code,
            "fiat_amount": int(fiat_amount or 0),
            "taken_at": int(taken_at or 0),
            "destino": destino_de(factura),
            "hash_comprador": None,
        })
    return ordenes

# ---------------------------------------------------------------------------
# LND
# ---------------------------------------------------------------------------

def preparar(orden, nodo):
    """Si el comprador dio una factura, averigua su payment_hash. Si no se puede, queda None
    y la orden se reintenta: un fallo de lncli no es motivo para buscarla por importe."""
    if orden["destino"] != "bolt11":
        return
    try:
        orden["hash_comprador"] = hash_de_factura(nodo, orden["buyer_invoice"])
    except ErrorLnd as e:
        print(f"⚠️  No se pudo decodificar la factura de {orden['id'][:8]}: {e}")


def cargar_pagos(nodos, desde, hasta):
    """Pagos completados de todos los nodos en la ventana, y los nodos que no respondieron."""
    pagos = []
    caidos = []
    for nodo in nodos:
        try:
            pagos += listar_pagos(nodo, desde, hasta)
        except ErrorLnd as e:
            caidos.append(nodo.nombre)
            print(f"⚠️  {e}")
    return pagos, caidos


def pubkeys_propias(nodos):
    propias = set()
    for nodo in nodos:
        try:
            propias.add(pubkey_de(nodo))
        except ErrorLnd as e:
            print(f"⚠️  No se pudo leer la identidad de {nodo.nombre}: {e}")
    return propias

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️  TELEGRAM_TOKEN o CHAT_ID no configurados")
        return False
    return enviar(TELEGRAM_TOKEN, CHAT_ID, msg, timeout=10, reintentos=1) is not None


def _externo(par):
    return None if par is None else par[0] - par[1]


def build_message(orden, res, neto_msat, total_msat, total_trades):
    fee_real = orden["fee"] * 2
    comprador, devs = res["comprador"], res["devs"]

    if comprador is None and devs is None:
        routing_txt = "⚡ <b>Routing:</b>      N/D (se reintentará)"
    else:
        externo = (_externo(comprador) or 0) + (_externo(devs) or 0)
        detalle = (
            f"comprador: {formato_msat(_externo(comprador)) if comprador else 'N/D'}"
            f" / devs: {formato_msat(_externo(devs)) if devs else 'N/D'}"
        )
        routing_txt = f"⚡ <b>Routing:</b>      {formato_msat(externo)} sats  ({detalle})"
        interno = (comprador[1] if comprador else 0) + (devs[1] if devs else 0)
        if interno:
            routing_txt += f"\n🏠 <b>Entre tus nodos:</b> {formato_msat(interno)} sats (no resta)"

    if neto_msat is not None:
        net_txt = f"✅ <b>Ganancia neta:</b> {formato_msat(neto_msat)} sats"
    else:
        provisional = neto_provisional_msat(fee_real, orden["dev_fee"], comprador, devs)
        net_txt = (
            f"✅ <b>Ganancia neta:</b> ≈ {formato_msat(provisional)} sats"
            f"  <i>(provisional: {res['motivo']})</i>"
        )

    acum_txt = (
        f"📈 <b>Acumulado:</b>   {formato_sats(redondear_sats(total_msat))} sats"
        f"  ({total_trades} trades)"
    )

    return "\n".join([
        "💰 <b>Operación completada</b>",
        "",
        f"💵 <b>Volumen:</b>      {formato_sats(orden['amount'])} sats"
        f"  •  {orden['fiat_amount']:,} {orden['fiat_code']}",
        f"📊 <b>Fee cobrado:</b>  {formato_sats(fee_real)} sats",
        f"👨‍💻 <b>Dev fee:</b>     {formato_sats(orden['dev_fee'])} sats",
        routing_txt,
        "━━━━━━━━━━━━━━━━━━━━━",
        net_txt,
        "",
        acum_txt,
    ])

# ---------------------------------------------------------------------------
# Ciclo principal
# ---------------------------------------------------------------------------

def procesar_ciclo(con, nodos, propias, ahora, reintentar):
    caducar_pendientes(con, ahora, MAX_REINTENTO_DIAS)

    try:
        todas = leer_ordenes()
    except sqlite3.Error as e:
        print(f"❌ Error consultando Mostro DB: {e}")
        return

    hechas = ids_procesados(con)
    por_id = {o["id"]: o for o in todas}
    nuevas = [o for o in todas if o["id"] not in hechas]
    reintentos = [por_id[i] for i in ids_a_reintentar(con) if i in por_id] if reintentar else []
    trabajo = nuevas + reintentos

    if not trabajo:
        print(f"✓ Sin órdenes nuevas ni pendientes ({len(hechas)} procesadas)")
        return

    print(f"🔍 {len(nuevas)} orden(es) nueva(s), {len(reintentos)} pendiente(s) por revisar")

    for orden in trabajo:
        preparar(orden, nodos[0])

    desde = min(o["taken_at"] for o in trabajo) - MARGEN_ANTES_SEGUNDOS
    pagos, caidos = cargar_pagos(nodos, desde, ahora)
    if len(caidos) == len(nodos):
        # Sin ningún nodo no se averigua nada: mejor esperar al siguiente ciclo que guardar
        # una operación entera como pendiente y avisar con todo a N/D.
        print("⚠️  Ningún nodo respondió; se reintenta en el próximo ciclo")
        return
    por_hash = {p["payment_hash"]: p for p in pagos}
    usados = hashes_usados(con)

    for orden in trabajo:
        es_nueva = orden["id"] not in hechas
        # Una pendiente que ya tenía su pago asignado puede volver a encontrarlo.
        propio = hash_asignado(con, orden["id"])
        res = resolver_orden(orden, por_hash, pagos, propias, usados - {propio})
        if res["hash_comprador"]:
            usados.add(res["hash_comprador"])

        neto = guardar_resolucion(con, orden, res, ahora)

        if es_nueva:
            total_msat, total_trades = totales(con)
            msg = build_message(orden, res, neto, total_msat, total_trades)
            if send_telegram(msg):
                print(f"✅ Notificado: {orden['id'][:8]}... estado={res['estado']}")
            else:
                print(f"⚠️  Telegram falló para {orden['id'][:8]}...")
            time.sleep(0.5)  # evitar rate limit de Telegram (30 msg/s)
        elif res["estado"] == "ok":
            print(f"🧾 Resuelta la pendiente {orden['id'][:8]}...")


def main():
    print("🧾 Mostro Accounting iniciado")
    print(f"📂 Accounting DB: {ACCOUNTING_DB}")
    print(f"📡 Mostro DB: {MOSTRO_DB}")

    con = init_db()
    print(f"📋 Órdenes ya procesadas: {len(ids_procesados(con))}")

    nodos = cargar_nodos(os.environ)
    print(f"⚡ Nodos consultados: {', '.join(n.nombre for n in nodos)}")
    if len(nodos) < 2:
        print("⚠️  lnd2 no está configurado (LND2_TLSCERT y LND2_MACAROON): los pagos que haga "
              "Mostro desde lnd2 quedarán pendientes en vez de contarse")

    propias = set()
    ultimo_reintento = 0
    while True:
        try:
            if not propias:
                propias = pubkeys_propias(nodos)
            ahora = int(time.time())
            reintentar = ahora - ultimo_reintento >= REINTENTO_SEGUNDOS
            if propias:
                procesar_ciclo(con, nodos, propias, ahora, reintentar)
                if reintentar:
                    ultimo_reintento = ahora
        except Exception as e:
            print(f"❌ Error en ciclo: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
