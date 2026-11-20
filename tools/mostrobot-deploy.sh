#!/usr/bin/env bash
# mostrobot-deploy.sh — Deploy code changes from ~/mostro-sources/scripts to /opt/mostrobot.
# Runs the test suite first, never touches state files (*.json, *.db) or .env, refreshes the
# read-only LND certificates (LND1 uses tlsautorefresh) and restarts the bots.
#
#   sudo bash ~/mostro-sources/scripts/tools/mostrobot-deploy.sh
set -euo pipefail

SRC=/home/admin/mostro-sources/scripts
DST=/opt/mostrobot
UNITS=(mostrobot mostrobot-nostr mostro-accounting)

[ "$(id -u)" -eq 0 ] || { echo "Ejecuta con sudo" >&2; exit 1; }

echo "== tests"
sudo -u admin bash "$SRC/run-tests.sh" > /tmp/mostrobot-tests.log 2>&1 || {
    tail -20 /tmp/mostrobot-tests.log; echo "Tests en rojo: no se despliega" >&2; exit 1; }
tail -1 /tmp/mostrobot-tests.log; rm -f /tmp/mostrobot-tests.log

echo "== código (sin estado ni .env)"
rsync -a --itemize-changes \
    --exclude '__pycache__' --include 'admin/' --include 'admin/env.sh' --exclude 'admin/*' --exclude 'systemd/' --exclude 'tools/' \
    --exclude 'app.log' --exclude '.env' --exclude '.env.example' \
    --exclude '*.json' --exclude '*.db' --exclude '*.db.*' --exclude '*.db-*' \
    "$SRC/" "$DST/scripts/" | grep -v '^\.d' || true
chown -R mostrobot:mostrobot "$DST/scripts"

echo "== certificados de LND (solo lectura)"
install -o mostrobot -g mostrobot -m 644 /data/lnd/tls.cert  "$DST/lnd/lnd1-tls.cert"
install -o mostrobot -g mostrobot -m 644 /data/lnd2/tls.cert "$DST/lnd/lnd2-tls.cert"

echo "== reiniciar"
systemctl restart "${UNITS[@]}"
sleep 5
for u in "${UNITS[@]}"; do echo "   $u: $(systemctl is-active "$u")"; done
