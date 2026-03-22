import json
import os
import websocket
import requests
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURACIÓN ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MOSTRO_PUBKEY = os.getenv("MOSTRO_PUBKEY")
RELAY = os.getenv("MOSTRO_RELAY")

ordenes_enviadas = set()


def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    datos = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        respuesta = requests.post(url, data=datos)
        if respuesta.status_code == 200:
            print("✅ Alerta enviada a Telegram")
        else:
            print(f"❌ Error de Telegram: {respuesta.text}")
    except Exception as e:
        print(f"❌ Error de conexión: {e}")


def formato_sats(sats_str):
    """Formatea satoshis con separador de miles."""
    try:
        return f"{int(sats_str):,}".replace(",", ".")
    except (ValueError, TypeError):
        return sats_str


def procesar_mensaje(ws, mensaje):
    try:
        datos = json.loads(mensaje)
        if datos[0] != "EVENT":
            return

        evento = datos[2]
        todas_las_etiquetas = evento.get('tags', [])
        tags = {t[0]: t[1:] for t in todas_las_etiquetas if len(t) > 1}

        order_id = tags.get('d', [''])[0]
        estado = tags.get('s', [''])[0].lower()

        if estado != 'pending' or order_id in ordenes_enviadas:
            return

        ordenes_enviadas.add(order_id)

        tipo = tags.get('k', [''])[0].upper()
        fiat = tags.get('f', [''])[0].upper()
        monto_sats = tags.get('amt', ['0'])[0]
        premium = tags.get('premium', ['0'])[0]
        created_at = evento.get('created_at', 0)
        bond = tags.get('bond', [''])[0] if 'bond' in tags else ''
        expiration = tags.get('expiration', [''])[0] if 'expiration' in tags else ''

        # Rangos fiat
        fa_datos = tags.get('fa', [])
        if len(fa_datos) > 1:
            monto_fiat = f"{fa_datos[0]} — {fa_datos[1]}"
        elif len(fa_datos) == 1:
            monto_fiat = fa_datos[0]
        else:
            monto_fiat = "Cualquier monto"

        # Métodos de pago (puede haber varios tags 'pm')
        lista_pm = [
            metodo.upper()
            for t in todas_las_etiquetas
            if t[0] == 'pm'
            for metodo in t[1:]
        ]
        metodos_texto = ", ".join(lista_pm) if lista_pm else "No especificado"

        # Tipo y descripción
        if tipo == "BUY":
            accion = "COMPRA"
            emoji = "🟢"
            descripcion = "Alguien quiere <b>comprar</b> Bitcoin"
        else:
            accion = "VENTA"
            emoji = "🔴"
            descripcion = "Alguien quiere <b>vender</b> Bitcoin"

        # Tiempo desde la creación
        tiempo = ""
        if created_at:
            try:
                creado = datetime.fromtimestamp(created_at, tz=timezone.utc)
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

        # --- Construir mensaje ---
        texto = f"{emoji} <b>Nueva oferta #{accion}</b>\n"
        texto += "━━━━━━━━━━━━━━━━━━━━━\n\n"
        texto += f"{descripcion}\n\n"
        texto += f"💰 <b>Fiat:</b>  {monto_fiat} {fiat}\n"

        if monto_sats != "0":
            texto += f"⚡ <b>Sats:</b>  {formato_sats(monto_sats)} sats\n"
        else:
            texto += "⚡ <b>Sats:</b>  A precio de mercado\n"

        # Premium con indicador visual
        try:
            p = float(premium)
            if p > 0:
                texto += f"📈 <b>Premium:</b>  +{premium}%\n"
            elif p < 0:
                texto += f"📉 <b>Descuento:</b>  {premium}%\n"
            else:
                texto += "📊 <b>Premium:</b>  Sin premium (precio de mercado)\n"
        except ValueError:
            texto += f"📊 <b>Premium:</b>  {premium}%\n"

        texto += f"🏦 <b>Método:</b>  {metodos_texto}\n"

        if bond:
            texto += f"🔒 <b>Fianza:</b>  {bond}%\n"

        if tiempo:
            texto += f"\n🕐 <i>{tiempo}</i>\n"

        texto += f"\n<code>{order_id}</code>\n"
        texto += "━━━━━━━━━━━━━━━━━━━━━\n"
        texto += "🧌 <b>Mostro P2P</b> — Exchange sin KYC vía ⚡"

        enviar_telegram(texto)

    except Exception as e:
        print(f"⚠️ Error procesando mensaje: {e}")


def al_abrir(ws):
    print(f"📡 Conectado a {RELAY}")
    print("👂 Escuchando nuevas ofertas...")
    ahora = int(time.time()) - 60
    suscripcion = [
        "REQ", "mostro_listener",
        {
            "kinds": [38383],
            "authors": [MOSTRO_PUBKEY],
            "since": ahora
        }
    ]
    ws.send(json.dumps(suscripcion))


def al_cerrar(ws, close_status_code, close_msg):
    print("⚠️ Conexión cerrada. Reconectando en 5s...")
    time.sleep(5)


def al_error(ws, error):
    print(f"❌ Error WebSocket: {error}")


if __name__ == "__main__":
    while True:
        try:
            ws = websocket.WebSocketApp(
                RELAY,
                on_message=procesar_mensaje,
                on_open=al_abrir,
                on_close=al_cerrar,
                on_error=al_error
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            print(f"❌ Error fatal: {e}. Reintentando en 10s...")
            time.sleep(10)
