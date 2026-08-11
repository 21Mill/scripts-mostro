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

# Ruta absoluta a propósito, y única para todos los scripts de bot/. Se ejecutan desde
# directorios muy distintos (systemd, cron, a mano desde cualquier sitio) y un
# load_dotenv() relativo simplemente no encuentra el fichero: la configuración se queda
# vacía sin dar ningún error, que es la peor forma de fallar.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_FILE)

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
            sats_fijos = oferta["monto_sats"]
            fiat_fijo = oferta["monto_fiat"]
            if sats_fijos != "0" and fiat_fijo not in ("Cualquier monto",) and "—" not in fiat_fijo:
                try:
                    precio_btc = (float(fiat_fijo) / int(sats_fijos)) * 100_000_000
                    precio_fmt = f"{int(round(precio_btc)):,}".replace(",", ".")
                    premium_txt = f"💲 {b('Precio BTC:')}  {precio_fmt} {oferta['fiat']}"
                except (ValueError, ZeroDivisionError):
                    premium_txt = f"📊 {b('Premium:')}  Precio de mercado"
            else:
                premium_txt = f"📊 {b('Premium:')}  Precio de mercado"
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


def obtener_pending(timeout=15, max_eventos=2000):
    """Ofertas pending según el relay: {order_id: oferta}, o None si no contestó.

    Sin filtro 'since' a propósito. Los kind 38383 son eventos reemplazables
    parametrizados: el relay solo conserva el último por (autor, kind, d), así que una
    consulta sin ventana temporal da el estado actual y nada más. Con la ventana de 24 h
    que se usaba antes, una oferta viva más antigua parecía no existir.

    Devolver None cuando no llega el EOSE es deliberado: quien reconcilia tiene que poder
    distinguir "el relay dice que no queda ninguna" de "el relay no ha contestado".
    Confundirlos vaciaría el canal entero durante una caída del relay.
    """
    ws = None
    try:
        ws = websocket.create_connection(RELAY, timeout=timeout)
        ws.send(json.dumps(["REQ", "scan", {"kinds": [38383], "authors": [MOSTRO_PUBKEY]}]))

        pending = {}
        for _ in range(max_eventos):
            respuesta = json.loads(ws.recv())
            if respuesta[0] == "EVENT":
                oferta = parsear_oferta(respuesta[2])
                if oferta and oferta["estado"] == "pending":
                    pending[oferta["order_id"]] = oferta
            elif respuesta[0] == "EOSE":
                return pending
        print("⚠️ Demasiados eventos sin EOSE: no me fío del resultado")
        return None
    except Exception as e:
        print(f"⚠️ No se pudo consultar el relay: {e}")
        return None
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def reconciliar(publicadas, pending):
    """Compara lo publicado con la realidad del relay: (a_retirar, a_publicar).

    Es el arreglo de una fuga real: los bots solo retiraban una oferta al ver en directo su
    evento de cambio de estado, así que todo lo ocurrido mientras estaban parados se perdía
    para siempre. Y la expiración NIP-40 es silenciosa —el evento simplemente desaparece,
    sin avisar—, de modo que comparar contra el estado actual es la única forma de
    detectarla.
    """
    if pending is None:
        return [], []
    a_retirar = [oid for oid in publicadas if oid not in pending]
    a_publicar = [oid for oid in pending if oid not in publicadas]
    return a_retirar, a_publicar


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
