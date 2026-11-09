"""
test-accounting.py — Comprueba el mensaje de operación completada de accounting.py.

Ejecutar: python3 accounting/test-accounting.py

Sin red ni LND: construye el mensaje a partir de datos de ejemplo y mira su texto. Importar
accounting.py carga el .env, que aquí no se usa para nada.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import accounting  # noqa: E402

fallos = []


def comprobar(descripcion, obtenido, esperado):
    if obtenido == esperado:
        print(f"  ✅ {descripcion}")
    else:
        print(f"  ❌ {descripcion}\n       esperado: {esperado}\n       obtenido: {obtenido}")
        fallos.append(descripcion)


ID_HEX = "33b61a7f97414e1aa9df5735181da549"
ID_UUID = "33b61a7f-9741-4e1a-a9df-5735181da549"

orden = {"id": ID_HEX, "amount": 100000, "fee": 300, "dev_fee": 90, "fiat_code": "EUR", "fiat_amount": 50}
completa = {"comprador": (15845, 0), "devs": (1013, 0), "estado": "ok", "motivo": ""}
pendiente = {"comprador": (15845, 0), "devs": None, "estado": "pendiente",
             "motivo": "comision de desarrollo aun sin pagar"}

print("mensaje de operación completada\n")

msg = accounting.build_message(orden, completa, 493_142, 493_142, 1)
comprobar("el id de la orden va en <code>, copiable con un toque", f"<code>{ID_UUID}</code>" in msg, True)
comprobar("como UUID con guiones, no en hexadecimal corrido", ID_HEX in msg, False)
comprobar("y una sola vez", msg.count(ID_UUID), 1)
comprobar("el id sale justo debajo del título",
          msg.splitlines()[2].startswith("🆔"), True)
comprobar("con el neto exacto cuando se conoce todo", "Ganancia neta:</b> 493,142 sats" in msg, True)

msg = accounting.build_message(orden, pendiente, None, 493_142, 1)
comprobar("con una operación pendiente también lleva el id", f"<code>{ID_UUID}</code>" in msg, True)
comprobar("y marca el neto como provisional", "provisional" in msg, True)

interno = {"comprador": (3000, 1500), "devs": (0, 0), "estado": "ok", "motivo": ""}
comprobar("el coste entre tus nodos se enseña aparte",
          "Entre tus nodos" in accounting.build_message(orden, interno, 1, 1, 1), True)
comprobar("y no sale cuando es 0", "Entre tus nodos" in msg, False)

print()
if fallos:
    print(f"{len(fallos)} prueba(s) fallida(s)")
    sys.exit(1)
print("Todas las pruebas pasan")
