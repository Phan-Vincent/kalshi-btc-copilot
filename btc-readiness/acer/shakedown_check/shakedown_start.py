"""Start the Study 005 r2 QA shakedown, resuming after a reboot. Run by btc-r2-shakedown.service.

Owner decision D2 = b (2026-10-12): a runtime resumes in the same database after a reboot. The
store writes a REBOOT_RESUME event, restarts the continuity-clock baseline, and the slots missed
during the reboot count as missing with the reason "reboot" (2% budget). This script only:

  * notices a reboot (the runtime's stored boot differs from this boot) and sends one Telegram;
  * waits (up to 20 min) for a fee/clock attestation written after this boot, since resuming
    requires one and btc-study-attest.timer runs 2 min after boot;
  * starts the runner, which performs (or refuses) the resume itself.

A runtime from *this* boot that stopped uncleanly is not touched: the runner refuses it, the
service fails, and the twice-daily check alerts for review.
"""
import hashlib, json, os, sqlite3, sys, time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

QA = Path.home() / 'btc-study-qa'
CANDIDATE = QA / 'study005r2_release/candidate'
RUNTIME = CANDIDATE / 'runtime_shakedown'
ATTESTATION = Path.home() / 'btc-study/state/attestation.json'
TELEGRAM = Path.home() / '.config/btc-study/telegram.json'


def log(msg):
    print(msg, flush=True)  # lands in the unit's journal


def current_boot():
    # Same derivation as coherent_runtime.boot_identity(): the runtime stores a SHA-256 of the raw boot id.
    return hashlib.sha256(Path('/proc/sys/kernel/random/boot_id').read_text().strip().encode()).hexdigest()


def boot_time():
    return time.time() - float(Path('/proc/uptime').read_text().split()[0])


def stored_boot_and_last():
    db = RUNTIME / 'evidence.sqlite3'
    if not db.exists() or db.stat().st_size == 0:  # a fresh run, or a database not yet initialized
        return None, None
    try:
        c = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
        try:
            boot = c.execute('SELECT boot FROM runtime_control').fetchone()
            last = c.execute('SELECT MAX(epoch) FROM observations').fetchone()
            return (boot[0] if boot else None), (last[0] if last else None)
        finally:
            c.close()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return None, None
        raise


def telegram(text):
    try:
        cfg = json.loads(TELEGRAM.read_text())
        body = urlencode({'chat_id': cfg['chat_id'], 'text': text}).encode()
        with urlopen(Request('https://api.telegram.org/bot%s/sendMessage' % cfg['bot_token'], data=body, method='POST'), timeout=15):
            pass
    except Exception as e:
        log('telegram failed: %s' % type(e).__name__)


def main():
    boot, last = stored_boot_and_last()
    rebooted = boot is not None and boot != current_boot()
    if rebooted:
        gap = '%d min' % round((time.time() - last) / 60) if last else 'unknown'
        log('Reboot detected (runtime boot %s…, now %s…); resuming in the same database' % (boot[:8], current_boot()[:8]))
        telegram('[R2 SHAKEDOWN] The Acer rebooted. Resuming the same run (gap so far %s; those slots count as '
                 'missing with reason "reboot"). If it cannot resume, the twice-daily check will alert.' % gap)
    deadline = time.time() + 20 * 60
    while not (ATTESTATION.exists() and ATTESTATION.stat().st_mtime >= boot_time()):
        if time.time() > deadline:
            log('No fee attestation written since this boot after 20 min; not starting.')
            sys.exit(1)
        time.sleep(15)
    os.chdir(CANDIDATE)
    os.execv('/usr/bin/python3', ['/usr/bin/python3', '-B', str(CANDIDATE / 'evidence_runner.py'), '--shakedown'])


if __name__ == '__main__':
    main()
