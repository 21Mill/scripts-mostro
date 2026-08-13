"""
test-lib.py — Comprueba las piezas compartidas de lib/.

Ejecutar: python3 lib/test-lib.py

Sin dependencias ni framework, como test-reconciliacion.py: esto debe poder lanzarse tal
cual en el servidor. Nada aquí toca la red.
"""

import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.contabilidad import rango_dia, rango_mes
from lib.estado import cargar_estado, guardar_estado
from lib.formato import con_signo, fecha_larga, formato_euros, formato_sats
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

# En castellano y sin depender del locale: cron no hereda el del operador y strftime('%B')
# sacaría el mes en inglés dentro de un informe que sí está en español.
comprobar("fecha larga en castellano", fecha_larga(date(2026, 8, 12)), "12 de agosto")
comprobar("sin cero a la izquierda en el día", fecha_larga(date(2026, 1, 3)), "3 de enero")


print("\ncontabilidad · ventanas\n")

# Los rangos se calculan en hora local a propósito: el día del operador, no el de UTC. Es
# el mismo criterio con el que el gráfico de la web agrupa por 'localtime'. Por eso las
# comprobaciones se hacen contra datetime local y no contra epochs fijos, que dependerían
# de la zona horaria de la máquina que ejecute las pruebas.
def epoch_local(*args):
    return int(datetime(*args).timestamp())


inicio, fin = rango_dia(date(2026, 8, 12))
comprobar("el día empieza a medianoche local", inicio, epoch_local(2026, 8, 12))
comprobar("y acaba al empezar el siguiente", fin, epoch_local(2026, 8, 13))
comprobar("el día dura 24 h", fin - inicio, 86400)

# timedelta y no fecha.day + 1: el 31 de agosto tiene que dar el 1 de septiembre.
_, fin_mes = rango_dia(date(2026, 8, 31))
comprobar("el último día del mes salta al siguiente", fin_mes, epoch_local(2026, 9, 1))

_, fin_anio = rango_dia(date(2026, 12, 31))
comprobar("y el 31 de diciembre salta de año", fin_anio, epoch_local(2027, 1, 1))

comprobar("rango_dia acepta también un datetime",
          rango_dia(datetime(2026, 8, 12, 17, 45)), rango_dia(date(2026, 8, 12)))

inicio_mes, fin_mes = rango_mes(2026, 8)
comprobar("el mes empieza el día 1", inicio_mes, epoch_local(2026, 8, 1))
comprobar("y acaba al empezar el siguiente", fin_mes, epoch_local(2026, 9, 1))
comprobar("diciembre cierra en enero del año siguiente",
          rango_mes(2026, 12)[1], epoch_local(2027, 1, 1))

# El día completo cae dentro de su mes: es lo que permite mostrar juntos "las cuentas del
# día" y "el mes hasta hoy" sin que uno contradiga al otro.
d_ini, d_fin = rango_dia(date(2026, 8, 31))
m_ini, m_fin = rango_mes(2026, 8)
comprobar("el último día del mes está contenido en su mes",
          d_ini >= m_ini and d_fin <= m_fin, True)


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
