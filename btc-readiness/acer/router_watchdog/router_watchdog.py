"""Reboot the Netgear router when the internet has been down for a while.

Runs as a user service on the Acer. Every CHECK_SECONDS it pings public resolvers
(WAN) and the router itself (LAN). If the WAN has been down for DOWN_MINUTES while
the router still answers on the LAN, it reboots the router through its own admin
API (pynetgear), within hard limits so it can never reboot-loop:

  * at least MIN_GAP_MINUTES between reboots;
  * at most MAX_PER_DAY reboots in any 24 h.

After a reboot it waits GRACE_MINUTES before judging again. When the WAN comes back
it sends one Telegram message ([ROUTER]) with the outage length and what it tried;
if reboots didn't help, the message says the modem probably needs a power cycle.

    python3 router_watchdog.py           # the service loop
    python3 router_watchdog.py --check   # log in and print router info; never reboots
    python3 router_watchdog.py --reboot-now   # one manual reboot (asks to confirm)

Router credentials live only in ~/.config/router-watchdog/netgear.json (0600),
written by setup.sh. Nothing here talks to Kalshi or the study.
"""
import json, os, stat, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CONFIG = Path.home() / '.config/router-watchdog/netgear.json'
TELEGRAM = Path.home() / '.config/btc-study/telegram.json'
STATE = Path.home() / 'router-watchdog/state'
EVENTS = STATE / 'events.jsonl'
ROUTER = '192.168.1.1'
WAN_TARGETS = ('1.1.1.1', '9.9.9.9', '8.8.8.8')
CHECK_SECONDS = 30
DOWN_MINUTES = 10
GRACE_MINUTES = 10
MIN_GAP_MINUTES = 45
MAX_PER_DAY = 3


def now():
    return time.time()


def stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def log(kind, **fields):
    STATE.mkdir(parents=True, exist_ok=True)
    row = {'at': stamp(now()), 'event': kind, **fields}
    with EVENTS.open('a') as f:
        f.write(json.dumps(row) + '\n')
    print(json.dumps(row), flush=True)  # also lands in the journal


def ping(host):
    return subprocess.run(['ping', '-n', '-q', '-c1', '-W2', host],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def wan_up():
    return any(ping(t) for t in WAN_TARGETS)


def load_config():
    if not CONFIG.exists():
        raise SystemExit('No router credentials: run setup.sh first')
    mode = stat.S_IMODE(CONFIG.stat().st_mode)
    if mode & 0o077:
        raise SystemExit('%s must be private (chmod 600)' % CONFIG)
    cfg = json.loads(CONFIG.read_text())
    return cfg['host'], cfg['user'], cfg['password']


def router():
    from pynetgear import Netgear
    host, user, password = load_config()
    return Netgear(password=password, host=host, user=user)


def reboot_router():
    r = router()
    if not r.login_try_port():
        raise RuntimeError('router login failed')
    if not r.reboot():
        raise RuntimeError('router refused the reboot request')


def telegram(text):
    try:
        cfg = json.loads(TELEGRAM.read_text())
        body = urlencode({'chat_id': cfg['chat_id'], 'text': text[:3500]}).encode()
        req = Request('https://api.telegram.org/bot%s/sendMessage' % cfg['bot_token'], data=body, method='POST')
        with urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:  # alerts are best effort; never crash the loop
        log('telegram_failed', error=type(e).__name__)
        return False


def minutes(seconds):
    return '%d min' % round(seconds / 60) if seconds < 5400 else '%.1f h' % (seconds / 3600)


def loop():
    log('started', down_minutes=DOWN_MINUTES, min_gap_minutes=MIN_GAP_MINUTES, max_per_day=MAX_PER_DAY)
    down_since = None
    reboots = []          # epochs of reboots, all time (pruned to 24 h when counting)
    outage_reboots = 0    # reboots during the current outage
    grace_until = 0.0
    while True:
        t = now()
        if wan_up():
            if down_since is not None:
                length = t - down_since
                log('wan_recovered', outage_seconds=round(length), reboots=outage_reboots)
                if outage_reboots == 0:
                    msg = '[ROUTER] Internet was down %s (since %s); recovered on its own.' % (minutes(length), stamp(down_since))
                else:
                    msg = ('[ROUTER] Internet was down %s (since %s). The watchdog rebooted the Netgear %d time(s); '
                           'it recovered %s after the last reboot.' % (minutes(length), stamp(down_since), outage_reboots,
                                                                       minutes(t - reboots[-1])))
                    if t - reboots[-1] > (GRACE_MINUTES + 5) * 60:
                        msg += (' Recovery came well after the reboot, so the Netgear probably wasn\'t the cause; '
                                'if this repeats, power-cycle the modem.')
                if length >= 120:  # skip blips
                    telegram(msg)
                down_since = None
                outage_reboots = 0
        else:
            if down_since is None:
                down_since = t
                log('wan_down')
            lan = ping(ROUTER)
            recent = [r for r in reboots if t - r < 86400]
            due = (t - down_since >= DOWN_MINUTES * 60 and t >= grace_until and lan
                   and (not reboots or t - reboots[-1] >= MIN_GAP_MINUTES * 60) and len(recent) < MAX_PER_DAY)
            if due:
                # Every attempt counts toward the limits: a router often drops the connection
                # mid-reply because it is already rebooting, so "failed" may mean "worked".
                reboots.append(t)
                outage_reboots += 1
                grace_until = t + GRACE_MINUTES * 60
                try:
                    reboot_router()
                    log('router_rebooted', outage_seconds=round(t - down_since), reboot_number=outage_reboots,
                        reboots_last_24h=len(recent) + 1)
                except Exception as e:
                    log('reboot_attempt_unconfirmed', reboot_number=outage_reboots,
                        error='%s: %s' % (type(e).__name__, str(e)[:150]))
            elif not lan and int(t - down_since) % 600 < CHECK_SECONDS:
                log('router_unreachable_on_lan')  # power cut or the router itself is dead: nothing to do
        time.sleep(CHECK_SECONDS)


def check():
    r = router()
    if not r.login_try_port():
        raise SystemExit('Login FAILED: check the admin password in setup.sh')
    info = r.get_info() or {}
    keep = ('ModelName', 'Firmwareversion', 'Description', 'DeviceMode')
    print(json.dumps({k: info.get(k) for k in keep if k in info} or {'logged_in': True}, indent=1))
    print('Login OK. reboot() available:', hasattr(r, 'reboot'))


if __name__ == '__main__':
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    if arg == '--check':
        check()
    elif arg == '--reboot-now':
        if input('Reboot the Netgear now? Home internet drops for ~2-3 min. Type yes: ').strip() == 'yes':
            reboot_router()
            log('router_rebooted', manual=True)
            print('Reboot requested.')
    elif arg == '':
        loop()
    else:
        raise SystemExit(__doc__)
