#!/usr/bin/env bash
# Copy the existing READ-ONLY Kalshi key from this Mac to the Acer and write the Acer
# config. Nothing is printed; the key travels over ssh stdin, never on a command line.
set -euo pipefail
HOST=${BTC_STUDY_HOST:-acer}
SRC=${KALSHI_READONLY_CONFIG:-"$HOME/Documents/Codex/2026-09-26/new-chat/outputs/kalshi_readonly_config.json"}
python3 - "$SRC" <<'PY' | ssh -o BatchMode=yes "$HOST" 'umask 077; mkdir -p ~/.config/btc-study && python3 -c "
import json,os,sys
d=json.load(sys.stdin); home=os.path.expanduser(\"~/.config/btc-study\")
key=os.path.join(home,\"kalshi_readonly.pem\")
fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600); os.write(fd,d[\"pem\"].encode()); os.close(fd)
cfg=os.path.join(home,\"kalshi_readonly_config.json\")
fd=os.open(cfg,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600); os.write(fd,json.dumps({\"private_key_path\":key,\"api_key_id\":d[\"id\"]}).encode()); os.close(fd)
print(\"installed read-only key and config on\",os.uname().nodename)"'
import json,os,sys
cfg=json.load(open(sys.argv[1]))
key_id=cfg.get('api_key_id') or os.environ.get(cfg.get('api_key_id_env','KALSHI_READONLY_API_KEY_ID'))
if not key_id: sys.exit('No API key id in config or environment')
json.dump({'pem':open(os.path.expanduser(cfg['private_key_path'])).read(),'id':key_id},sys.stdout)
PY
