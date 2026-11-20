#!/usr/bin/env bash
# mostrobot-setup.sh — Move the NostroMostro bots (Telegram, Nostr, accounting, premiums,
# monthly report) from user admin to a dedicated, unprivileged user "mostrobot" in /opt/mostrobot.
#
#   sudo bash mostrobot-setup.sh preparar   # user, code, state, .env, read-only macaroons, venv. No service changes.
#   sudo bash mostrobot-setup.sh activar    # hardened units + timers, crontab cleanup, restart.
#   sudo bash mostrobot-setup.sh comprobar  # isolation checks only.
#
# Plan: ~/Documentos/Mostro/mostro/odd/tasks/mostrobot-isolation.md (on the desktop PC).
set -euo pipefail

SRC=/home/admin/mostro-sources/scripts
WEB_SRC=/home/admin/nostromostro.github.io
DST=/opt/mostrobot
SNAP=/var/lib/mostro-snapshot
UNITS=(mostrobot mostrobot-nostr mostro-accounting)
LND1_DIR=/data/lnd
LND1_MAC=$LND1_DIR/data/chain/bitcoin/mainnet/readonly.macaroon
LND2_DIR=/data/lnd2
LND2_MAC=$LND2_DIR/data/chain/bitcoin/mainnet/readonly.macaroon

[ "$(id -u)" -eq 0 ] || { echo "Ejecuta con sudo" >&2; exit 1; }
as_bot() { sudo -u mostrobot -H "$@"; }

copiar_credenciales_lnd() {
    install -d -o mostrobot -g mostrobot -m 700 "$DST/lnd"
    install -o mostrobot -g mostrobot -m 600 "$LND1_MAC"          "$DST/lnd/lnd1-readonly.macaroon"
    install -o mostrobot -g mostrobot -m 644 "$LND1_DIR/tls.cert" "$DST/lnd/lnd1-tls.cert"
    install -o mostrobot -g mostrobot -m 600 "$LND2_MAC"          "$DST/lnd/lnd2-readonly.macaroon"
    install -o mostrobot -g mostrobot -m 644 "$LND2_DIR/tls.cert" "$DST/lnd/lnd2-tls.cert"
}

preparar() {
    echo "== paquetes"
    NEEDRESTART_SUSPEND=1 DEBIAN_FRONTEND=noninteractive apt-get install -y python3.10-venv rsync >/dev/null

    echo "== usuario mostrobot"
    id mostrobot >/dev/null 2>&1 || useradd --system --user-group --home-dir "$DST" --shell /usr/sbin/nologin mostrobot
    install -d -o mostrobot -g mostrobot -m 750 "$DST"

    echo "== código y estado (copia inicial completa)"
    rsync -a --delete \
        --exclude '__pycache__' --exclude 'admin/' --exclude 'systemd/' --exclude 'tools/' \
        --exclude 'app.log' --exclude '.env' --exclude '.env.example' \
        "$SRC/" "$DST/scripts/"
    chown -R mostrobot:mostrobot "$DST/scripts"

    echo "== .env solo con lo que usan los bots"
    grep -vE '^(MOSTROD_|MOSTRIX_|WATCHDOG_|BACKUP_DIR=|MOSTRO_DB=|MOSTRO_LOG=|BOT_SERVICE=|LND1_|LND2_)' "$SRC/.env" > "$DST/scripts/.env"
    cat >> "$DST/scripts/.env" <<EOF
LND1_RPCSERVER=localhost:10009
LND1_TLSCERT=$DST/lnd/lnd1-tls.cert
LND1_MACAROON=$DST/lnd/lnd1-readonly.macaroon
LND2_TLSCERT=$DST/lnd/lnd2-tls.cert
LND2_MACAROON=$DST/lnd/lnd2-readonly.macaroon
EOF
    chown mostrobot:mostrobot "$DST/scripts/.env"; chmod 600 "$DST/scripts/.env"

    echo "== macaroons de solo lectura"
    copiar_credenciales_lnd

    echo "== repositorio web de premiums y su deploy key"
    rsync -a --delete "$WEB_SRC/" "$DST/nostromostro.github.io/"
    chown -R mostrobot:mostrobot "$DST/nostromostro.github.io"
    install -d -o mostrobot -g mostrobot -m 700 "$DST/.ssh"
    install -o mostrobot -g mostrobot -m 600 /home/admin/.ssh/id_nostromostro "$DST/.ssh/id_nostromostro"
    cat > "$DST/.ssh/config" <<EOF
Host github-nostromostro
  HostName github.com
  User git
  IdentityFile $DST/.ssh/id_nostromostro
  IdentitiesOnly yes
EOF
    ssh-keygen -F github.com -f /home/admin/.ssh/known_hosts > "$DST/.ssh/known_hosts" || true
    { grep -v '^#' "$DST/.ssh/known_hosts" || true; } > "$DST/.ssh/known_hosts.tmp"; mv "$DST/.ssh/known_hosts.tmp" "$DST/.ssh/known_hosts"
    [ -s "$DST/.ssh/known_hosts" ] || echo "   AVISO: no se encontró github.com en el known_hosts de admin; el primer push de premiums fallará"
    chown mostrobot:mostrobot "$DST/.ssh/config" "$DST/.ssh/known_hosts"; chmod 600 "$DST/.ssh/config" "$DST/.ssh/known_hosts"
    as_bot git config --global user.name "$(sudo -u admin git -C "$WEB_SRC" config user.name || echo NostroMostro)"
    as_bot git config --global user.email "$(sudo -u admin git -C "$WEB_SRC" config user.email || echo bot@nostromostro)"

    echo "== entorno virtual de Python"
    [ -x "$DST/venv/bin/python" ] || as_bot python3 -m venv "$DST/venv"
    as_bot "$DST/venv/bin/pip" install --quiet --disable-pip-version-check \
        python-dotenv==1.2.2 pynostr==0.7.0 requests==2.32.5 websocket-client==1.9.0

    echo "== acceso de solo lectura a la instantánea de mostro.db"
    groupadd -f mostrodb
    usermod -aG mostrodb mostrobot
    usermod -aG mostrodb admin
    chgrp -R mostrodb "$SNAP"; chmod 2750 "$SNAP"

    echo "== preparado. Los servicios siguen como estaban."
    comprobar
}

escribir_unidad() {  # nombre, directorio, comando, líneas extra de [Service], instalar (si/no)
    cat > "/etc/systemd/system/$1.service" <<EOF
[Unit]
Description=NostroMostro $1 (user mostrobot)
After=network-online.target mostro.service
Wants=network-online.target

[Service]
User=mostrobot
Group=mostrobot
Environment=HOME=$DST
Environment=NOSTROMOSTRO_WEB_REPO=$DST/nostromostro.github.io
WorkingDirectory=$2
ExecStart=$3
$4
StandardOutput=journal
StandardError=journal

# Hardening: no privilege gain, read-only system, no /home, writes only under $DST.
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$DST
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
CapabilityBoundingSet=
SystemCallArchitectures=native
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
EOF
    if [ "$5" = si ]; then printf '\n[Install]\nWantedBy=multi-user.target\n' >> "/etc/systemd/system/$1.service"; fi
}

activar() {
    echo "== copia de las unidades y del crontab actuales"
    B=/root/mostrobot-backup-$(date +%Y%m%d-%H%M%S); mkdir -p "$B"
    for u in "${UNITS[@]}"; do cp -a "/etc/systemd/system/$u.service" "$B/"; done
    crontab -u admin -l > "$B/crontab-admin" 2>/dev/null || true
    echo "   en $B"

    echo "== parar los bots antiguos y volver a copiar el estado (pudo cambiar desde preparar)"
    systemctl stop "${UNITS[@]}"
    rsync -a \
        --exclude '__pycache__' --exclude 'admin/' --exclude 'systemd/' --exclude 'tools/' \
        --exclude 'app.log' --exclude '.env' --exclude '.env.example' \
        "$SRC/" "$DST/scripts/"
    rsync -a "$WEB_SRC/" "$DST/nostromostro.github.io/"
    chown -R mostrobot:mostrobot "$DST/scripts" "$DST/nostromostro.github.io"

    echo "== .env: LND1 por nombre (sin SNI presenta un certificado efímero caducado)"
    grep -q '^LND1_RPCSERVER=' "$DST/scripts/.env" || echo 'LND1_RPCSERVER=localhost:10009' >> "$DST/scripts/.env"

    echo "== unidades endurecidas"
    PY="$DST/venv/bin/python -u"
    escribir_unidad mostrobot          "$DST/scripts/bot"        "$PY $DST/scripts/bot/bot.py"               $'Restart=always\nRestartSec=10' si
    escribir_unidad mostrobot-nostr    "$DST/scripts/bot"        "$PY $DST/scripts/bot/bot-nostr.py"         $'Restart=always\nRestartSec=10' si
    escribir_unidad mostro-accounting  "$DST/scripts/accounting" "$PY $DST/scripts/accounting/accounting.py" $'Restart=always\nRestartSec=30' si
    escribir_unidad mostrobot-premiums "$DST/scripts/bot"        "/bin/bash $DST/scripts/bot/premiums.sh"    'Type=oneshot' no
    escribir_unidad mostrobot-informe  "$DST/scripts/accounting" "$PY $DST/scripts/accounting/informe-mensual.py" 'Type=oneshot' no
    cat > /etc/systemd/system/mostrobot-premiums.timer <<EOF
[Unit]
Description=Daily premiums export (user mostrobot)
[Timer]
OnCalendar=*-*-* 00:00:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
    cat > /etc/systemd/system/mostrobot-informe.timer <<EOF
[Unit]
Description=Monthly accounting report (user mostrobot)
[Timer]
OnCalendar=*-*-01 00:30:00
Persistent=true
[Install]
WantedBy=timers.target
EOF

    echo "== quitar premiums e informe del crontab de admin"
    crontab -u admin -l | grep -vE 'bot/premiums\.sh|accounting/informe-mensual\.py' | crontab -u admin -

    echo "== arrancar"
    systemctl daemon-reload
    systemctl restart "${UNITS[@]}"
    systemctl enable --now mostrobot-premiums.timer mostrobot-informe.timer
    sleep 8
    for u in "${UNITS[@]}"; do echo "   $u: $(systemctl is-active "$u") como $(systemctl show "$u" -p User --value)"; done
    comprobar
}

comprobar() {
    echo "== comprobaciones de aislamiento (como mostrobot)"
    ok() { if "$@" >/dev/null 2>&1; then echo "MAL"; else echo "OK"; fi; }
    echo "   leer admin.macaroon de LND1:  $(ok as_bot test -r $LND1_DIR/data/chain/bitcoin/mainnet/admin.macaroon)"
    echo "   leer admin.macaroon de LND2:  $(ok as_bot test -r $LND2_DIR/data/chain/bitcoin/mainnet/admin.macaroon)"
    echo "   leer /opt/mostro (clave):     $(ok as_bot test -r /opt/mostro/settings.toml)"
    echo "   entrar en /home/admin:        $(ok as_bot ls /home/admin)"
    echo "   usar sudo:                    $(ok as_bot sudo -n true)"
    echo "   grupos: $(id -nG mostrobot)"
    echo "== lo que sí debe poder hacer"
    as_bot test -r "$SNAP/mostro.db" && echo "   leer la instantánea:          OK" || echo "   leer la instantánea:          MAL"
    # LND1 must be reached by name: without SNI (plain IP) it presents an expired ephemeral certificate.
    for par in 1:localhost:10009 2:127.0.0.1:10010; do
        n=${par%%:*}; servidor=${par#*:}
        if as_bot lncli --rpcserver=$servidor --tlscertpath=$DST/lnd/lnd$n-tls.cert \
               --macaroonpath=$DST/lnd/lnd$n-readonly.macaroon getinfo >/dev/null 2>&1; then
            echo "   lnd$n getinfo (solo lectura):  OK"
        else
            echo "   lnd$n getinfo (solo lectura):  MAL"
        fi
    done
    salida=$(as_bot lncli --rpcserver=localhost:10009 --tlscertpath=$DST/lnd/lnd1-tls.cert \
           --macaroonpath=$DST/lnd/lnd1-readonly.macaroon newaddress p2wkh 2>&1 || true)
    if echo "$salida" | grep -q '"address"'; then
        echo "   lnd1 escribir con ese macaroon: MAL (no debería poder)"
    elif echo "$salida" | grep -qi 'permission denied'; then
        echo "   lnd1 escribir con ese macaroon: OK (rechazado por permisos)"
    else
        echo "   lnd1 escribir con ese macaroon: NO CONCLUYENTE: $(echo "$salida" | head -1 | cut -c1-100)"
    fi
}

case "${1:-}" in
    preparar)  preparar ;;
    activar)   activar ;;
    comprobar) comprobar ;;
    *) echo "uso: sudo bash $0 preparar|activar|comprobar" >&2; exit 2 ;;
esac
