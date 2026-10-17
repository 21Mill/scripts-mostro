"""
test-telegram.py — Comprueba que las credenciales de Telegram funcionan.

Publica en TELEGRAM_TEST_CHAT_ID, que **hoy apunta al canal público de ofertas**. Esta
nota antes decía justo lo contrario y costó cuatro mensajes de prueba a la vista de todo
el canal; el README ya lo advertía en su tabla de variables, pero aquí no. Si quieres
probar en privado, apunta esa variable a un chat tuyo —el bot solo puede escribirte si le
has dado a «Iniciar»— o usa el par TELEGRAM_MONITOR_*, que ya es privado.

Antes de enviar se consulta getChat y se imprime adónde va. Si el destino no es privado,
hay que confirmarlo con --si-es-publico. getChat es una consulta directa y funciona aunque
el bot tenga un webhook activo, al contrario que el getUpdates de resolver-chat-id.py.
"""

import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.entorno import cargar_env
from lib.telegram import enviar

cargar_env()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_TEST_CHAT_ID")
MENSAJE = "🤖 ¡Hola NostroMostro España! El cartero digital acaba de aterrizar. Preparado para anunciar las ofertas."

if not TOKEN or not CHAT_ID:
    print("❌ Faltan TELEGRAM_TOKEN o TELEGRAM_TEST_CHAT_ID en el .env")
    sys.exit(1)

respuesta = requests.get(
    f"https://api.telegram.org/bot{TOKEN}/getChat",
    params={"chat_id": CHAT_ID},
    timeout=20,
).json()
if not respuesta.get("ok"):
    print(f"❌ No se pudo consultar el destino: {respuesta.get('description')}")
    sys.exit(1)

destino = respuesta["result"]
tipo = destino.get("type")
nombre = destino.get("title") or destino.get("first_name") or destino.get("username") or ""
print(f"Destino: {tipo} {CHAT_ID} ({nombre})")

if tipo != "private" and "--si-es-publico" not in sys.argv:
    print(f"❌ Esto publicaría en «{nombre}», que no es un chat privado.")
    print("   Apunta TELEGRAM_TEST_CHAT_ID a un chat tuyo, o repite con --si-es-publico.")
    sys.exit(1)

message_id = enviar(TOKEN, CHAT_ID, MENSAJE, html=False)
if message_id is None:
    sys.exit(1)
print(f"✅ Enviado (message_id: {message_id})")
