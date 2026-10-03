"""GET-only fee evidence collection for KXBTC15M. Raw account data stays in private/ (0600).
Only aggregate comparison counts are written to fee_evidence_summary.json."""
import json, os, sys, hashlib, collections
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal as D
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path.home() / 'Documents/Codex/2026-09-26/new-chat/outputs'))
from kalshi_readonly import KalshiReadOnly, CONFIG  # production GET-only client
import importlib.util
_spec = importlib.util.spec_from_file_location('fee_reconciliation', HERE / 'qa/readiness_release/candidate/fee_reconciliation.py')
_mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_mod); reconcile = _mod.reconcile

CUTOFF = datetime(2026, 9, 27, tzinfo=timezone.utc)  # pre-study records only, as in the prior review
c = KalshiReadOnly(CONFIG)
raw = {}
def save(name, obj):
    path = HERE / 'private' / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump(obj, f, default=str)
series = c.get('/series/KXBTC15M')['data'].get('series', {})
fee_changes = c.get('/series/fee_changes', {'series_ticker': 'KXBTC15M', 'show_historical': 'true'})['data']
fills, orders, complete = [], {}, {}
for path in ('/portfolio/fills', '/historical/fills'):
    try:
        res = c.pages(path, {'limit': 200, 'max_ts': int(CUTOFF.timestamp())}, max_pages=25)
    except Exception as e:
        complete[path] = 'error: ' + type(e).__name__ + ' ' + str(e)[:80]; continue
    complete[path] = res['complete']
    fills += [f for p in res['pages'] for f in p['data'].get('fills', [])]
for path in ('/portfolio/orders', '/historical/orders'):
    try:
        res = c.pages(path, {'limit': 200, 'max_ts': int(CUTOFF.timestamp())}, max_pages=25)
    except Exception as e:
        complete[path] = 'error: ' + type(e).__name__ + ' ' + str(e)[:80]; continue
    complete[path] = res['complete']
    for p in res['pages']:
        for o in p['data'].get('orders', []): orders.setdefault(o.get('order_id'), o)
save('fills.json', fills); save('orders.json', orders)
fills = [f for f in fills if str(f.get('ticker') or f.get('market_ticker', '')).startswith('KXBTC15M-')
         and datetime.fromisoformat(f['created_time'].replace('Z', '+00:00')) < CUTOFF]
uniq = {f.get('fill_id') or f.get('trade_id'): f for f in fills}
groups = collections.defaultdict(list)
for f in uniq.values(): groups[f.get('order_id')].append(f)
results = [reconcile({'order': orders.get(oid), 'fills': rows}) for oid, rows in groups.items()]
counter = collections.Counter()
for r in results:
    if not r['eligible_for_comparison']: counter['excluded:' + r['exclusion']] += 1; continue
    cmp = r['precision_comparisons']
    key = ('fills=%s' % ('1' if r['sample_fills'] == 1 else '2+'),
           '.0001' if cmp['.0001']['total_matches'] and cmp['.0001']['every_fill_matches'] else '-',
           '.01' if cmp['.01']['total_matches'] and cmp['.01']['every_fill_matches'] else '-',
           'rounding_present' if cmp['.0001']['modeled_rounding_present'] or cmp['.01']['modeled_rounding_present'] else 'no_rounding')
    counter[' '.join(key)] += 1
qty = collections.Counter()
for f in uniq.values():
    q = D(str(f.get('count_fp') or f.get('count')))
    qty['fractional' if q % 1 else 'whole'] += 1
    if q < 1: qty['below_one_contract'] += 1
summary = {'retrieved_at': datetime.now(timezone.utc).isoformat(), 'cutoff_utc': CUTOFF.isoformat(),
           'series_fee_type': series.get('fee_type'), 'series_fee_multiplier': str(series.get('fee_multiplier')),
           'series_last_updated_ts': series.get('last_updated_ts'),
           'fee_changes_response_keys': sorted(fee_changes.keys()),
           'fee_changes_for_series': fee_changes.get('series_fee_change_arr') or fee_changes.get('fee_changes'),
           'pagination_complete': complete, 'btc15m_fills': len(uniq), 'btc15m_orders_with_fills': len(groups),
           'orders_matched_to_order_record': sum(1 for oid in groups if oid in orders),
           'comparison_classes': dict(counter), 'fill_quantity_profile': dict(qty),
           'raw_sha256': {n: hashlib.sha256((HERE / 'private' / n).read_bytes()).hexdigest() for n in ('fills.json', 'orders.json')},
           'note': 'Aggregate only. A class like "fills=1 .0001 - rounding_present" means reported fees match the .0001 model exactly and not the .01 model, on an order where the two models differ.'}
(HERE / 'fee_evidence_summary.json').write_text(json.dumps(summary, indent=2, default=str) + '\n')
print(json.dumps(summary, indent=2, default=str))
