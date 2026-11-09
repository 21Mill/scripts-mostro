"""
test-routing.py — Comprueba el cálculo del coste de enrutamiento y su contabilidad.

Ejecutar: python3 lib/test-routing.py

Sin dependencias ni framework, como test-lib.py. Nada aquí toca LND ni la red: los pagos
son diccionarios con la forma de `lncli listpayments`, y la base es SQLite en memoria.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.contabilidad import consultar
from lib.formato import formato_msat
from lib.ganancias import (
    caducar_pendientes, calcular_neto_msat, guardar_resolucion, hashes_usados,
    ids_a_reintentar, migrar_esquema, neto_provisional_msat, totales,
)
from lib.lnd import cargar_nodos, desglosar_coste, destino_de, pago_por_importe, resolver_orden

fallos = []


def comprobar(descripcion, obtenido, esperado):
    if obtenido == esperado:
        print(f"  ✅ {descripcion}")
    else:
        print(f"  ❌ {descripcion}\n       esperado: {esperado}\n       obtenido: {obtenido}")
        fallos.append(descripcion)


LND1 = "02" + "a1" * 32
LND2 = "03" + "b2" * 32
EXTERNO = "02" + "c3" * 32
PROPIAS = {LND1, LND2}
H_COMPRADOR = "b" * 64
H_DEVS = "d" * 64


def pago(hash_, value, creado, fee_msat, saltos=(), estado="SUCCEEDED"):
    """Un pago con la forma de listpayments. saltos: [(pubkey, fee_msat), ...]."""
    return {
        "payment_hash": hash_,
        "value_sat": str(value),
        "creation_date": str(creado),
        "fee_msat": str(fee_msat),
        "status": "SUCCEEDED",
        "htlcs": [{
            "status": estado,
            "route": {"hops": [{"pub_key": k, "fee_msat": str(f)} for k, f in saltos]},
        }],
    }


def orden(**cambios):
    base = {
        "id": "o1", "amount": 100000, "fee": 300, "dev_fee": 90, "dev_fee_hash": H_DEVS,
        "taken_at": 1_000_000, "destino": "bolt11", "hash_comprador": H_COMPRADOR,
        "fiat_code": "EUR", "fiat_amount": 50,
    }
    base.update(cambios)
    return base


def indexar(pagos):
    return {p["payment_hash"]: p for p in pagos}


print("formato_msat\n")

comprobar("conserva los decimales de sat", formato_msat(15845), "15,845")
comprobar("quita los ceros finales", formato_msat(421570), "421,57")
comprobar("un sat exacto no lleva coma", formato_msat(1000), "1")
comprobar("el cero es cero", formato_msat(0), "0")
comprobar("separa los miles con punto", formato_msat(1234567890), "1.234.567,89")
comprobar("los negativos llevan el menos tipográfico", formato_msat(-2500), "−2,5")
comprobar("devuelve intacto lo que no es un número", formato_msat(None), None)


print("\ndesglosar_coste\n")

ruta = [(LND1, 300), (EXTERNO, 700), ("02" + "dd" * 32, 0)]
comprobar("separa lo que cobra tu nodo de lo que cobra la red",
          desglosar_coste(pago("x", 1, 1, 1000, ruta), PROPIAS), (1000, 300))
comprobar("hoy, con lnd1 subvencionado, lo interno es 0",
          desglosar_coste(pago("x", 1, 1, 700, [(LND1, 0), (EXTERNO, 700)]), PROPIAS), (700, 0))
comprobar("sin saltos tuyos todo es externo",
          desglosar_coste(pago("x", 1, 1, 700, [(EXTERNO, 700)]), PROPIAS), (700, 0))
comprobar("un pago directo no cuesta nada",
          desglosar_coste(pago("x", 1, 1, 0, []), PROPIAS), (0, 0))

# fee_sat de LND trunca; fee_msat no. Es lo que da 28,975 y no 28.
comprobar("conserva la fracción de sat",
          desglosar_coste(pago("x", 1, 1, 28975, [(EXTERNO, 28975)]), PROPIAS), (28975, 0))

sin_total = pago("x", 1, 1, 0, [(LND1, 100), (EXTERNO, 250)])
del sin_total["fee_msat"]
comprobar("sin fee_msat declarada, suma los saltos", desglosar_coste(sin_total, PROPIAS), (350, 100))

con_fallo = pago("x", 1, 1, 400, [(EXTERNO, 400)])
con_fallo["htlcs"].append({"status": "FAILED", "route": {"hops": [{"pub_key": LND1, "fee_msat": "999"}]}})
comprobar("un intento fallido no cuenta como coste", desglosar_coste(con_fallo, PROPIAS), (400, 0))

mpp = pago("x", 1, 1, 500, [(LND1, 100), (EXTERNO, 150)])
mpp["htlcs"].append({"status": "SUCCEEDED", "route": {"hops": [
    {"pub_key": LND1, "fee_msat": "50"}, {"pub_key": EXTERNO, "fee_msat": "200"}]}})
comprobar("un pago en varias partes suma todas", desglosar_coste(mpp, PROPIAS), (500, 150))


print("\ndestino_de\n")

comprobar("una factura bolt11", destino_de("lnbc1230n1pabcdefghijklmnopqrstuvwxyz0123456789"), "bolt11")
comprobar("una Lightning Address", destino_de("alguien@ejemplo.com"), "direccion")
comprobar("vacío", destino_de(""), "desconocido")
comprobar("None", destino_de(None), "desconocido")
comprobar("algo irreconocible", destino_de("hola"), "desconocido")


print("\npago_por_importe\n")

DESDE, HASTA = 1_000_000 - 600, 1_000_000 + 3 * 86400
p_ok = pago("a" * 64, 99700, 1_000_050, 500)

comprobar("encuentra el único pago de ese importe",
          pago_por_importe([p_ok], 99700, DESDE, HASTA), (p_ok, "ok"))
comprobar("ninguno si no hay de ese importe",
          pago_por_importe([p_ok], 99701, DESDE, HASTA), (None, "ninguno"))
comprobar("ignora un pago del mismo importe fuera de la ventana",
          pago_por_importe([pago("e" * 64, 99700, 5, 500)], 99700, DESDE, HASTA), (None, "ninguno"))

p_otro = pago("f" * 64, 99700, 1_000_090, 300)
comprobar("con dos candidatas no elige ninguna",
          pago_por_importe([p_ok, p_otro], 99700, DESDE, HASTA), (None, "ambiguo"))
comprobar("pero si una ya está asignada a otra orden, la otra es la buena",
          pago_por_importe([p_ok, p_otro], 99700, DESDE, HASTA, excluidos={"a" * 64}), (p_otro, "ok"))

# Órdenes pequeñas repetidas al mismo destino: varios pagos del mismo importe que costaron
# exactamente lo mismo. Da igual cuál sea de cada orden; se toma la más temprana.
gemelo_1 = pago("1" * 64, 99700, 1_000_300, 1850, [(EXTERNO, 1850)])
gemelo_2 = pago("2" * 64, 99700, 1_000_100, 1850, [(EXTERNO, 1850)])
mismo_coste = lambda p: desglosar_coste(p, PROPIAS)
comprobar("varios pagos que cuestan lo mismo no son ambiguos: se toma el más temprano",
          pago_por_importe([gemelo_1, gemelo_2], 99700, DESDE, HASTA, coste=mismo_coste), (gemelo_2, "ok"))
comprobar("sin decir cómo comparar costes, sigue siendo ambiguo",
          pago_por_importe([gemelo_1, gemelo_2], 99700, DESDE, HASTA), (None, "ambiguo"))
comprobar("si cuestan distinto, sigue siendo ambiguo aunque se compare",
          pago_por_importe([p_ok, p_otro], 99700, DESDE, HASTA, coste=mismo_coste), (None, "ambiguo"))
comprobar("y el segundo gemelo se asigna al siguiente pago libre",
          pago_por_importe([gemelo_1, gemelo_2], 99700, DESDE, HASTA, excluidos={"2" * 64}, coste=mismo_coste),
          (gemelo_1, "ok"))


print("\nresolver_orden\n")

p_comprador = pago(H_COMPRADOR, 99700, 1_000_050, 15845, [(LND1, 0), (EXTERNO, 15845)])
p_devs = pago(H_DEVS, 90, 1_000_100, 1013, [(LND1, 0), (EXTERNO, 1013)])
todos = [p_comprador, p_devs]

r = resolver_orden(orden(), indexar(todos), todos, PROPIAS)
comprobar("con los dos pagos localizados está resuelta", r["estado"], "ok")
comprobar("el coste del comprador sale en msat exactos", r["comprador"], (15845, 0))
comprobar("y el de la comisión de desarrollo también", r["devs"], (1013, 0))
comprobar("y guarda qué pago era cada uno", (r["hash_comprador"], r["hash_devs"]), (H_COMPRADOR, H_DEVS))

# El fallo de fondo: pagar desde lnd2 y consultar solo lnd1. El pago no está en la lista y
# la orden tiene que quedar pendiente, no en 0.
r = resolver_orden(orden(), {}, [], PROPIAS)
comprobar("un pago que no aparece queda pendiente, no a 0", r["estado"], "pendiente")
comprobar("y el coste desconocido es None", (r["comprador"], r["devs"]), (None, None))
comprobar("con el motivo anotado", "comprador" in r["motivo"], True)

sin_pagar = orden(dev_fee_hash="")
r = resolver_orden(sin_pagar, indexar([p_comprador]), [p_comprador], PROPIAS)
comprobar("con la comisión de desarrollo sin pagar, el comprador sí se resuelve",
          (r["comprador"], r["devs"], r["estado"]), ((15845, 0), None, "pendiente"))

marcador = orden(dev_fee_hash="PENDING-31d01cc3-af52-4036-aae1-cdc6c427449d-1794052069")
r = resolver_orden(marcador, indexar([p_comprador]), [p_comprador], PROPIAS)
comprobar("el marcador PENDING de Mostro no se confunde con un hash", r["devs"], None)

r = resolver_orden(orden(dev_fee=0, dev_fee_hash=""), indexar([p_comprador]), [p_comprador], PROPIAS)
comprobar("sin comisión de desarrollo que pagar, su coste es 0 de verdad",
          (r["devs"], r["estado"]), ((0, 0), "ok"))

direccion = orden(destino="direccion", hash_comprador=None)
p_ln = pago("9" * 64, 99700, 1_000_200, 2999, [(LND1, 0), (EXTERNO, 2999)])
r = resolver_orden(direccion, indexar([p_ln, p_devs]), [p_ln, p_devs], PROPIAS)
comprobar("una Lightning Address se localiza por amount - fee",
          (r["comprador"], r["hash_comprador"], r["estado"]), ((2999, 0), "9" * 64, "ok"))

p_ln2 = pago("8" * 64, 99700, 1_000_300, 100, [(EXTERNO, 100)])
r = resolver_orden(direccion, indexar([p_ln, p_ln2, p_devs]), [p_ln, p_ln2, p_devs], PROPIAS)
comprobar("dos pagos candidatos que cuestan distinto: ambigua, sin adivinar",
          (r["comprador"], r["estado"]), (None, "ambiguo"))

p_ln3 = pago("7" * 64, 99700, 1_000_400, 2999, [(LND1, 0), (EXTERNO, 2999)])
r = resolver_orden(direccion, indexar([p_ln, p_ln3, p_devs]), [p_ln, p_ln3, p_devs], PROPIAS)
comprobar("dos candidatos que cuestan lo mismo se resuelven",
          (r["comprador"], r["hash_comprador"], r["estado"]), ((2999, 0), "9" * 64, "ok"))

r = resolver_orden(direccion, indexar([p_ln, p_devs]), [p_ln, p_devs], PROPIAS, usados={"9" * 64})
comprobar("un pago ya asignado a otra orden no se reutiliza",
          (r["comprador"], r["estado"]), (None, "pendiente"))

r = resolver_orden(orden(destino="desconocido"), indexar(todos), todos, PROPIAS)
comprobar("un destino irreconocible queda pendiente", r["estado"], "pendiente")


print("\nneto\n")

comprobar("fee - dev fee - coste externo, en msat",
          calcular_neto_msat(600, 90, (15845, 0), (1013, 0)), 510_000 - 16_858)
comprobar("lo que cobran tus nodos no se resta",
          calcular_neto_msat(600, 90, (15845, 5000), (1013, 0)), 510_000 - 10_845 - 1_013)
comprobar("si falta un dato no hay neto, no un neto con 0",
          calcular_neto_msat(600, 90, (15845, 0), None), None)
comprobar("el provisional cuenta lo conocido y avisa del resto",
          neto_provisional_msat(600, 90, (15845, 0), None), 510_000 - 15_845)


print("\nnodos\n")

comprobar("sin configurar lnd2 solo se consulta lnd1",
          [n.nombre for n in cargar_nodos({})], ["lnd1"])
con_lnd2 = cargar_nodos({"LND2_TLSCERT": "/c/tls.cert", "LND2_MACAROON": "/c/ro.macaroon"})
comprobar("con certificado y macaroon se añade lnd2", [n.nombre for n in con_lnd2], ["lnd1", "lnd2"])
comprobar("con el puerto por defecto", "--rpcserver=127.0.0.1:10010" in con_lnd2[1].args, True)
comprobar("y apuntando a su certificado y macaroon",
          ("--tlscertpath=/c/tls.cert" in con_lnd2[1].args, "--macaroonpath=/c/ro.macaroon" in con_lnd2[1].args),
          (True, True))
comprobar("un certificado sin macaroon no basta", len(cargar_nodos({"LND2_TLSCERT": "/c/tls.cert"})), 1)


print("\nbase de datos\n")

ESQUEMA_ORIGINAL = """
    CREATE TABLE earnings (
        order_id TEXT PRIMARY KEY, completed_at INTEGER, amount INTEGER, fiat_code TEXT,
        fiat_amount INTEGER, fee INTEGER, dev_fee INTEGER, routing_buyer INTEGER,
        routing_devs INTEGER, net_profit INTEGER, notified_at INTEGER
    )
"""


def base_con_fila_antigua():
    con = sqlite3.connect(":memory:")
    con.execute(ESQUEMA_ORIGINAL)
    con.execute(
        "INSERT INTO earnings VALUES ('viejo', 100, 1000, 'EUR', 5, 600, 90, 3, 1, 506, 100)"
    )
    con.commit()
    return con


TODO = (0, 10**10)

# consultar() debe seguir funcionando antes de recalcular nada: los informes se lanzan por
# cron y no pueden romperse porque la migración aún no haya corrido.
con = base_con_fila_antigua()
antes = consultar(con, *TODO)
comprobar("una base sin las columnas nuevas se sigue leyendo",
          (antes["operaciones"], antes["neto"], antes["routing"], antes["pendientes"]), (1, 506, 4, 0))

migrar_esquema(con)
migrar_esquema(con)
comprobar("migrar el esquema es idempotente",
          len([c for c in con.execute("PRAGMA table_info(earnings)")]), 11 + 10)
comprobar("y no altera lo que ya había",
          (consultar(con, *TODO)["neto"], consultar(con, *TODO)["routing"]), (506, 4))

# Tres operaciones con neto de 100,4 sats: redondeadas una a una darían 300, y la suma
# exacta es 301,2. El informe tiene que dar 301.
con = sqlite3.connect(":memory:")
con.execute(ESQUEMA_ORIGINAL)
migrar_esquema(con)
for i in range(3):
    guardar_resolucion(con, orden(id=f"o{i}", fee=51, dev_fee=0, dev_fee_hash=""),
                       {"comprador": (1600, 0), "devs": (0, 0), "estado": "ok", "motivo": "",
                        "hash_comprador": f"{i}" * 64, "hash_devs": None}, ahora=2_000_000)
agregado = consultar(con, *TODO)
comprobar("el total suma msat y redondea al final", agregado["neto"], 301)
comprobar("el routing también (3 x 1,6 sats = 4,8)", agregado["routing"], 5)
comprobar("las filas se guardan redondeadas para los lectores antiguos",
          con.execute("SELECT net_profit, routing_buyer FROM earnings WHERE order_id='o0'").fetchone(), (100, 2))
comprobar("el acumulado del aviso usa los msat", totales(con), (301_200, 3))
comprobar("los pagos asignados quedan registrados", len(hashes_usados(con)), 3)

# Una operación a medias: el comprador se conoce y la comisión de desarrollo aún no.
con = base_con_fila_antigua()
migrar_esquema(con)
pendiente = {"comprador": (15845, 0), "devs": None, "estado": "pendiente",
             "motivo": "comision de desarrollo aun sin pagar", "hash_comprador": H_COMPRADOR, "hash_devs": None}
neto = guardar_resolucion(con, orden(id="a"), pendiente, ahora=2_000_000)
comprobar("sin todos los datos no hay neto", neto, None)
a = consultar(con, *TODO)
comprobar("la operación pendiente se cuenta como pendiente", a["pendientes"], 1)
# Solo le falta el coste del pago de la comisión de desarrollo (~1 sat). Contarla como si no
# existiera borraría su beneficio entero de los informes mientras dure la espera.
comprobar("su neto provisional sí entra en la suma", a["neto"], 506 + 494)
comprobar("y el routing ya conocido también", a["routing"], 4 + 16)
comprobar("el acumulado del aviso la cuenta igual", totales(con), (506_000 + 494_155, 2))
comprobar("y está en la lista de reintentos", ids_a_reintentar(con), ["a"])

# Cuando aparece el pago que faltaba se completa la misma fila, sin tocar lo demás.
completa = dict(pendiente, devs=(1013, 0), estado="ok", motivo="", hash_devs=H_DEVS)
antes_fila = con.execute("SELECT fee, notified_at, completed_at FROM earnings WHERE order_id='a'").fetchone()
neto = guardar_resolucion(con, orden(id="a"), completa, ahora=3_000_000)
despues_fila = con.execute("SELECT fee, notified_at, completed_at FROM earnings WHERE order_id='a'").fetchone()
comprobar("al resolverse calcula el neto", neto, 510_000 - 16_858)
comprobar("sin duplicar la fila", con.execute("SELECT COUNT(*) FROM earnings WHERE order_id='a'").fetchone()[0], 1)
comprobar("y sin cambiar su fee, su fecha de aviso ni su fecha", despues_fila, antes_fila)
comprobar("deja de estar pendiente", (consultar(con, *TODO)["pendientes"], ids_a_reintentar(con)), (0, []))
comprobar("y su neto pasa de provisional a exacto", consultar(con, *TODO)["neto"], 999)

# Sin el coste del comprador no hay base para estimar nada: el error podría ser grande.
sin_comprador = {"comprador": None, "devs": None, "estado": "pendiente", "motivo": "x",
                 "hash_comprador": None, "hash_devs": None}
con2 = base_con_fila_antigua()
migrar_esquema(con2)
guardar_resolucion(con2, orden(id="b"), sin_comprador, ahora=2_000_000)
comprobar("una pendiente sin el coste del comprador no suma a ciegas",
          (consultar(con2, *TODO)["neto"], consultar(con2, *TODO)["pendientes"]), (506, 1))

# Lo interno se ve aparte y no resta del neto.
con = sqlite3.connect(":memory:")
con.execute(ESQUEMA_ORIGINAL)
migrar_esquema(con)
neto = guardar_resolucion(con, orden(id="i", fee=300, dev_fee=0, dev_fee_hash=""),
                          {"comprador": (3000, 1500), "devs": (0, 0), "estado": "ok", "motivo": "",
                           "hash_comprador": "7" * 64, "hash_devs": None}, ahora=2_000_000)
resumen = consultar(con, *TODO)
comprobar("el coste entre tus nodos sale aparte", (resumen["routing"], resumen["routing_interno"]), (2, 2))
comprobar("y no resta del neto", neto, 600_000 - 1500)

# Las pendientes viejas dejan de reintentarse, pero siguen contando como no resueltas.
con = sqlite3.connect(":memory:")
con.execute(ESQUEMA_ORIGINAL)
migrar_esquema(con)
guardar_resolucion(con, orden(id="viejo", taken_at=1_000), pendiente, ahora=1_000)
guardar_resolucion(con, orden(id="reciente", taken_at=90 * 86400), pendiente, ahora=90 * 86400)
caducar_pendientes(con, ahora=100 * 86400, max_dias=30)
comprobar("tras el plazo, la pendiente se da por perdida y no se reintenta", ids_a_reintentar(con), ["reciente"])
comprobar("y sigue figurando como sin resolver en los informes", consultar(con, *TODO)["pendientes"], 2)


print()
if fallos:
    print(f"{len(fallos)} prueba(s) fallida(s)")
    sys.exit(1)
print("Todas las pruebas pasan")
