"""
test-resumen.py — Comprueba el guardia de destino y el bloque de cuentas del resumen.

Ejecutar: python3 bot/test-resumen.py

Sin dependencias ni framework, como el resto de pruebas del repo. Nada aquí toca la red ni
la base real: el bloque se arma con diccionarios a mano.

Lo que de verdad se está protegiendo aquí es que las cuentas de la instancia no acaben
publicadas en el canal de ofertas. Si algún día se toca destino_privado, estas pruebas son
las que deben fallar primero.
"""

import os
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))
sys.path.insert(0, str(SCRIPT_DIR))

# Fijados antes de importar: el módulo carga el .env al importarse, y load_dotenv no pisa
# lo que ya está en el entorno. Así las pruebas no dependen de la configuración real ni la
# tocan, y los canales contra los que se comprueba el guardia son los de aquí abajo.
os.environ["TELEGRAM_CHAT_ID"] = "@nostromostroofertas"
os.environ["TELEGRAM_TEST_CHAT_ID"] = "-1001111111111"

import resumen

fallos = []


def comprobar(descripcion, obtenido, esperado):
    if obtenido == esperado:
        print(f"  ✅ {descripcion}")
    else:
        print(f"  ❌ {descripcion}\n       esperado: {esperado}\n       obtenido: {obtenido}")
        fallos.append(descripcion)


print("destino_privado\n")

comprobar("un chat numérico propio es privado",
          resumen.destino_privado("123456789"), True)
comprobar("acepta un ID que llega como número",
          resumen.destino_privado(123456789), True)

comprobar("un canal por @nombre no lo es",
          resumen.destino_privado("@nostromostroofertas"), False)
comprobar("el canal público declarado no lo es",
          resumen.destino_privado(os.environ["TELEGRAM_CHAT_ID"]), False)

# El de pruebas apunta al mismo canal público que el de ofertas. Es la trampa que ya casi
# se traga el .env.example: un respaldo silencioso a esta variable publicaba en abierto.
comprobar("el canal de pruebas tampoco, aunque sea un ID numérico",
          resumen.destino_privado("-1001111111111"), False)

comprobar("sin destino, no", resumen.destino_privado(None), False)
comprobar("cadena vacía, no", resumen.destino_privado(""), False)
comprobar("solo espacios, no", resumen.destino_privado("   "), False)

# Un espacio de más en el .env no debe convertir el canal público en "privado".
comprobar("el canal público con espacios alrededor sigue sin serlo",
          resumen.destino_privado("  -1001111111111  "), False)


print("\nbloque_cuentas\n")

FECHA = date(2026, 8, 12)
DIA = {"operaciones": 21, "neto": 11445, "volumen": 2783600,
       "fee": 16696, "dev_fee": 5009, "routing": 242}
MES = {"operaciones": 72, "neto": 28450, "volumen": 7465740,
       "fee": 44782, "dev_fee": 13433, "routing": 2899}

texto = "\n".join(resumen.bloque_cuentas(FECHA, {"dia": DIA, "mes": MES}, 55417))

comprobar("el encabezado lleva la fecha del día informado",
          "🔒 <b>Cuentas del 12 de agosto</b>" in texto, True)
comprobar("el neto sale formateado", "11.445 sats" in texto, True)
comprobar("el volumen también", "2.783.600 sats" in texto, True)
comprobar("con el equivalente en euros", "(≈ 6,34 €)" in texto, True)
comprobar("y el acumulado del mes del día informado",
          "📆 <b>Agosto hasta hoy:</b>  28.450 sats  (72 ops)" in texto, True)

sin_precio = "\n".join(resumen.bloque_cuentas(FECHA, {"dia": DIA, "mes": MES}, None))
comprobar("sin precio BTC se omite el paréntesis en euros", "€" in sin_precio, False)

vacio = {"operaciones": 0, "neto": 0, "volumen": 0, "fee": 0, "dev_fee": 0, "routing": 0}
texto_vacio = "\n".join(resumen.bloque_cuentas(FECHA, {"dia": vacio, "mes": MES}, 55417))
comprobar("un día sin operaciones se resume en una línea",
          "Sin operaciones." in texto_vacio, True)
comprobar("pero el acumulado del mes se sigue dando",
          "28.450 sats" in texto_vacio, True)
comprobar("y no se inventa un desglose de ceros",
          "Fee cobrado" in texto_vacio, False)


print("\ndia_informado\n")

# El cron de las 00:00 informa del día que acaba de cerrarse, no del que empieza.
comprobar("informa del día anterior",
          resumen.dia_informado(date(2026, 8, 13)), date(2026, 8, 12))
comprobar("el día 1 informa del último del mes anterior",
          resumen.dia_informado(date(2026, 9, 1)), date(2026, 8, 31))
comprobar("y el 1 de enero, del 31 de diciembre",
          resumen.dia_informado(date(2027, 1, 1)), date(2026, 12, 31))


print()
if fallos:
    print(f"{len(fallos)} prueba(s) fallida(s)")
    sys.exit(1)
print("Todas las pruebas pasan")
