#!/usr/bin/env bash
# run-tests.sh — Lanza todas las comprobaciones del repositorio.
#
# Cada test es un script suelto con asserts, sin framework, ejecutable por su cuenta. Este
# runner solo los encadena y devuelve un código de salida útil para un hook o un CI.
#
# Los tests de bot/ se ejecutan desde su propio directorio porque importan common.py, que
# Python encuentra por estar junto al script.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fallos=0

ejecutar() {
    local dir="$1" script="$2"
    echo "── $script ──"
    if ( cd "$REPO/$dir" && python3 "$script" ); then
        :
    else
        fallos=$((fallos + 1))
    fi
    echo
}

ejecutar lib test-lib.py
ejecutar bot test-reconciliacion.py

# Comprobación de sintaxis: barata y atrapa el error más tonto antes de reiniciar un
# servicio en producción.
echo "── sintaxis ──"
if find "$REPO" -name '*.py' -not -path '*/__pycache__/*' -exec python3 -m py_compile {} + ; then
    echo "  ✅ todos los .py compilan"
else
    echo "  ❌ hay ficheros .py que no compilan"
    fallos=$((fallos + 1))
fi

if find "$REPO" -name '*.sh' -exec bash -n {} + ; then
    echo "  ✅ todos los .sh son sintácticamente válidos"
else
    echo "  ❌ hay ficheros .sh con errores de sintaxis"
    fallos=$((fallos + 1))
fi

echo
if [ "$fallos" -gt 0 ]; then
    echo "$fallos comprobación(es) fallida(s)"
    exit 1
fi
echo "Todo en verde"
