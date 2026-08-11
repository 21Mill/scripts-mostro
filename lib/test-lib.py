"""
test-lib.py — Comprueba las piezas compartidas de lib/.

Ejecutar: python3 lib/test-lib.py

Sin dependencias ni framework, como test-reconciliacion.py: esto debe poder lanzarse tal
cual en el servidor. Nada aquí toca la red.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.estado import cargar_estado, guardar_estado
from lib.formato import con_signo, formato_euros, formato_sats
from lib.telegram import credenciales_de_toml

fallos = []


def comprobar(descripcion, obtenido, esperado):
    if obtenido == esperado:
        print(f"  ✅ {descripcion}")
    else:
        print(f"  ❌ {descripcion}\n       esperado: {esperado}\n       obtenido: {obtenido}")
        fallos.append(descripcion)


print("formato\n")

comprobar("separa los miles con punto", formato_sats(1234567), "1.234.567")
comprobar("no adorna las cifras pequeñas", formato_sats(42), "42")
comprobar("acepta cifras que llegan como texto", formato_sats("2500"), "2.500")

# Devolver el valor tal cual, y no str(valor), evita que un None acabe impreso como la
# cadena 'None' en mitad de un mensaje publicado.
comprobar("devuelve intacto lo que no es un número", formato_sats(None), None)

comprobar("euros con coma decimal", formato_euros(5.938), "5,94")
comprobar("euros con miles y decimales", formato_euros(1234.5), "1.234,50")

comprobar("suma con signo más", con_signo(1200), "+1.200")
comprobar("resta con el menos tipográfico", con_signo(-300), "−300")
comprobar("el cero no lleva signo", con_signo(0), "0")


print("\nestado\n")

with tempfile.TemporaryDirectory() as tmp:
    ruta = Path(tmp) / "estado.json"

    comprobar("un fichero que no existe es estado vacío", cargar_estado(ruta), {})

    guardar_estado(ruta, {"ultimo_envio": "2026-08"})
    comprobar("lee lo que acaba de guardar", cargar_estado(ruta), {"ultimo_envio": "2026-08"})

    guardar_estado(ruta, {"ultimo_envio": "2026-09"})
    comprobar("sobreescribe en vez de acumular", cargar_estado(ruta), {"ultimo_envio": "2026-09"})
    comprobar("y el fichero no crece", len(json.loads(ruta.read_text())), 1)

    # Un JSON corrupto significa "no hay estado previo", no una excepción en mitad del cron.
    ruta.write_text("{esto no es json")
    comprobar("un fichero corrupto es estado vacío", cargar_estado(ruta), {})


print("\ncredenciales_de_toml\n")

with tempfile.TemporaryDirectory() as tmp:
    bueno = Path(tmp) / "config.toml"
    bueno.write_text(
        '[lnd]\ncert_path = "/x/y"\n\n'
        '[telegram]\nbot_token = "123:ABC"\nchat_id = "-1001234567890"\n\n'
        '[otra]\nbot_token = "no-es-este"\n'
    )
    comprobar("lee token y chat de la sección [telegram]",
              credenciales_de_toml(bueno), ("123:ABC", "-1001234567890"))

    sin_seccion = Path(tmp) / "sin.toml"
    sin_seccion.write_text('[lnd]\nbot_token = "123:ABC"\n')
    comprobar("sin sección [telegram] no devuelve nada",
              credenciales_de_toml(sin_seccion), (None, None))

    comprobar("un fichero inexistente no revienta",
              credenciales_de_toml(Path(tmp) / "no-existe.toml"), (None, None))


print()
if fallos:
    print(f"{len(fallos)} prueba(s) fallida(s)")
    sys.exit(1)
print("Todas las pruebas pasan")
