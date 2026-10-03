#!/usr/bin/env bash
# Deploy the TA dashboard to the Acer as a systemd user service, published
# tailnet-only at https://homelab.example-tailnet.ts.net:11443 (no Funnel).
#   ./deploy.sh          copy files, (re)start the service and watcher
#   ./deploy.sh status   service + Serve state and a local health probe
#   ./deploy.sh remove   stop/disable the service and remove the Serve mapping (files kept)
set -euo pipefail
HOST=acer
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/.."
PORT=8770
SERVE_PORT=11443
remote() { ssh -o BatchMode=yes "$HOST" "$@"; }

status() {
  remote "systemctl --user --no-pager status btc-ta-copilot.service | head -5
          systemctl --user --no-pager status btc-ta-watch.timer | head -5
          tailscale serve status 2>/dev/null | grep -A1 ':$SERVE_PORT' || echo 'no Serve mapping on :$SERVE_PORT'
          curl -s -m 5 http://127.0.0.1:$PORT/api/state?tf=1m | python3 -c 'import json,sys; s=json.load(sys.stdin); print(\"ready\", s.get(\"ready\"), \"stale\", s.get(\"stale\"), \"composite\", s.get(\"composite\"), \"errors\", s.get(\"errors\"), \"frames\", list(s.get(\"frames\", {})))'"
}

case "${1:-deploy}" in
  deploy)
    (cd "$SRC" && python3 -m unittest discover -q -p 'test_*.py')
    remote 'mkdir -p ~/btc-ta/state ~/.config/systemd/user && chmod 700 ~/btc-ta ~/btc-ta/state'
    scp -q "$SRC/ta_copilot.py" "$SRC/stream_flow.py" "$SRC/ta_watch.py" "$SRC/tally_report.py" "$SRC/index.html" "$SRC/app.js" "$SRC/README.md" "$SRC"/test_*.py "$HOST:btc-ta/"
    scp -q "$HERE/btc-ta-copilot.service" "$HERE/btc-ta-watch.service" "$HERE/btc-ta-watch.timer" "$HOST:.config/systemd/user/"
    remote "set -e
            cd ~/btc-ta && python3 -m unittest discover -q -p 'test_*.py'
            systemctl --user daemon-reload
            systemctl --user enable btc-ta-copilot.service >/dev/null 2>&1
            systemctl --user restart btc-ta-copilot.service
            systemctl --user enable --now btc-ta-watch.timer >/dev/null 2>&1
            tailscale serve status 2>/dev/null | grep -q ':$SERVE_PORT'"
    sleep 8
    status
    ;;
  status) status ;;
  remove)
    remote "systemctl --user disable --now btc-ta-watch.timer btc-ta-copilot.service; systemctl --user stop btc-ta-watch.service; tailscale serve --https=$SERVE_PORT off || true"
    ;;
  *) echo "usage: $0 [deploy|status|remove]" >&2; exit 2 ;;
esac
