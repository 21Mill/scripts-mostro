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
comprobar("con el neto redondeado al sat cuando se conoce todo", "Ganancia neta:</b> 493 sats" in msg, True)
comprobar("sin decimales en el neto", "493,142" in msg, False)
comprobar("el routing sale redondeado, total y desglose",
          "Routing:</b>      17 sats  (comprador: 16 / devs: 1)" in msg, True)

msg = accounting.build_message(orden, pendiente, None, 493_142, 1)
comprobar("con una operación pendiente también lleva el id", f"<code>{ID_UUID}</code>" in msg, True)
comprobar("y marca el neto como provisional", "provisional" in msg, True)

redondeo = accounting.build_message(orden, {"comprador": (15499, 0), "devs": (500, 0), "estado": "ok", "motivo": ""},
                                     -2_500, 1_999_500, 2)
comprobar("por debajo de medio sat baja", "comprador: 15 /" in redondeo, True)
comprobar("medio sat exacto sube", "devs: 1)" in redondeo, True)
comprobar("el neto negativo se redondea con el menos tipográfico", "Ganancia neta:</b> −2 sats" in redondeo, True)
comprobar("sin coma decimal en ninguna cifra de sats", ",5" in redondeo or ",4" in redondeo, False)

provisional = accounting.build_message(orden, pendiente, None, 493_142, 1)
comprobar("el neto provisional también va redondeado", "≈ " in provisional and "," not in provisional.split("≈ ")[1].split(" sats")[0], True)

interno = {"comprador": (3000, 1500), "devs": (0, 0), "estado": "ok", "motivo": ""}
comprobar("el coste entre tus nodos se enseña aparte",
          "Entre tus nodos" in accounting.build_message(orden, interno, 1, 1, 1), True)
comprobar("y no sale cuando es 0", "Entre tus nodos" in msg, False)

print("\nruta del pago al comprador\n")

PUB_DESTINO = "022b9b71e705ccd4aabbccddeeff00112233445566778899aabbccddeeff0011"
ruta_ej = [[
    {"pub": "03aa", "alias": "NostroMostro", "fee_msat": 0, "propio": True, "destino": False},
    {"pub": "03bb", "alias": "xmrk reloaded", "fee_msat": 10_941, "propio": False, "destino": False},
    {"pub": "03cc", "alias": "LNServer_Wave", "fee_msat": 92_589, "propio": False, "destino": False},
    {"pub": PUB_DESTINO, "alias": "", "fee_msat": 0, "propio": False, "destino": True},
]]
ok_ruta = {"comprador": (103_530, 0), "devs": (0, 0), "estado": "ok", "motivo": ""}
msg = accounting.build_message(orden, ok_ruta, 1, 1, 1, ruta=ruta_ej)
lineas = msg.splitlines()
comprobar("lleva el título de la ruta", "🛣️ <b>Ruta al comprador:</b>" in lineas, True)
comprobar("el salto propio, con su marca", any("NostroMostro" in l and "0 msat (tuyo)" in l for l in lineas), True)
comprobar("un salto intermedio, en msat con puntos", any("xmrk reloaded" in l and "10.941 msat" in l for l in lineas), True)
comprobar("otro salto intermedio", any("LNServer_Wave" in l and "92.589 msat" in l for l in lineas), True)
comprobar("sin alias, la clave pública abreviada y la marca de destino",
          any("022b9b71e705ccd4…" in l and "0 msat (destino)" in l for l in lineas), True)
comprobar("el total de fees, redondeado al sat más cercano (103,53 → 104)", "Fees pagadas:</b>   104 sats" in msg, True)
comprobar("los saltos van numerados en orden", [l.strip()[:2] for l in lineas if l.strip()[:2] in ("1.", "2.", "3.", "4.")],
          ["1.", "2.", "3.", "4."])
comprobar("la ruta va antes de la ganancia neta", msg.index("Ruta al comprador") < msg.index("Ganancia neta"), True)

malicioso = [[{"pub": "03dd", "alias": "<b>x</b> & co", "fee_msat": 5, "propio": False, "destino": True}]]
msg = accounting.build_message(orden, ok_ruta, 1, 1, 1, ruta=malicioso)
comprobar("el alias se escapa para el HTML de Telegram", "&lt;b&gt;x&lt;/b&gt; &amp; co" in msg, True)

dos = ruta_ej + [[{"pub": "03ee", "alias": "Otro", "fee_msat": 1_000, "propio": False, "destino": True}]]
msg = accounting.build_message(orden, ok_ruta, 1, 1, 1, ruta=dos)
comprobar("pago en varias partes: Ruta 1/2", "Ruta 1/2" in msg, True)
comprobar("pago en varias partes: Ruta 2/2", "Ruta 2/2" in msg, True)
comprobar("y un solo total", msg.count("Fees pagadas"), 1)

sin = accounting.build_message(orden, ok_ruta, 1, 1, 1)
comprobar("sin ruta no sale la sección", "Ruta al comprador" in sin or "Fees pagadas" in sin, False)
sin_comprador = accounting.build_message(orden, {"comprador": None, "devs": (0, 0), "estado": "pendiente", "motivo": "x"},
                                         None, 1, 1, ruta=ruta_ej)
comprobar("sin pago al comprador conocido tampoco", "Ruta al comprador" in sin_comprador, False)
comprobar("con ruta=[] tampoco", "Ruta al comprador" in accounting.build_message(orden, ok_ruta, 1, 1, 1, ruta=[]), False)

print()
if fallos:
    print(f"{len(fallos)} prueba(s) fallida(s)")
    sys.exit(1)
print("Todas las pruebas pasan")
