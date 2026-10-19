"""Twice-daily health check of the Study 005 r2 QA shakedown on the Acer.

Read-only: opens the shakedown database in SQLite read-only mode and never touches the
collector. Sends a Telegram message ([R2 SHAKEDOWN]) only when something drifts toward a
seal-gate failure, plus one message when 72 h of collection is reached.

    python3 shakedown_check.py          # check, alert only on drift
    python3 shakedown_check.py --print  # print the full report, never send

Thresholds mirror release_tool's seal gate (>= 95% enrolled checkpoints, <= 1% poll failures
per hour, every miss explained, storage projection x2 under the main-database cap), with an
early warning when the 95th-percentile snapshot skew passes 4 s (the veto budget is 5 s).
"""
import json, math, sqlite3, subprocess, sys, time, zlib
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

RELEASE = Path.home() / 'btc-study-qa/study005r2_release/candidate'
DB = RELEASE / 'runtime_shakedown/evidence.sqlite3'
LIMITS = RELEASE / 'operating_limits.json'
TELEGRAM = Path.home() / '.config/btc-study/telegram.json'
STATE = Path.home() / 'btc-study-qa/shakedown_check_state.json'
UNIT = 'btc-r2-shakedown.service'
LOOKBACK = 12 * 3600
BAND = (300, 330)  # checkpoint band: seconds after a slot opens
MIN_COVERAGE, MAX_FAIL_RATE, SKEW_WARN, TARGET_HOURS = 0.95, 0.01, 4.0, 72


SKEW_SAMPLE, BATCH = 240, 20


def connect():
    # Autocommit reads with a short busy wait. Every query below is fetched in full before any processing,
    # so no shared lock outlives a single small statement. (On 2026-10-13 this check iterated a live
    # 12-hour payload scan, held the lock for seconds, and the collector halted on "database is locked".)
    return sqlite3.connect('file:%s?mode=ro' % DB, uri=True, timeout=1, isolation_level=None)


def payloads(c, ids):
    """Decoded snapshots for observation ids, fetched in small batches with a pause between them."""
    out = []
    for i in range(0, len(ids), BATCH):
        part = ids[i:i + BATCH]
        rows = c.execute('SELECT payload FROM observations WHERE id IN (%s)' % ','.join('?' * len(part)), part).fetchall()
        out += [json.loads(zlib.decompress(b))['snapshot'] for (b,) in rows]
        time.sleep(0.05)
    return out


def report(now):
    c = connect()
    try:
        start = c.execute("SELECT MIN(epoch) FROM runtime_events WHERE kind='COLLECTOR_START'").fetchone()[0]
        if start is None:
            return {'problems': ['No COLLECTOR_START event: the shakedown has not started']}
        since = max(start, now - LOOKBACK)
        events = c.execute('SELECT epoch,kind,detail FROM runtime_events WHERE epoch>=?', (since,)).fetchall()
        obs = [e for (e,) in c.execute('SELECT epoch FROM observations WHERE epoch>=?', (since,)).fetchall()]
        enrolled = {round(o) for (o,) in c.execute('SELECT opened FROM markets WHERE checkpoint IS NOT NULL AND opened>=?', (since - 900,)).fetchall()}
        recent = [i for (i,) in c.execute('SELECT id FROM observations WHERE epoch>=? ORDER BY id DESC LIMIT ?', (since, SKEW_SAMPLE)).fetchall()]
        skews = [s['retrieval_health']['snapshot_skew_seconds'] for s in payloads(c, recent)]
        # Slots whose whole checkpoint band lies inside the window and has already passed.
        first = math.ceil((since - BAND[0]) / 900) * 900
        slots = [s for s in range(first, int(now), 900) if s + BAND[0] >= since and s + BAND[1] <= now]
        missed = [s for s in slots if s not in enrolled]
        # Reasons the collector itself logged per slot, fetched once in full before the loop below.
        logged = {}
        for opened, reason in c.execute('SELECT opened,reason FROM slot_events WHERE opened>=?', (since - 900,)).fetchall():
            logged.setdefault(round(opened), []).append(reason)
        miss_reasons = Counter()
        for s in missed[-12:]:  # the most recent misses are enough to name the cause
            near = [d for e, k, d in events if k in ('COLLECTION_FAILED','EXCHANGE_CLOSED') and s + BAND[0] - 15 <= e <= s + BAND[1]]
            ids = [i for (i,) in c.execute('SELECT id FROM observations WHERE epoch BETWEEN ? AND ?', (s + BAND[0], s + BAND[1])).fetchall()]
            vetoes = [v for snap in payloads(c, ids) for v in snap.get('operational_vetoes', [])]
            explained = [r for r in logged.get(s, []) if r]
            # A reboot that resumed after this band opened explains it; the release tool applies the same test.
            for e, k, d in events:
                if k != 'REBOOT_RESUME':
                    continue
                try:
                    previous = json.loads(d)['last_observation_epoch']
                    if type(previous) in (int, float) and math.isfinite(previous) and previous < e and s + BAND[0] < e and s + BAND[1] > previous:
                        explained.append('reboot')
                except (TypeError, ValueError, KeyError):
                    pass
            stops = [(e, d) for e, k, d in events if k == 'COLLECTOR_STOP' and e <= s + BAND[1]]
            if stops:
                stopped, detail = stops[-1]
                # Cleared only by a restart that had the collector up for the whole band (release tool rule).
                if not any(e > stopped and e <= s + BAND[0] and k == 'COLLECTOR_START' for e, k, _ in events):
                    explained.append(detail or 'Collector stopped')
            miss_reasons.update(set(near + vetoes + explained) or {'NO OBSERVATION OR REASON IN THE BAND'})
        # Poll failure rate per full clock hour.
        hours = []
        for h in range(math.ceil(since / 3600) * 3600, int(now) - 3599, 3600):
            fail = sum(1 for e, k, _ in events if k == 'COLLECTION_FAILED' and h <= e < h + 3600)
            ok = sum(1 for e in obs if h <= e < h + 3600)
            hours.append((h, fail, ok + fail))
        bad_hours = [(time.strftime('%m-%d %H:00', time.gmtime(h)), f, n) for h, f, n in hours if n and f / n > MAX_FAIL_RATE]
        fail_reasons = Counter((d or 'NO DETAIL')[:90] for e, k, d in events if k == 'COLLECTION_FAILED')
        unexplained = sum(1 for e, k, d in events if k == 'COLLECTION_FAILED' and not d)
        cutoffs = sum(1 for e, k, _ in events if k == 'FINAL_CUTOFF')
    finally:
        c.close()
    skews.sort()
    p95 = skews[int(0.95 * (len(skews) - 1))] if skews else None
    quota = json.loads(LIMITS.read_text())['quota_bytes']
    cap = (quota - 1024 ** 2) // 3
    hours_run = (now - start) / 3600
    grown = DB.stat().st_size + sum(p.stat().st_size for p in DB.parent.glob('evidence.sqlite3-*'))
    projected = grown / max(hours_run, 1e-9) * 24 * 72 * 2
    active = subprocess.run(['systemctl', '--user', 'is-active', UNIT], capture_output=True, text=True).stdout.strip()
    problems = []
    if active != 'active':
        problems.append('Shakedown service is %s (it does not restart itself)' % (active or 'unknown'))
    if slots and len(missed) / len(slots) > 1 - MIN_COVERAGE:
        problems.append('Checkpoint coverage %d/%d (%.1f%%) is below 95%%' % (len(slots) - len(missed), len(slots), 100 * (1 - len(missed) / len(slots))))
    if bad_hours:
        problems.append('Poll failures above 1%% in %d hour(s): %s' % (len(bad_hours), ', '.join('%s %d/%d' % x for x in bad_hours[:4])))
    if unexplained:
        problems.append('%d collection failure(s) with no logged reason' % unexplained)
    if p95 is not None and p95 > SKEW_WARN:
        problems.append('Snapshot skew p95 %.2f s is above 4 s (veto at 5 s; max %.2f s)' % (p95, skews[-1]))
    if projected > cap:
        problems.append('Storage projection %.1f GB (x2, 72 d) exceeds the %.1f GB main-database cap' % (projected / 1e9, cap / 1e9))
    return {'problems': problems, 'hours_run': round(hours_run, 1), 'window_hours': round((now - since) / 3600, 1),
            'service': active, 'slots': len(slots), 'enrolled': len(slots) - len(missed),
            'missed_reasons': miss_reasons.most_common(4), 'failures': sum(f for _, f, _ in hours),
            'polls': sum(n for _, _, n in hours), 'failure_reasons': fail_reasons.most_common(4),
            'final_cutoff_events': cutoffs, 'skew_p95': p95, 'skew_max': skews[-1] if skews else None,
            'db_bytes': grown, 'storage_projection_x2_bytes': int(projected), 'main_db_cap_bytes': cap}


def telegram(text):
    cfg = json.loads(TELEGRAM.read_text())
    body = urlencode({'chat_id': cfg['chat_id'], 'text': text[:3500]}).encode()
    with urlopen(Request('https://api.telegram.org/bot%s/sendMessage' % cfg['bot_token'], data=body, method='POST'), timeout=15) as r:
        return r.status == 200


def main():
    now = time.time()
    try:
        r = report(now)
    except Exception as e:  # the check itself failing is worth a message
        r = {'problems': ['Check could not read the shakedown: %s: %s' % (type(e).__name__, str(e)[:150])]}
    if '--print' in sys.argv:
        print(json.dumps(r, indent=1, default=str))
        return
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    lines = []
    if r['problems']:
        lines += ['[R2 SHAKEDOWN] Drift found:'] + ['- ' + p for p in r['problems']]
        if r.get('missed_reasons'):
            lines.append('Missed-slot reasons: ' + '; '.join('%s (%d)' % x for x in r['missed_reasons']))
        if r.get('failure_reasons'):
            lines.append('Failure reasons: ' + '; '.join('%s (%d)' % x for x in r['failure_reasons']))
    if r.get('hours_run', 0) >= TARGET_HOURS and not state.get('target_reached_sent') and r.get('service') == 'active' and not r['problems']:
        lines.append('[R2 SHAKEDOWN] %d h of collection reached (%s/%s checkpoints in the last %s h). '
                     'Ready for release_tool.py shakedown-summary.' % (TARGET_HOURS, r.get('enrolled'), r.get('slots'), r.get('window_hours')))
        state['target_reached_sent'] = True
    if lines:
        telegram('\n'.join(lines))
    state['last_run'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))
    state['last_problems'] = r['problems']
    STATE.write_text(json.dumps(state))


if __name__ == '__main__':
    main()
