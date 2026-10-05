#!/usr/bin/env bash
# Off-Acer alarm on the Mac (LaunchAgent, every 5 min while the Mac is awake).
#   ./install_acer_watch.sh           copy acer_watch.py out of ~/Documents and load the agent
#   ./install_acer_watch.sh status    agent state, last run's log lines, alarm state
#   ./install_acer_watch.sh remove    unload the agent and delete its plist (state kept)
# launchd jobs can't read ~/Documents (macOS privacy), so the script runs from
# ~/.local/share/btc-acer-watch. Needs ~/.config/btc-study/telegram.json (0600),
# copied from the Acer; installing refuses without it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL=local.btc-acer-watch
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DEST="$HOME/.local/share/btc-acer-watch"
LOG="$HOME/Library/Logs/btc-acer-watch.log"
PY=/opt/homebrew/bin/python3
TELEGRAM="$HOME/.config/btc-study/telegram.json"

case "${1:-install}" in
  install)
    [ -f "$TELEGRAM" ] || { echo "missing $TELEGRAM (copy it from the Acer, mode 0600)" >&2; exit 1; }
    chmod 600 "$TELEGRAM"
    mkdir -p "$DEST" "$HOME/Library/LaunchAgents" && chmod 700 "$DEST"
    cp "$HERE/../acer_watch.py" "$DEST/"
    sed -e "s#@PY@#$PY#" -e "s#@SCRIPT@#$DEST/acer_watch.py#" -e "s#@LOG@#$LOG#" \
      "$HERE/acer-watch.plist" > "$PLIST"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "installed $LABEL"
    ;;
  status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E 'state|last exit|run interval' || echo "not loaded"
    tail -3 "$LOG" 2>/dev/null || true
    cat "$HOME/.local/state/btc-acer-watch/state.json" 2>/dev/null || echo "no alarm state yet"
    ;;
  remove)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed $LABEL (state and script copy kept)"
    ;;
  *) echo "usage: $0 [install|status|remove]" >&2; exit 2 ;;
esac
