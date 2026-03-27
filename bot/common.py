"""
common.py — Módulo compartido por los bots de Mostro.
Contiene la conexión al relay, parsing de ofertas y persistencia.
"""

import json
import os
import time
import threading
import websocket
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

MOSTRO_PUBKEY = os.getenv("MOSTRO_PUBKEY")
RELAY = os.getenv("MOSTRO_RELAY")


def cargar_ordenes(archivo):
    try:
        if Path(archivo).exists():
            with open(archivo, "r") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return {}


def guardar_ordenes(archivo, ordenes):
    try:
        with open(archivo, "w") as f:
            json.dump(ordenes, f)
    except IOError as e:
        print(f"⚠️ Error guardando {archivo}: {e}")


def parsear_oferta(evento):
    """Extrae los datos de una oferta de un evento kind 38383."""
    todas_las_etiquetas = evento.get('tags', [])
    tags = {t[0]: t[1:] for t in todas_las_etiquetas if len(t) > 1}

    order_id = tags.get('d', [''])[0]
    estado = tags.get('s', [''])[0].lower()

    if not order_id:
        return None

    tipo = tags.get('k', [''])[0].upper()
    fiat = tags.get('f', [''])[0].upper()
    monto_sats = tags.get('amt', ['0'])[0]
    premium = tags.get('premium', ['0'])[0]
    created_at = evento.get('created_at', 0)
    bond = tags.get('bond', [''])[0] if 'bond' in tags else ''

    fa_datos = tags.get('fa', [])
    if len(fa_datos) > 1:
        monto_fiat = f"{fa_datos[0]} — {fa_datos[1]}"
    elif len(fa_datos) == 1:
        monto_fiat = fa_datos[0]
    else:
        monto_fiat = "Cualquier monto"

    lista_pm = [
        metodo.upper()
        for t in todas_las_etiquetas
        if t[0] == 'pm'
        for metodo in t[1:]
    ]
    metodos_texto = ", ".join(lista_pm) if lista_pm else "No especificado"

    return {
        "order_id": order_id,
        "estado": estado,
        "tipo": tipo,
        "fiat": fiat,
        "monto_sats": monto_sats,
        "premium": premium,
        "created_at": created_at,
        "bond": bond,
        "monto_fiat": monto_fiat,
        "metodos": metodos_texto,
    }


def formato_sats(sats_str):
    try:
        return f"{int(sats_str):,}".replace(",", ".")
    except (ValueError, TypeError):
        return sats_str


def formato_texto(oferta, html=False):
    """Genera el texto de la oferta. html=True para Telegram, False para texto plano."""
    tipo = oferta["tipo"]
    b = lambda t: f"<b>{t}</b>" if html else t
    i = lambda t: f"<i>{t}</i>" if html else t

    if tipo == "BUY":
        accion = "COMPRA"
        emoji = "🟢"
        desc = f"Alguien quiere {b('comprar')} Bitcoin"
    else:
        accion = "VENTA"
        emoji = "🔴"
        desc = f"Alguien quiere {b('vender')} Bitcoin"

    premium = oferta["premium"]
    try:
        p = float(premium)
        if p > 0:
            premium_txt = f"📈 {b('Premium:')}  +{premium}%"
        elif p < 0:
            premium_txt = f"📉 {b('Descuento:')}  {premium}%"
        else:
            premium_txt = f"📊 {b('Premium:')}  Sin premium (precio de mercado)"
    except ValueError:
        premium_txt = f"📊 {b('Premium:')}  {premium}%"

    sats = oferta["monto_sats"]
    if sats != "0":
        sats_txt = f"⚡ {b('Sats:')}  {formato_sats(sats)} sats"
    else:
        sats_txt = f"⚡ {b('Sats:')}  A precio de mercado"

    tiempo = ""
    if oferta["created_at"]:
        try:
            creado = datetime.fromtimestamp(oferta["created_at"], tz=timezone.utc)
            ahora = datetime.now(timezone.utc)
            minutos = int((ahora - creado).total_seconds() / 60)
            if minutos < 1:
                tiempo = "Hace un momento"
            elif minutos < 60:
                tiempo = f"Hace {minutos} min"
            else:
                tiempo = f"Hace {minutos // 60}h {minutos % 60}min"
        except Exception:
            pass

    if html:
        code = lambda t: f"<code>{t}</code>"
    else:
        code = lambda t: t

    lineas = [
        f"{emoji} {b(f'Nueva oferta #{accion}')}",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        desc,
        "",
        f"💰 {b('Fiat:')}  {oferta['monto_fiat']} {oferta['fiat']}",
        sats_txt,
        premium_txt,
        f"🏦 {b('Método:')}  {oferta['metodos']}",
    ]

    if oferta["bond"]:
        lineas.append(f"🔒 {b('Fianza:')}  {oferta['bond']}%")

    if tiempo:
        lineas.append(f"\n🕐 {i(tiempo)}")

    lineas.append(f"\n{code(oferta['order_id'])}")
    lineas.append("━━━━━━━━━━━━━━━━━━━━━")
    lineas.append(f"🧌 {b('Mostro P2P')} — Exchange sin KYC vía ⚡")

    return "\n".join(lineas)


def conectar_relay(on_message, on_open_extra=None):
    """Conecta al relay de Mostro y escucha eventos con keepalive."""

    last_connected = [0]

    def al_abrir(ws):
        since = last_connected[0] if last_connected[0] > 0 else int(time.time()) - 300
        last_connected[0] = int(time.time())
        print(f"📡 Conectado a {RELAY}")
        suscripcion = [
            "REQ", "mostro_listener",
            {
                "kinds": [38383],
                "authors": [MOSTRO_PUBKEY],
                "since": since
            }
        ]
        ws.send(json.dumps(suscripcion))
        if on_open_extra:
            on_open_extra(ws)

    def al_cerrar(ws, code, msg):
        print("⚠️ Conexión cerrada. Reconectando en 5s...")
        time.sleep(5)

    def al_error(ws, error):
        print(f"❌ Error WebSocket: {error}")

    while True:
        try:
            ws = websocket.WebSocketApp(
                RELAY,
                on_message=on_message,
                on_open=al_abrir,
                on_close=al_cerrar,
                on_error=al_error
            )
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            print(f"❌ Error fatal: {e}. Reintentando en 10s...")
            time.sleep(10)
