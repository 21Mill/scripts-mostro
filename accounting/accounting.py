"""
accounting.py — Contabilidad de ganancias de NostroMostro.

Sondea mostro.db cada 60s buscando órdenes completadas (status='success'),
calcula el beneficio neto real usando LND para routing fees, guarda en
accounting.db (SQLite) y notifica al operador por Telegram privado.

Beneficio = fee - dev_fee - routing_buyer - routing_devs
"""

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv
import requests

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(ENV_FILE)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_MONITOR_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_MONITOR_CHAT_ID")
MOSTRO_DB = os.getenv("MOSTRO_DB", "/opt/mostro/mostro.db")

ACCOUNTING_DB = Path(__file__).parent / "accounting.db"
POLL_INTERVAL = 60  # segundos

# Caché incremental de pagos LND: {payment_hash: fee_sat}
_lnd_cache: dict = {}
_lnd_last_fetch: int = 0  # epoch del último fetch

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
    return con


def get_processed_ids(con):
    rows = con.execute("SELECT order_id FROM earnings").fetchall()
    return {r[0] for r in rows}


def insert_earning(con, order_id, completed_at, amount, fiat_code, fiat_amount,
                   fee, dev_fee, routing_buyer, routing_devs):
    net = None
    if routing_buyer is not None and routing_devs is not None:
        net = fee - dev_fee - routing_buyer - routing_devs
    con.execute("""
        INSERT OR IGNORE INTO earnings
          (order_id, completed_at, amount, fiat_code, fiat_amount,
           fee, dev_fee, routing_buyer, routing_devs, net_profit, notified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (order_id, completed_at, amount, fiat_code, fiat_amount,
          fee, dev_fee, routing_buyer, routing_devs, net, int(time.time())))
    con.commit()
    return net


def get_totals(con):
    row = con.execute(
        "SELECT COALESCE(SUM(net_profit), 0), COUNT(*) FROM earnings WHERE net_profit IS NOT NULL"
    ).fetchone()
    return row[0], row[1]

# ---------------------------------------------------------------------------
# Mostro DB
# ---------------------------------------------------------------------------

def query_mostro(sql):
    result = subprocess.run(
        ["sudo", "-u", "mostro", "sqlite3", "-separator", "|", MOSTRO_DB, sql],
        capture_output=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"sqlite3 error: {result.stderr.decode('utf-8', errors='replace').strip()}")
    return result.stdout.decode("utf-8", errors="replace").strip()


def get_success_orders(processed_ids):
    # id es un BLOB binario en Mostro — usamos lower(hex(id)) para obtener el UUID legible
    sql = (
        "SELECT lower(hex(id)), amount, fee, dev_fee, dev_fee_payment_hash, "
        "buyer_invoice, fiat_code, fiat_amount, taken_at "
        "FROM orders WHERE status='success';"
    )
    output = query_mostro(sql)
    if not output:
        return []

    orders = []
    for line in output.splitlines():
        parts = line.split("|")
        if len(parts) < 9:
            continue
        order_id = parts[0]
        if order_id in processed_ids:
            continue
        try:
            orders.append({
                "id": order_id,
                "amount": int(parts[1] or 0),
                "fee": int(parts[2] or 0),
                "dev_fee": int(parts[3] or 0),
                "dev_fee_hash": parts[4],
                "buyer_invoice": parts[5],
                "fiat_code": parts[6],
                "fiat_amount": int(parts[7] or 0),
                "taken_at": int(parts[8] or 0),
            })
        except (ValueError, IndexError):
            pass
    return orders

# ---------------------------------------------------------------------------
# LND — caché incremental
# ---------------------------------------------------------------------------

def refresh_lnd_cache(full=False):
    """
    Actualiza _lnd_cache con pagos SUCCEEDED de LND.
    full=True: trae todos los pagos (primer arranque, ~30s).
    full=False: solo pagos desde el último fetch (ciclos normales, <1s).
    """
    global _lnd_cache, _lnd_last_fetch

    cmd = ["lncli", "listpayments"]
    if full:
        cmd += ["--max_payments=10000"]
    else:
        since = max(0, _lnd_last_fetch - 120)  # margen de 2 min
        cmd += [f"--creation_date_start={since}"]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        raise RuntimeError(f"lncli error: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    added = 0
    for p in data.get("payments", []):
        if p.get("status") == "SUCCEEDED":
            h = p["payment_hash"]
            if h not in _lnd_cache:
                _lnd_cache[h] = int(p.get("fee_sat", 0))
                added += 1

    _lnd_last_fetch = int(time.time())
    return added


def get_lnd_fee(payment_hash):
    return _lnd_cache.get(payment_hash)


def decode_invoice_hash(bolt11):
    """Decodifica un bolt11 y devuelve su payment_hash, o None si falla."""
    if not bolt11:
        return None
    result = subprocess.run(
        ["lncli", "decodepayreq", bolt11],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout).get("payment_hash")
    except (json.JSONDecodeError, AttributeError):
        return None

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def formato_sats(n):
    try:
        return f"{int(n):,}".replace(",", ".")
    except (ValueError, TypeError):
        return str(n)


def send_telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️  TELEGRAM_TOKEN o CHAT_ID no configurados")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for _ in range(2):
        try:
            r = requests.post(url, data=payload, timeout=10)
            if r.status_code == 200:
                return True
            print(f"⚠️  Telegram {r.status_code}: {r.text[:100]}")
        except requests.RequestException as e:
            print(f"⚠️  Telegram error: {e}")
        time.sleep(2)
    return False


def build_message(order, net_profit, routing_buyer, routing_devs, total_sats, total_trades):
    fee = order["fee"]
    dev_fee = order["dev_fee"]
    amount = order["amount"]
    fiat = order["fiat_code"]
    fiat_amount = order["fiat_amount"]

    routing_total = (routing_buyer or 0) + (routing_devs or 0)

    if routing_buyer is None or routing_devs is None:
        routing_txt = "⚡ <b>Routing:</b>      N/D (reintentando...)"
        net_txt = "✅ <b>Ganancia neta:</b> N/D"
    else:
        routing_txt = (
            f"⚡ <b>Routing:</b>      {formato_sats(routing_total)} sats"
            f"  (comprador: {routing_buyer} / devs: {routing_devs})"
        )
        net_txt = f"✅ <b>Ganancia neta:</b> {formato_sats(net_profit)} sats"

    acum_txt = (
        f"📈 <b>Acumulado:</b>   {formato_sats(total_sats)} sats  ({total_trades} trades)"
    )

    return "\n".join([
        "💰 <b>Operación completada</b>",
        "",
        f"💵 <b>Volumen:</b>      {formato_sats(amount)} sats  •  {fiat_amount:,} {fiat}",
        f"📊 <b>Fee cobrado:</b>  {formato_sats(fee)} sats",
        f"👨‍💻 <b>Dev fee:</b>     {formato_sats(dev_fee)} sats",
        routing_txt,
        "━━━━━━━━━━━━━━━━━━━━━",
        net_txt,
        "",
        acum_txt,
    ])

# ---------------------------------------------------------------------------
# Ciclo principal
# ---------------------------------------------------------------------------

def process_cycle(con, first_run=False):
    processed_ids = get_processed_ids(con)

    try:
        new_orders = get_success_orders(processed_ids)
    except RuntimeError as e:
        print(f"❌ Error consultando Mostro DB: {e}")
        return

    if not new_orders:
        print(f"✓ Sin órdenes nuevas ({len(processed_ids)} procesadas)")
        return

    print(f"🔍 {len(new_orders)} orden(es) nueva(s) completada(s)")

    # Actualizar caché LND (incremental en ciclos normales)
    try:
        added = refresh_lnd_cache(full=first_run)
        print(f"⚡ Caché LND: {len(_lnd_cache)} pagos totales (+{added} nuevos)")
    except RuntimeError as e:
        print(f"⚠️  No se pudo actualizar caché LND: {e}")

    for order in new_orders:
        order_id = order["id"]
        print(f"📦 Procesando orden {order_id[:8]}...")

        # Routing fee del pago al comprador (decodificar invoice → payment_hash)
        buyer_hash = decode_invoice_hash(order["buyer_invoice"])
        if buyer_hash:
            routing_buyer = get_lnd_fee(buyer_hash)
            if routing_buyer is None:
                # Invoice expirada o pago no encontrado: asumir 0
                routing_buyer = 0
        else:
            routing_buyer = 0

        # Routing fee del pago a devs
        dev_hash = order["dev_fee_hash"]
        routing_devs = get_lnd_fee(dev_hash) if dev_hash else 0
        if routing_devs is None:
            routing_devs = 0

        net = insert_earning(
            con, order_id, order["taken_at"], order["amount"],
            order["fiat_code"], order["fiat_amount"],
            order["fee"], order["dev_fee"], routing_buyer, routing_devs
        )

        total_sats, total_trades = get_totals(con)
        msg = build_message(order, net, routing_buyer, routing_devs, total_sats, total_trades)
        if send_telegram(msg):
            print(f"✅ Notificado: {order_id[:8]}... ganancia={net} sats")
        else:
            print(f"⚠️  Telegram falló para {order_id[:8]}...")

        time.sleep(0.5)  # evitar rate limit de Telegram (30 msg/s)


def main():
    print("🧾 Mostro Accounting iniciado")
    print(f"📂 Accounting DB: {ACCOUNTING_DB}")
    print(f"📡 Mostro DB: {MOSTRO_DB}")

    con = init_db()
    already = len(get_processed_ids(con))
    print(f"📋 Órdenes ya procesadas: {already}")

    first_run = True
    if first_run:
        print("⏳ Primer arranque: cargando historial completo de LND (~30s)...")

    while True:
        try:
            process_cycle(con, first_run=first_run)
        except Exception as e:
            print(f"❌ Error en ciclo: {e}")
        first_run = False
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
