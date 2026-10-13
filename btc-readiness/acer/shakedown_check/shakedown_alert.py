"""Immediate Telegram when the r2 shakedown service fails (systemd OnFailure= hook).

The twice-daily check can find a dead collector up to 12 h late; this fires within seconds.
Read-only: reads the HALTED marker and the unit's last journal line, never the database.
"""
import json, subprocess
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

RUNTIME = Path.home() / 'btc-study-qa/study005r2_release/candidate/runtime_shakedown'
TELEGRAM = Path.home() / '.config/btc-study/telegram.json'


def main():
    halted = (RUNTIME / 'HALTED').read_text().strip() if (RUNTIME / 'HALTED').exists() else 'none'
    tail = subprocess.run(['journalctl', '--user', '-u', 'btc-r2-shakedown.service', '-n', '1', '-o', 'cat', '--no-pager'],
                          capture_output=True, text=True).stdout.strip()[:300]
    text = ('[R2 SHAKEDOWN] The collector stopped with a failure. HALTED marker: %s. Last log line: %s\n'
            'It does not restart itself; the 72 h run needs review.' % (halted, tail or 'unavailable'))
    cfg = json.loads(TELEGRAM.read_text())
    body = urlencode({'chat_id': cfg['chat_id'], 'text': text}).encode()
    with urlopen(Request('https://api.telegram.org/bot%s/sendMessage' % cfg['bot_token'], data=body, method='POST'), timeout=15):
        pass


if __name__ == '__main__':
    main()
