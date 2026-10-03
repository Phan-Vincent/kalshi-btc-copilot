import json, itertools, collections, importlib.util
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime, timezone
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('fr', HERE / 'qa/readiness_release/candidate/fee_reconciliation.py')
fr = importlib.util.module_from_spec(spec); spec.loader.exec_module(fr)
fills = json.loads((HERE/'private/fills.json').read_text()); orders = json.loads((HERE/'private/orders.json').read_text())
CUT = datetime(2026,9,27,tzinfo=timezone.utc)
fills = {f.get('fill_id') or f.get('trade_id'): f for f in fills if str(f.get('ticker') or f.get('market_ticker','')).startswith('KXBTC15M-') and datetime.fromisoformat(f['created_time'].replace('Z','+00:00')) < CUT}
g = collections.defaultdict(list)
for f in fills.values(): g[f['order_id']].append(f)
def seq(rows):
    out=[]
    for r in rows:
        side=r.get('outcome_side') or r.get('side'); p=D(r['yes_price_dollars']) if side=='yes' else D(r['no_price_dollars'])
        out.append((p, D(r['count_fp']), r['action']))
    return out
neither=[]; multi=collections.Counter(); multi_rebate=collections.Counter()
for oid, rows in g.items():
    res = fr.reconcile({'order': orders.get(oid), 'fills': rows})
    if res.get('eligible_for_comparison') and res['comparison_class']=='neither_matches':
        s=seq(rows); rep=sum(D(r['fee_cost']) for r in rows)
        neither.append({'action': s[0][2], 'qty_fractional': bool(s[0][1]%1), 'price_subcent': bool(s[0][0]%D('.01')),
            'reported_minus_0001': str(rep-D(fr.fee_sequence(s,'.0001')['fee'])), 'reported_minus_01': str(rep-D(fr.fee_sequence(s,'.01')['fee'])),
            'reported_minus_tradefee_only': str(rep-sum(D(x['trade']) for x in fr.fee_sequence(s,'.0001')['fills'])),
            'order_status': orders[oid].get('status'), 'is_taker': [r.get('is_taker') for r in rows]})
    if res.get('exclusion')=='fill_order_ambiguous' and all(r.get('is_taker') is True for r in rows):
        rep=sum(D(r['fee_cost']) for r in rows); s=seq(rows)
        perms = list(itertools.permutations(s)) if len(s)<=6 else [tuple(s), tuple(reversed(s))]
        m4 = any(D(fr.fee_sequence(list(p),'.0001')['fee'])==rep for p in perms)
        m2 = any(D(fr.fee_sequence(list(p),'.01')['fee'])==rep for p in perms)
        differ = any(D(fr.fee_sequence(list(p),'.0001')['fee'])!=D(fr.fee_sequence(list(p),'.01')['fee']) for p in perms)
        multi['fills=%d match.0001=%s match.01=%s models_differ=%s' % (min(len(s),7), m4, m2, differ)] += 1
        # does per-fill reported fee ever go below the trade fee (i.e. a rebate visible on a fill)?
        tf=[D(x['trade']) for x in fr.fee_sequence(s,'.0001')['fills']]
        multi_rebate['some_fill_fee_below_trade_fee=%s' % any(D(r['fee_cost'])<t for r,t in zip(rows,tf))] += 1
print(json.dumps({'neither_detail': neither, 'multi_fill_same_timestamp': dict(multi), 'multi_fill_rebate_visibility': dict(multi_rebate)}, indent=1))
