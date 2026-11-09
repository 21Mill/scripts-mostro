"""
contabilidad.py — Consultas sobre accounting.db, la contabilidad de la instancia.

Una fila por operación liquidada, con el beneficio neto ya calculado por accounting.py.
Aquí solo se agrega y solo se lee: la base se abre en modo ro para no poder interferir con
mostro-accounting.service, que le está escribiendo mientras tanto.

Lo usan el informe mensual y el bloque privado del resumen diario, que solo se diferencian
en la ventana que le pasan a consultar().
"""

import sqlite3
from datetime import datetime, timedelta

from lib.ganancias import SQL_NETO_MSAT, redondear_sats


def abrir(ruta):
    """Conexión de solo lectura. Falla si el fichero no existe, en vez de crearlo vacío."""
    return sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)


def rango_mes(anio, mes):
    """Límites del mes en hora local, como epoch.

    datetime(...).timestamp() interpreta en la zona del sistema, que es lo que queremos:
    "agosto" es el agosto del operador, no el de UTC. Se devuelve el inicio del mes
    siguiente en vez del final del actual para no depender del número de días ni de los
    cambios de horario.
    """
    inicio = datetime(anio, mes, 1).timestamp()
    if mes == 12:
        fin = datetime(anio + 1, 1, 1).timestamp()
    else:
        fin = datetime(anio, mes + 1, 1).timestamp()
    return int(inicio), int(fin)


def rango_dia(fecha):
    """Límites del día en hora local, como epoch, para un date o un datetime.

    Mismo criterio que rango_mes y que la agrupación 'localtime' del gráfico: el día del
    operador, no el de UTC. El día siguiente sale de timedelta y no de fecha.day + 1, que
    se rompería a fin de mes.
    """
    inicio = datetime(fecha.year, fecha.month, fecha.day)
    fin = inicio + timedelta(days=1)
    return int(inicio.timestamp()), int(fin.timestamp())


def _tiene_msat(con):
    return "net_profit_msat" in {fila[1] for fila in con.execute("PRAGMA table_info(earnings)")}


def consultar(con, inicio, fin):
    """Agregados de las operaciones liquidadas en [inicio, fin), epochs.

    Suma en milisatoshis y redondea una sola vez al final, de modo que el total del mes no
    arrastra el error de redondear cada operación. Las filas anteriores a las columnas en
    msat entran con sus sats enteros, así que el histórico no se pierde si aún no se ha
    recalculado.

    routing es lo que cobró la red; routing_interno, lo que cobraron tus propios nodos
    entre sí (no sale de tu bolsillo, por eso no está en routing ni en el neto).
    pendientes cuenta las operaciones cuyo coste aún no se ha podido averiguar: su neto no
    entra en la suma, y conviene que quien lea el informe lo sepa.
    """
    # completed_at guarda en realidad el taken_at de la orden: el momento en que se tomó,
    # no en el que se liquidó. La diferencia son minutos y solo importaría para una
    # operación a caballo entre dos periodos. Se deja así porque cambiar el criterio
    # reasignaría de mes operaciones ya notificadas en su día.
    if _tiene_msat(con):
        neto = SQL_NETO_MSAT
        comprador = "COALESCE(routing_buyer_msat - routing_buyer_interno_msat, routing_buyer * 1000)"
        devs = "COALESCE(routing_devs_msat - routing_devs_interno_msat, routing_devs * 1000)"
        interno = "COALESCE(routing_buyer_interno_msat, 0) + COALESCE(routing_devs_interno_msat, 0)"
        pendiente = "CASE WHEN routing_estado IN ('pendiente', 'ambiguo', 'sin_datos') THEN 1 ELSE 0 END"
    else:
        neto = "net_profit * 1000"
        comprador = "routing_buyer * 1000"
        devs = "routing_devs * 1000"
        interno = "0"
        pendiente = "0"

    fila = con.execute(
        f"""
        SELECT COUNT(*),
               COALESCE(SUM({neto}), 0),
               COALESCE(SUM(fee), 0),
               COALESCE(SUM(dev_fee), 0),
               COALESCE(SUM(COALESCE({comprador}, 0) + COALESCE({devs}, 0)), 0),
               COALESCE(SUM({interno}), 0),
               COALESCE(SUM(amount), 0),
               COALESCE(SUM({pendiente}), 0)
        FROM earnings
        WHERE completed_at >= ? AND completed_at < ?
        """,
        (inicio, fin),
    ).fetchone()
    return {
        "operaciones": fila[0],
        "neto": redondear_sats(fila[1]),
        "fee": fila[2],
        "dev_fee": fila[3],
        "routing": redondear_sats(fila[4]),
        "routing_interno": redondear_sats(fila[5]),
        "volumen": fila[6],
        "pendientes": fila[7],
    }
