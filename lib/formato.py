"""
formato.py — Formateo de cifras y fechas con las convenciones españolas.

Punto para los miles y coma para los decimales, que es al revés que en Python. Estaba
escrito tres veces (common.py, accounting.py, informe-mensual.py) con pequeñas diferencias
entre las copias.

Los meses en castellano y en minúscula: strftime('%B') depende del locale del proceso, y
cron no hereda el del operador, así que un informe lanzado por cron saldría en inglés.
"""

MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def fecha_larga(fecha):
    """date(2026, 8, 12) -> '12 de agosto'. Sin año: quien lo lee sabe en cuál está."""
    return f"{fecha.day} de {MESES[fecha.month - 1]}"


def formato_sats(valor):
    """12345 -> '12.345'.

    Devuelve el valor tal cual si no es un número. Eso es lo que hacía la copia de los
    bots, y cambiarlo por str(valor) convertiría un None en la cadena 'None' dentro de un
    mensaje publicado, en vez de dejarlo visiblemente vacío.
    """
    try:
        return f"{int(valor):,}".replace(",", ".")
    except (ValueError, TypeError):
        return valor


def formato_euros(valor):
    """5.938 -> '5,94'. Miles con punto y decimales con coma."""
    return f"{valor:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def con_signo(n):
    """Diferencia con signo explícito: '+1.200', '−300', '0'.

    El menos es el tipográfico (U+2212), no el guion, para que case con el resto del
    desglose de los informes.
    """
    if n > 0:
        return f"+{formato_sats(n)}"
    if n < 0:
        return f"−{formato_sats(abs(n))}"
    return "0"
