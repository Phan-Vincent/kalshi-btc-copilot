"""Candidate-only synthetic regressions. No network or production state."""
import sys
sys.argv.insert(1,'candidate')
import remediation_audit as a
import copy,json,os,sqlite3,tempfile,unittest,zlib,socket,subprocess
from pathlib import Path
from unittest.mock import patch
from decimal import Decimal as D
b,r=a.b,a.r
import btc_copilot_evidence as e
import study_policy as p

class Regression(unittest.TestCase):
    def test_honest_replay_and_every_compared_field_tamper(self):
        s=a.collect(copy.deepcopy(a.RAW))
        with tempfile.TemporaryDirectory() as td:
            audit=r.Audit(Path(td)/'a.sqlite3');audit.record(s,a.RAW,None,a.SETTINGS,Path(td)/'absent')
            honest=r.replay(audit.path);self.assertTrue(honest['matches'],honest)
            with audit.connect() as c:original=c.execute('SELECT payload FROM observations').fetchone()[0]
            for field in honest['compared_fields']:
                with self.subTest(field=field):
                    payload=json.loads(zlib.decompress(original));payload['snapshot'][field]='FORGED'
                    with audit.connect() as c:c.execute('UPDATE observations SET payload=?',(zlib.compress(json.dumps(payload).encode()),))
                    if field in {'model_version','collection_epoch','analysis_epoch','epoch'}:
                        with self.assertRaises((TypeError,ValueError,KeyError)):r.replay(audit.path)
                    else:
                        result=r.replay(audit.path)
                        self.assertFalse(result['matches']);self.assertIn(field,result['mismatched_fields'])
    def test_replay_delayed_completion_clocks(self):
        c=a.make(copy.deepcopy(a.RAW))
        health={'collection_epoch':a.NOW-.8,'analysis_epoch':a.NOW,'available_epoch':a.NOW+.4,'requests':{k:{'started_at_epoch':a.NOW-.5,'finished_at_epoch':a.NOW-.1,'latency_seconds':.4} for k in ('market','event','series','book','benchmark','trades','candles','spot','exchange')}}
        with patch.object(b.time,'time',return_value=a.NOW),patch.object(b,'spot_get',return_value=a.RAW['spot']),patch.object(b,'load_settings',return_value=a.SETTINGS):s=c.collect(replay_health=health)
        with tempfile.TemporaryDirectory() as td:
            audit=r.Audit(Path(td)/'a');audit.record(s,c.raw,None,a.SETTINGS,Path(td)/'absent')
            self.assertTrue(r.replay(audit.path)['matches'])
    def test_unknown_prior_health_and_same_source_never_confirm(self):
        raw,st,model,prev=a.eligible()
        for update in ({'confirmation_valid':None},{'source_epoch':a.NOW-.5},{'epoch':a.NOW+1},{'operational_vetoes':['outage']},{'data_quality_faults':['stale']}):
            prior=dict(prev,**update);s=a.collect(raw,prior,st,model)
            self.assertEqual(s['shadow_decision'],'NO TRADE');self.assertFalse(s['shadow_strategies']['current_full']['eligible'])
    def test_record_preserves_health_and_provenance(self):
        raw=copy.deepcopy(a.RAW);raw['trades']['data']={'trades':[]}
        s=a.collect(raw);c=a.make(raw)
        with tempfile.TemporaryDirectory() as td,patch.object(b,'WORK',Path(td)),patch.object(b.time,'time',return_value=a.NOW):
            c.state_path=Path(td)/'state.json';b.Copilot.record(c,s)
            self.assertFalse(json.loads(c.state_path.read_text())['previous']['confirmation_valid'])
            b.Copilot.commit_observation(c,s)
            prior=json.loads(c.state_path.read_text())['previous']
            self.assertTrue(prior['confirmation_valid']);self.assertEqual(prior['source_epoch'],b.dt(s['btc_observed_at']['utc']).timestamp())
            s['operational_vetoes']=['outage'];s['epoch']+=15;b.Copilot.record(c,s)
            with patch.object(b.time,'time',return_value=a.NOW+15):b.Copilot.commit_observation(c,s)
            self.assertFalse(json.loads(c.state_path.read_text())['previous']['confirmation_valid'])
    def candle(self,end):
        return {'end_period_ts':end,'volume_fp':'1','open_interest_fp':'2',**{f:dict(zip(('open_dollars','high_dollars','low_dollars','close_dollars'),('.4','.6','.3','.5'))) for f in ('price','yes_bid','yes_ask')}}
    def test_forming_aux_candle_omitted(self):
        end=int(a.NOW)//60*60;closed=self.candle(end);forming=self.candle(end+60)
        self.assertEqual(b.validated_candles([closed,forming],a.NOW),[closed])
    def test_aux_candle_ordering_duplicates_and_ohlc(self):
        end=int(a.NOW)//60*60;row=self.candle(end)
        for rows in ([row,row],[self.candle(end+60),row],[self.candle(end+120)], [dict(row,end_period_ts=end+1)]):
            with self.assertRaises(a.ReadError):b.validated_candles(rows,a.NOW)
        for value in ('NaN','-1','1.1',None):
            bad=copy.deepcopy(row);bad['price']['high_dollars']=value
            with self.assertRaises((a.ReadError,ArithmeticError)):b.validated_candles([bad],a.NOW)
    def test_empty_no_trade_candle_is_not_fabricated(self):
        row=self.candle(int(a.NOW)//60*60)
        row['price']={k:None for k in row['price']}
        self.assertEqual(b.validated_candles([row],a.NOW)[0]['price'],row['price'])
    def test_trade_full_schema_boundaries(self):
        row={'trade_id':'T','ticker':'M','created_time':b.stamp(a.NOW-1)['utc'],'count_fp':'1','taker_outcome_side':'yes','yes_price_dollars':'.4','no_price_dollars':'.6'}
        self.assertEqual(len(b.validated_trades([row],'M',a.NOW)),1)
        for key,value in [('ticker','OTHER'),('count_fp','NaN'),('count_fp','0'),('yes_price_dollars','1.1'),('no_price_dollars','.4'),('created_time',b.stamp(a.NOW+1)['utc']),('created_time',b.stamp(a.NOW-181)['utc']),('trade_id',None)]:
            with self.subTest(key=key,value=value),self.assertRaises((a.ReadError,ValueError,ArithmeticError)):
                b.validated_trades([dict(row,**{key:value})],'M',a.NOW)
    def test_partial_trade_window_every_arm_abstains(self):
        raw,st,model,prev=a.eligible();raw['trades']['data']['cursor']='UNREAD'
        s=a.collect(raw,prev,st,model)
        self.assertTrue(s['data_quality_faults']);self.assertEqual(s['shadow_decision'],'NO TRADE')
        self.assertTrue(all(not signal['eligible'] for signal in s['shadow_strategies'].values()))
    def test_missing_trade_and_candle_keys_rejected(self):
        for source,key in (('trades','trades'),('candles','candlesticks')):
            raw=copy.deepcopy(a.RAW);del raw[source]['data'][key]
            with self.assertRaises((KeyError,a.ReadError)):a.collect(raw)
    def test_each_required_upstream_error_rejected(self):
        for key in ('market','event','series','book','benchmark','trades','candles','spot','exchange'):
            raw=copy.deepcopy(a.RAW);raw[key]['data']['error']='upstream unavailable'
            with self.subTest(source=key),self.assertRaises(a.ReadError):a.collect(raw)
    def test_timezone_naive_rejected(self):
        with self.assertRaises(a.ReadError):b.dt('2026-09-27T12:00:00')
    def test_grid_finite_capacity_and_bounds(self):
        market=copy.deepcopy(a.RAW['market']['data']['market']);self.assertTrue(b.grid_prices(market))
        for start,end,step in [('NaN','1','.01'),('0','Infinity','.01'),('0','1','0'),('-.1','1','.01'),('0','1','.000001'),('.9','.1','.01')]:
            bad=dict(market,price_ranges=[{'start':start,'end':end,'step':step}])
            with self.assertRaises((a.ReadError,ValueError,ArithmeticError)):b.grid_prices(bad)
    def test_walk_and_execution_duplicate_depth_rejected(self):
        rows=[['.5','.6'],['.5','.6']]
        with self.assertRaises(ValueError):r.walk(rows,1)
        with self.assertRaises(ValueError):p.execution_cost(rows,{'slippage_reserve_cents':'.5'})
    def test_atomic_destination_links_and_old_temp_sentinel(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);dest=root/'state.json';sentinel=root/'sentinel';sentinel.write_text('KEEP');(root/'state.json.tmp').symlink_to(sentinel)
            b.atomic(dest,{'safe':True});self.assertEqual(sentinel.read_text(),'KEEP');self.assertEqual(dest.stat().st_mode&0o777,0o600)
            dest.unlink();dest.symlink_to(sentinel)
            with self.assertRaises(a.ReadError):b.atomic(dest,'BAD')
            dest.unlink();os.link(sentinel,dest)
            with self.assertRaises(a.ReadError):b.atomic(dest,'BAD')
            self.assertEqual(sentinel.read_text(),'KEEP')
    def test_atomic_failed_replace_preserves_old_and_cleans_unique_temp(self):
        with tempfile.TemporaryDirectory() as td:
            dest=Path(td)/'state';dest.write_text('OLD')
            with patch.object(b.os,'replace',side_effect=OSError('synthetic crash')),self.assertRaises(OSError):b.atomic(dest,'NEW')
            self.assertEqual(dest.read_text(),'OLD');self.assertEqual(list(Path(td).iterdir()),[dest])
    def test_atomic_file_and_directory_fsync(self):
        with tempfile.TemporaryDirectory() as td,patch.object(b.os,'fsync',wraps=b.os.fsync) as sync:
            b.atomic(Path(td)/'state','NEW');self.assertEqual(sync.call_count,2)
    def test_offline_cli_and_transport_gate(self):
        cli=subprocess.run([sys.executable,'-B',str(a.ROOT/'btc_copilot.py'),'--watch'],capture_output=True,text=True,timeout=5)
        self.assertNotEqual(cli.returncode,0);self.assertIn('NOT ACTIVATED',cli.stderr)
        with self.assertRaisesRegex(ValueError,'NOT ACTIVATED'):p.launch_guard(a.ROOT,a.NOW)
    def test_settlement_crash_rolls_back_then_retry_is_idempotent(self):
        protocol=a.ROOT/'btc_copilot_protocol.json';start=p.epoch(json.loads(protocol.read_text())['start_utc']);now=start+1000
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'study';study=e.Study(path,protocol,'synthetic-only',{})
            with study.db() as c:
                c.execute('INSERT INTO markets(ticker,opened,closed) VALUES (?,?,?)',('T',start,start+900))
                c.execute("CREATE TRIGGER injected_crash BEFORE UPDATE OF result ON markets BEGIN SELECT RAISE(ABORT,'synthetic crash'); END")
            class Client:
                def market(self,t):return {'data':{'market':{'ticker':t,'status':'finalized','result':'yes'}}}
            with self.assertRaises(sqlite3.IntegrityError):study.settle(Client(),now)
            with study.db() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM outcome_events').fetchone()[0],0)
                self.assertIsNone(c.execute('SELECT result FROM markets').fetchone()[0]);c.execute('DROP TRIGGER injected_crash')
            study.settle(Client(),now+21601);study.settle(Client(),now+21601)
            with study.db() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM outcome_events').fetchone()[0],1)
                self.assertEqual(c.execute('SELECT result FROM markets').fetchone()[0],1)

if __name__=='__main__':
    with patch.object(socket,'create_connection',side_effect=AssertionError('QA NETWORK DISABLED')):unittest.main(verbosity=2)
