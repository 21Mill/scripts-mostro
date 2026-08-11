"""
telegram.py — Envío de mensajes a la API de Telegram.

Estaba escrito cuatro veces (bot.py, resumen.py, accounting.py, informe-mensual.py) y las
copias habían divergido: una sin timeout, otra con reintentos, cada una con su propio texto
de error. Aquí van unificadas.

La vista previa de enlaces se desactiva siempre: los cuatro llamantes lo hacían, y en un
mensaje de ofertas o de cuentas una tarjeta de previsualización solo estorba.
"""

import re
import time
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{metodo}"


def enviar(token, chat_id, texto, html=True, timeout=30, reintentos=1):
    """Envía un mensaje. Devuelve su message_id, o None si no se pudo enviar.

    El message_id hace falta para poder borrar el mensaje después, que es lo que hace el
    bot de ofertas cuando una oferta deja de estar disponible. A quien solo le importe si
    salió le basta con comprobar que no es None: la API siempre devuelve message_id cuando
    acepta el envío.

    'reintentos' es el número de intentos adicionales tras el primero.
    """
    datos = {
        "chat_id": chat_id,
        "text": texto,
        "disable_web_page_preview": True,
    }
    if html:
        datos["parse_mode"] = "HTML"

    url = API.format(token=token, metodo="sendMessage")

    for intento in range(reintentos + 1):
        try:
            respuesta = requests.post(url, data=datos, timeout=timeout)
            if respuesta.status_code == 200:
                return respuesta.json().get("result", {}).get("message_id")
            print(f"❌ Error de Telegram ({respuesta.status_code}): {respuesta.text[:200]}")
        except requests.RequestException as e:
            print(f"❌ Error de conexión con Telegram: {e}")
        if intento < reintentos:
            time.sleep(2)
    return None


def borrar(token, chat_id, message_id, timeout=30):
    """Borra un mensaje. True si puede darse por retirado, False si conviene reintentarlo.

    Que Telegram responda con un error (mensaje inexistente, demasiado antiguo para
    borrarlo) también cuenta como retirada: reintentarlo en cada arranque no lo va a
    arreglar y la entrada se quedaría atascada para siempre. Solo un fallo de conexión
    justifica conservarla.
    """
    url = API.format(token=token, metodo="deleteMessage")
    try:
        respuesta = requests.post(
            url, data={"chat_id": chat_id, "message_id": message_id}, timeout=timeout
        )
    except requests.RequestException as e:
        print(f"❌ Error de conexión borrando mensaje {message_id}: {e}")
        return False

    if respuesta.status_code == 200:
        print(f"🗑️ Oferta eliminada del canal (msg_id: {message_id})")
    else:
        print(f"⚠️ Telegram no pudo borrar {message_id}, se descarta igualmente: {respuesta.text}")
    return True


def credenciales_de_toml(ruta):
    """Lee bot_token y chat_id de la sección [telegram] de un config.toml.

    Permite reutilizar el bot que ya tenga configurado otro servicio (el watchdog de
    Mostro) sin duplicar el token en un segundo fichero: al rotarlo solo hay que tocar su
    configuración original. No usamos tomllib porque Ubuntu 22.04 trae Python 3.10 y llegó
    en la 3.11.
    """
    try:
        texto = Path(ruta).read_text()
    except (IOError, OSError) as e:
        print(f"⚠️ No se pudo leer {ruta}: {e}")
        return None, None
    if "[telegram]" not in texto:
        print(f"⚠️ {ruta} no tiene sección [telegram]")
        return None, None
    seccion = texto.split("[telegram]", 1)[1].split("\n[", 1)[0]
    token = re.search(r'^\s*bot_token\s*=\s*"([^"]+)"', seccion, re.M)
    chat = re.search(r'^\s*chat_id\s*=\s*"?(-?\d+|@[\w]+)"?', seccion, re.M)
    return (token.group(1) if token else None), (chat.group(1) if chat else None)
