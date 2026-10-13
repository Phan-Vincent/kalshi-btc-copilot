#!/bin/bash
# Install the Wi-Fi WAN backup on the Acer. Run as root from this folder:
#   sudo bash install.sh            # asks for the backup network's SSID and password
#   sudo bash install.sh status
#   sudo bash install.sh uninstall
# The password is typed here, stored only in a root-only netplan file, and never echoed.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
NETPLAN=/etc/netplan/60-wifi-backup.yaml UNIT=/etc/systemd/system/wan-failover.service BIN=/usr/local/sbin/wan-failover
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

case "${1:-install}" in
status)
  cat /run/wan-failover.state 2>/dev/null || echo "no state yet"
  systemctl is-active wan-failover.service || true
  ip -4 route show default
  networkctl status wlp3s0 --no-pager 2>/dev/null | sed -n '1,12p'
  journalctl -u wan-failover.service -n 10 --no-pager
  exit 0;;
uninstall)
  systemctl disable --now wan-failover.service 2>/dev/null || true
  rm -f "$UNIT" "$BIN" "$NETPLAN"; systemctl daemon-reload; netplan apply
  echo "removed"; exit 0;;
install) ;;
*) echo "usage: $0 [install|status|uninstall]" >&2; exit 2;;
esac

read -rp "Backup Wi-Fi SSID (hotspot or backup gateway; NOT your home router): " SSID
read -rsp "Password: " PSK; echo
[ -n "$SSID" ] && [ ${#PSK} -ge 8 ] || { echo "SSID required and WPA password must be at least 8 characters" >&2; exit 1; }

umask 077
SSID="$SSID" PSK="$PSK" python3 - "$NETPLAN" <<'EOF'
import json, os, sys
q = json.dumps  # JSON strings are valid YAML scalars, so any characters are safe
open(sys.argv[1], 'w').write(f'''# Wi-Fi WAN backup for wan-failover; written by install.sh
network:
  version: 2
  wifis:
    wlp3s0:
      optional: true
      dhcp4: true
      dhcp4-overrides:
        route-metric: 600
      access-points:
        {q(os.environ["SSID"])}:
          password: {q(os.environ["PSK"])}
''')
EOF
chmod 600 "$NETPLAN"
unset PSK

install -m 755 "$HERE/wan-failover" "$BIN"
cat > "$UNIT" <<'EOF'
[Unit]
Description=WAN failover: wired preferred, Wi-Fi backup when wired can't reach the internet
After=network-online.target systemd-networkd.service
Wants=network-online.target

[Service]
ExecStart=/usr/local/sbin/wan-failover
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

netplan generate
netplan apply
systemctl daemon-reload
systemctl enable --now wan-failover.service
sleep 20
echo "--- installed ---"
"$0" status
