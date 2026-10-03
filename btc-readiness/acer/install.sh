#!/usr/bin/env bash
# Deploy the BTC study release to the Acer (homelab) from this Mac.
#
#   ./install.sh stage      copy the release, install units, start the attest + watch timers
#                           (GET-only checks and Telegram health alerts; NO collection)
#   ./install.sh activate   enable the collector timer; it starts at the protocol's start_utc
#   ./install.sh status     print the Acer health report
#   ./install.sh rollback   stop and disable every BTC-study unit; keeps all evidence
#
# Nothing here places orders or changes the Kalshi account. "activate" refuses unless the
# release on the Acer has no QA_ONLY marker and has an activation_approval.json.
set -euo pipefail

HOST=${BTC_STUDY_HOST:-acer}
HERE=$(cd "$(dirname "$0")" && pwd)
RELEASE=${BTC_STUDY_RELEASE:-"$HERE/../qa/readiness_release/candidate"}
UNITS="$HERE/systemd"
VERSION=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["candidate_release"])' "$RELEASE/release_manifest.json")
START=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["start_utc"].replace("T"," ").replace("+00:00"," UTC"))' "$RELEASE/readiness_protocol.json")

remote() { ssh -o BatchMode=yes "$HOST" "$@"; }

case "${1:-}" in
  stage)
    remote 'test -f ~/.config/btc-study/kalshi_readonly_config.json && test -f ~/.config/btc-study/telegram.json' \
      || { echo "Missing ~/.config/btc-study/{kalshi_readonly_config.json,telegram.json} on $HOST (see README)"; exit 1; }
    remote "mkdir -p ~/btc-study/releases/$VERSION ~/btc-study/state ~/.config/systemd/user && chmod 700 ~/btc-study ~/btc-study/state"
    rsync -a --delete --exclude __pycache__ --exclude runtime --exclude activation_approval.json \
      "$RELEASE/" "$HOST:btc-study/releases/$VERSION/"
    remote "cd ~/btc-study && ln -sfn releases/$VERSION current"
    sed "s|__START_UTC__|$START|" "$UNITS/btc-study-collector.timer" > "${TMPDIR:-/tmp}/btc-study-collector.timer"
    scp -q "$UNITS"/*.service "$UNITS"/btc-study-attest.timer "$UNITS"/btc-study-watch.timer \
      "${TMPDIR:-/tmp}/btc-study-collector.timer" "$HOST:.config/systemd/user/"
    remote 'systemctl --user daemon-reload && systemctl --user enable --now btc-study-attest.timer btc-study-watch.timer \
      && systemctl --user start btc-study-attest.service; python3 -B ~/btc-study/current/acer_ops.py status'
    echo "Staged $VERSION on $HOST. Collector NOT enabled. Start time when activated: $START"
    ;;
  activate)
    remote "test ! -e ~/btc-study/current/QA_ONLY && test -f ~/btc-study/current/activation_approval.json" \
      || { echo "Refusing: release is a QA candidate or has no activation_approval.json"; exit 1; }
    remote 'systemctl --user enable --now btc-study-collector.timer && systemctl --user list-timers "btc-study-*"'
    ;;
  status)
    remote 'python3 -B ~/btc-study/current/acer_ops.py status'
    ;;
  rollback)
    remote 'systemctl --user disable --now btc-study-collector.timer btc-study-attest.timer btc-study-watch.timer 2>/dev/null;
            systemctl --user stop btc-study-collector.service 2>/dev/null; systemctl --user list-units "btc-study-*" --all'
    echo "Stopped. Evidence in ~/btc-study/current/runtime on $HOST is untouched."
    ;;
  *)
    sed -n '2,12p' "$0"; exit 2 ;;
esac
