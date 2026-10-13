#!/bin/bash
# Install the Netgear router watchdog for this user (no sudo needed). Run on the Acer:
#   bash ~/router-watchdog/setup.sh            # asks for the router admin password, then installs
#   bash ~/router-watchdog/setup.sh status
#   bash ~/router-watchdog/setup.sh uninstall
# The password is typed here, stored only in ~/.config/router-watchdog/netgear.json (0600),
# and never echoed.
set -euo pipefail
DIR="$HOME/router-watchdog" CFG="$HOME/.config/router-watchdog/netgear.json"
UNIT="$HOME/.config/systemd/user/router-watchdog.service"
PY="$DIR/venv/bin/python"

case "${1:-install}" in
status)
  systemctl --user is-active router-watchdog.service || true
  tail -n 8 "$DIR/state/events.jsonl" 2>/dev/null || echo "no events yet"
  exit 0;;
uninstall)
  systemctl --user disable --now router-watchdog.service 2>/dev/null || true
  rm -f "$UNIT" "$CFG"; systemctl --user daemon-reload; echo "removed (venv and logs kept in $DIR)"; exit 0;;
install) ;;
*) echo "usage: $0 [install|status|uninstall]" >&2; exit 2;;
esac

[ -x "$PY" ] || { echo "venv missing: $DIR/venv" >&2; exit 1; }
read -rp "Netgear admin username [admin]: " USER_NAME; USER_NAME=${USER_NAME:-admin}
read -rsp "Netgear admin password (the router's web admin password): " PASS; echo
[ -n "$PASS" ] || { echo "password required" >&2; exit 1; }
umask 077; mkdir -p "$(dirname "$CFG")"
RW_USER="$USER_NAME" RW_PASS="$PASS" "$PY" -c '
import json,os,sys
json.dump({"host":"192.168.1.1","user":os.environ["RW_USER"],"password":os.environ["RW_PASS"]},open(sys.argv[1],"w"))' "$CFG"
chmod 600 "$CFG"; unset PASS

echo "Checking the login (this does NOT reboot anything)..."
"$PY" "$DIR/router_watchdog.py" --check

mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<EOF
[Unit]
Description=Netgear router watchdog: reboot the router when the internet stays down
After=network-online.target

[Service]
ExecStart=$PY -u $DIR/router_watchdog.py
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now router-watchdog.service
sleep 3; "$0" status
