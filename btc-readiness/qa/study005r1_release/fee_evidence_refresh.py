"""GET-only refresh of the KXBTC15M fee-precision evidence, through now. Also the pre-advice tripwire.

Reads every KXBTC15M fill and order on the account (GET only, via the production read-only
client) and reconciles each taker order's reported fees against the .0001 and .01 balance-
precision models. Raw account data is written to new owner-only files in btc-readiness/private/;
only aggregate counts go to btc-readiness/fee_evidence_refresh_summary.json.

`summarize()` is pure and offline, so release_tool.py can re-derive a summary from the raw
private files and prove it was not edited. Exit status 1 if the tripwire fails.
"""
import collections, hashlib, importlib.util, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[1]                      # btc-readiness/
PRIVATE = BASE / 'private'
SUMMARY = BASE / 'fee_evidence_refresh_summary.json'
PRIOR_CUTOFF = datetime(2026, 9, 27, tzinfo=timezone.utc)
_spec = importlib.util.spec_from_file_location('_refresh_fee_reconciliation', HERE / 'candidate' / 'fee_reconciliation.py')
_fr = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_fr)


def _when(rows):
    return max(datetime.fromisoformat(r['created_time'].replace('Z', '+00:00')) for r in rows)


def summarize(fills, orders, retrieved_at, series, fee_changes, complete, raw_sha256):
    """Aggregate-only summary. fills: list of fill records; orders: {order_id: order}."""
    btc = [f for f in fills if str(f.get('ticker') or f.get('market_ticker', '')).startswith('KXBTC15M-')]
    uniq = {f.get('fill_id') or f.get('trade_id'): f for f in btc}
    groups = collections.defaultdict(list)
    for f in uniq.values():
        groups[f.get('order_id')].append(f)
    counter = collections.Counter(); by_period = collections.defaultdict(collections.Counter)
    by_day = collections.defaultdict(collections.Counter); latest = {}
    for oid, rows in groups.items():
        r = _fr.reconcile({'order': orders.get(oid), 'fills': rows})
        period = 'before_2026-09-27' if _when(rows) < PRIOR_CUTOFF else 'since_2026-09-27'
        if not r['eligible_for_comparison']:
            key = 'excluded:' + r['exclusion']
        else:
            cmp = r['precision_comparisons']
            m4 = bool(cmp['.0001']['total_matches'] and cmp['.0001']['every_fill_matches'])
            m2 = bool(cmp['.01']['total_matches'] and cmp['.01']['every_fill_matches'])
            key = {(True, False): 'only_0001', (False, True): 'only_01', (True, True): 'both_agree', (False, False): 'neither'}[(m4, m2)]
            if key == 'only_0001':
                latest[period] = max(latest.get(period, _when(rows)), _when(rows))
        counter[key] += 1; by_period[period][key] += 1
        if key in ('only_0001', 'only_01'):
            by_day[_when(rows).strftime('%Y-%m-%d')][key] += 1
    only_01 = counter.get('only_01', 0)
    passed = only_01 == 0 and counter.get('only_0001', 0) > 0 and all(v is True for v in complete.values())
    return {'retrieved_at': retrieved_at, 'window': 'all KXBTC15M history through retrieval',
            'series_fee_type': series.get('fee_type'), 'series_fee_multiplier': str(series.get('fee_multiplier')),
            'fee_changes_for_series': fee_changes.get('series_fee_change_arr') or fee_changes.get('fee_changes'),
            'pagination_complete': complete, 'btc15m_fills': len(uniq), 'btc15m_orders_with_fills': len(groups),
            'orders_matched_to_order_record': sum(1 for oid in groups if oid in orders),
            'comparison_classes': dict(counter), 'by_period': {k: dict(v) for k, v in sorted(by_period.items())},
            'latest_decisive_0001_match_utc': {k: v.isoformat() for k, v in sorted(latest.items())},
            'decisive_by_utc_day': {d: dict(v) for d, v in sorted(by_day.items())},
            'tripwire': {'only_01_matches': only_01, 'passed': passed},
            'raw_sha256': raw_sha256,
            'note': 'Aggregate only. only_0001 = reported fees match the .0001 model exactly and not the .01 model, on an order '
                    'where the two differ; only_01 is the reverse and fails the tripwire. Evidence of this account\'s balance '
                    'precision from its own fee records, not a Kalshi classification.'}


DERIVED = ('btc15m_fills', 'btc15m_orders_with_fills', 'orders_matched_to_order_record', 'comparison_classes',
           'by_period', 'latest_decisive_0001_match_utc', 'decisive_by_utc_day')


def raw_problems(summary, private=PRIVATE):
    """Prove a summary came from the raw private records: hashes match and every count re-derives exactly."""
    names = summary.get('raw_sha256')
    if type(names) is not dict or len(names) != 2:
        return ['summary does not name its two raw files']
    paths = {}
    for name, expected in names.items():
        path = Path(private) / name
        if Path(name).name != name or not path.is_file() or path.is_symlink():
            return ['raw file missing: ' + str(name)]
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return ['raw file hash mismatch: ' + name]
        paths['fills' if name.startswith('fills_') else 'orders' if name.startswith('orders_') else name] = path
    if set(paths) != {'fills', 'orders'}:
        return ['raw files are not one fills and one orders file']
    # retrieved_at is bound to the timestamp in the raw file names written by the same run.
    try:
        stamp = datetime.fromisoformat(summary['retrieved_at']).strftime('%Y%m%dT%H%M%SZ')
    except (KeyError, TypeError, ValueError):
        return ['invalid retrieved_at']
    if {paths['fills'].name, paths['orders'].name} != {'fills_%s.json' % stamp, 'orders_%s.json' % stamp}:
        return ['retrieved_at does not match the raw file timestamps']
    again = summarize(json.loads(paths['fills'].read_text()), json.loads(paths['orders'].read_text()), summary['retrieved_at'],
                      {'fee_type': summary.get('series_fee_type'), 'fee_multiplier': summary.get('series_fee_multiplier')},
                      {'series_fee_change_arr': summary.get('fee_changes_for_series')}, summary.get('pagination_complete', {}), names)
    return ['re-derived ' + key + ' differs from the summary' for key in DERIVED if again[key] != summary.get(key)]


def main():
    sys.path.insert(0, str(Path.home() / 'Documents/Codex/2026-09-26/new-chat/outputs'))
    from kalshi_readonly import KalshiReadOnly, CONFIG  # production GET-only client
    now = datetime.now(timezone.utc); stamp = now.strftime('%Y%m%dT%H%M%SZ')
    c = KalshiReadOnly(CONFIG)
    series = c.get('/series/KXBTC15M')['data'].get('series', {})
    fee_changes = c.get('/series/fee_changes', {'series_ticker': 'KXBTC15M', 'show_historical': 'true'})['data']
    fills, orders, complete = [], {}, {}
    for path in ('/portfolio/fills', '/historical/fills'):
        try:
            res = c.pages(path, {'limit': 200, 'max_ts': int(now.timestamp())}, max_pages=50)
        except Exception as e:
            complete[path] = 'error: ' + type(e).__name__ + ' ' + str(e)[:80]; continue
        complete[path] = res['complete']
        fills += [f for p in res['pages'] for f in p['data'].get('fills', [])]
    for path in ('/portfolio/orders', '/historical/orders'):
        try:
            res = c.pages(path, {'limit': 200, 'max_ts': int(now.timestamp())}, max_pages=50)
        except Exception as e:
            complete[path] = 'error: ' + type(e).__name__ + ' ' + str(e)[:80]; continue
        complete[path] = res['complete']
        for p in res['pages']:
            for o in p['data'].get('orders', []):
                orders.setdefault(o.get('order_id'), o)
    hashes = {}
    for name, obj in (('fills_%s.json' % stamp, fills), ('orders_%s.json' % stamp, orders)):
        fd = os.open(PRIVATE / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as fh:
            json.dump(obj, fh, default=str)
        hashes[name] = hashlib.sha256((PRIVATE / name).read_bytes()).hexdigest()
    # Summarize from the files as written, exactly as a later re-derivation will read them.
    summary = summarize(json.loads((PRIVATE / ('fills_%s.json' % stamp)).read_text()),
                        json.loads((PRIVATE / ('orders_%s.json' % stamp)).read_text()),
                        now.isoformat(), series, fee_changes, complete, hashes)
    SUMMARY.write_text(json.dumps(summary, indent=2, default=str) + '\n')
    print(json.dumps(summary, indent=2, default=str))
    found = _fr.fee_evidence_problems(summary) + raw_problems(summary)
    if found:
        print('TRIPWIRE FAILED: ' + '; '.join(found), file=sys.stderr)
    return 1 if found else 0


def verify(path):
    """Check a summary against its raw records (use before passing a post-study summary to the report)."""
    summary = json.loads(Path(path).read_text())
    found = _fr.fee_evidence_problems(summary) + raw_problems(summary)
    print('VERIFIED' if not found else 'NOT VERIFIED: ' + '; '.join(found))
    return 1 if found else 0


if __name__ == '__main__':
    raise SystemExit(verify(sys.argv[2]) if sys.argv[1:2] == ['--verify'] and len(sys.argv) == 3 else main())
