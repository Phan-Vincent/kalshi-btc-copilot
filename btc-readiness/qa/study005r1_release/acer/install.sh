#!/usr/bin/env bash
# Deploy the BTC study release to the Acer (homelab) from this Mac.
#
#   ./install.sh stage      copy the release, install units, start the attest + watch timers
#                           (GET-only checks and Telegram health alerts; NO collection)
#   ./install.sh activate   enable the collector timer; it starts at the protocol's start_utc
#   ./install.sh approve    sign the evidence-only approval interactively on the Acer (owner only)
#   ./install.sh status     print the Acer health report (hash-checks acer_ops.py first)
#   ./install.sh send-test  send a Telegram test message (hash-checks acer_ops.py first)
#   ./install.sh rollback   stop and disable every BTC-study unit; keeps all evidence
#
# Nothing here places orders or changes the Kalshi account. "activate" refuses unless the
# release on the Acer has no QA_ONLY marker and has an activation_approval.json.
set -euo pipefail

HOST=${BTC_STUDY_HOST:-acer}
HERE=$(cd "$(dirname "$0")" && pwd)
RELEASE=${BTC_STUDY_RELEASE:-"$HERE/../sealed"}  # the output of: release_tool.py seal ../sealed
UNITS="$RELEASE"  # units are sealed into the release (hash-bound in its manifest)
VERSION=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["candidate_release"])' "$RELEASE/release_manifest.json")
START=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["start_utc"].replace("T"," ").replace("+00:00"," UTC"))' "$RELEASE/readiness_protocol.json")

remote() { ssh -o BatchMode=yes "$HOST" "$@"; }

case "${1:-}" in
  stage)
    remote 'test -f ~/.config/btc-study/kalshi_readonly_config.json && test -f ~/.config/btc-study/telegram.json' \
      || { echo "Missing ~/.config/btc-study/{kalshi_readonly_config.json,telegram.json} on $HOST (see README)"; exit 1; }
    remote "mkdir -p ~/btc-study/releases/$VERSION ~/btc-study/state ~/.config/systemd/user && chmod 700 ~/btc-study ~/btc-study/state"
    test -z "$(find "$RELEASE" -name __pycache__ -o -type l | head -1)" || { echo "Refusing: caches or symlinks in $RELEASE"; exit 1; }
    EXPECTED=$(python3 -I -c 'import json,sys;print(json.load(open(sys.argv[1]))["files"]["launch.py"])' "$RELEASE/release_manifest.json")
    echo "$EXPECTED  $RELEASE/launch.py" | shasum -a 256 -c --quiet - || { echo "Refusing: launch.py does not match its manifest hash"; exit 1; }
    python3 -I -B "$RELEASE/launch.py" --verify-files || { echo "Refusing: $RELEASE is not a verified sealed release"; exit 1; }
    rsync -a --delete --exclude runtime --exclude activation_approval.json \
      "$RELEASE/" "$HOST:btc-study/releases/$VERSION/"
    # Stop and disable the collector BEFORE switching releases; refuse if that cannot be confirmed.
    # Fail closed: each state must be an explicit safe value, so a failing systemctl (empty output) refuses.
    remote 'systemctl --user disable --now btc-study-collector.timer 2>/dev/null; systemctl --user stop btc-study-collector.service 2>/dev/null;
            e=$(systemctl --user is-enabled btc-study-collector.timer 2>/dev/null); t=$(systemctl --user is-active btc-study-collector.timer 2>/dev/null);
            s=$(systemctl --user is-active btc-study-collector.service 2>/dev/null); echo "collector before staging: timer $e/$t, service $s";
            case "$e" in disabled|not-found) ;; *) exit 1;; esac; test "$t" = inactive && case "$s" in inactive|failed) ;; *) exit 1;; esac' \
      || { echo "Refusing: could not confirm the collector is stopped and disabled before staging"; exit 1; }
    remote "cd ~/btc-study && ln -sfn releases/$VERSION current"
    scp -q "$UNITS"/btc-study-*.service "$UNITS"/btc-study-*.timer "$HOST:.config/systemd/user/"
    remote "python3 -I -B ~/btc-study/current/launch.py --verify-only" || { echo "Refusing: staged copy failed verification"; exit 1; }
    # A newly staged release needs its own approval: the collector stays off (stopped above) until ./install.sh activate.
    remote 'systemctl --user daemon-reload && systemctl --user enable --now btc-study-attest.timer btc-study-watch.timer \
      && systemctl --user start btc-study-attest.service; cd ~/btc-study/current && grep -o "[0-9a-f]\{64\}  acer_ops.py" btc-study-watch.service | sha256sum --check --quiet --strict - && python3 -B acer_ops.py status'
    echo "Staged $VERSION on $HOST. Collector NOT enabled. Start time when activated: $START"
    ;;
  approve)
    # The owner signs interactively on the Acer; the script lives in ~/btc-study/state, outside the release.
    scp -q "$HERE/sign_approval.py" "$HOST:btc-study/state/sign_approval.py"
    ssh -t "$HOST" 'python3 -I -B ~/btc-study/state/sign_approval.py'
    ;;
  activate)
    remote "test ! -e ~/btc-study/current/QA_ONLY && for u in ~/btc-study/current/btc-study-*; do cmp -s \"\$u\" ~/.config/systemd/user/\$(basename \"\$u\") || exit 1; done && python3 -I -B ~/btc-study/current/launch.py --verify-approval" \
      || { echo "Refusing: QA candidate, installed units differ from the sealed release, or the approval is invalid (run launch.py --verify-approval on the Acer for details)"; exit 1; }
    remote 'systemctl --user enable --now btc-study-collector.timer && systemctl --user list-timers "btc-study-*"'
    ;;
  status)
    # Hash-check acer_ops.py (value sealed into the watch unit) before running it, as the units do.
    remote 'cd ~/btc-study/current && grep -o "[0-9a-f]\{64\}  acer_ops.py" btc-study-watch.service | sha256sum --check --quiet --strict - && python3 -B acer_ops.py status'
    ;;
  send-test)
    remote 'cd ~/btc-study/current && grep -o "[0-9a-f]\{64\}  acer_ops.py" btc-study-watch.service | sha256sum --check --quiet --strict - && python3 -B acer_ops.py send-test'
    ;;
  rollback)
    remote 'systemctl --user disable --now btc-study-collector.timer btc-study-attest.timer btc-study-watch.timer 2>/dev/null;
            systemctl --user stop btc-study-collector.service 2>/dev/null; systemctl --user list-units "btc-study-*" --all'
    echo "Stopped. Evidence in ~/btc-study/current/runtime on $HOST is untouched."
    ;;
  *)
    sed -n '2,12p' "$0"; exit 2 ;;
esac
