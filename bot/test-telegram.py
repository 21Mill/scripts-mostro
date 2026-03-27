import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_TEST_CHAT_ID")
MENSAJE = "🤖 ¡Hola NostroMostro España! El cartero digital acaba de aterrizar. Preparado para anunciar las ofertas."

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
datos = {
    "chat_id": CHAT_ID,
    "text": MENSAJE
}

respuesta = requests.post(url, data=datos)
print("Estado del envío:", respuesta.json())
