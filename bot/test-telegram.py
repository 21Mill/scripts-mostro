"""
test-telegram.py — Comprueba que las credenciales de Telegram funcionan.

Publica en TELEGRAM_TEST_CHAT_ID, no en el canal de ofertas.
"""

import os
import sys
from pathlib import Path

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

message_id = enviar(TOKEN, CHAT_ID, MENSAJE, html=False)
if message_id is None:
    sys.exit(1)
print(f"✅ Enviado (message_id: {message_id})")
