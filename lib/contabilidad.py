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


def consultar(con, inicio, fin):
    """Agregados de las operaciones liquidadas en [inicio, fin), epochs."""
    # completed_at guarda en realidad el taken_at de la orden: el momento en que se tomó,
    # no en el que se liquidó. La diferencia son minutos y solo importaría para una
    # operación a caballo entre dos periodos. Se deja así porque cambiar el criterio
    # reasignaría de mes operaciones ya notificadas en su día.
    fila = con.execute(
        """
        SELECT COUNT(*),
               COALESCE(SUM(net_profit), 0),
               COALESCE(SUM(fee), 0),
               COALESCE(SUM(dev_fee), 0),
               COALESCE(SUM(routing_buyer + routing_devs), 0),
               COALESCE(SUM(amount), 0)
        FROM earnings
        WHERE completed_at >= ? AND completed_at < ?
        """,
        (inicio, fin),
    ).fetchone()
    return {
        "operaciones": fila[0],
        "neto": fila[1],
        "fee": fila[2],
        "dev_fee": fila[3],
        "routing": fila[4],
        "volumen": fila[5],
    }
