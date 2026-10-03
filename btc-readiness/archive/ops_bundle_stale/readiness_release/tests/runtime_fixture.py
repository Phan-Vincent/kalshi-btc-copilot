"""Synthetic clock and copied public fixture; no account or production state."""
from pathlib import Path
import copy,json,sys,tempfile
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]/'candidate'
sys.path.insert(0,str(ROOT))
import btc_copilot as b
import btc_copilot_research as r
from study_policy import epoch
RAW=json.loads((ROOT.parents[1]/'original/work/btc_copilot_test_fixture.json').read_text())
NOW=RAW['benchmark']['data']['data']['payload'][-1]['time']/1000+1.5

def fixture():
 raw=copy.deepcopy(RAW);raw['trades']['data']={'trades':[]}
 class Fake:
  def pages(self,*args,**kw):return {'pages':[{'data':{'markets':[raw['market']['data']['market']]}}],'complete':True}
  def market(self,*args):return raw['market']
  def event(self,*args):return raw['event']
  def orderbook(self,*args):return raw['book']
  def get(self,path,params=None):
   if path.endswith('/candlesticks'):return raw['candles']
   if path.startswith('/series/'):return raw['series']
   return raw[{'/cfbenchmarks/values':'benchmark','/markets/trades':'trades','/exchange/status':'exchange'}[path]]
 c=object.__new__(b.Copilot);c.client=Fake();c.positions=False;c.raw={};c.state={'contracts':{},'previous':None}
 settings=r.load_settings(ROOT/'btc_copilot_settings.json');settings['fee_verified_at']=b.stamp(NOW)['utc']
 health={'requests':{k:{'started_at_epoch':NOW-.2,'finished_at_epoch':NOW,'latency_seconds':.2} for k in ('market','event','series','book','benchmark','trades','candles','spot','exchange')}}
 with patch.object(b.time,'time',return_value=NOW),patch.object(b,'spot_get',return_value=raw['spot']),patch.object(b,'load_settings',return_value=settings):
  s=c.collect(replay_health=health,persist=False)
 s['observation_monotonic']=1000.0
 return c,s

def protocol_file(root):
 p=json.loads((ROOT/'btc_copilot_protocol.json').read_text())
 start=int(NOW)//86400*86400
 for key,days in [('start_utc',0),('validation_start_utc',7),('holdout_start_utc',14),('end_utc',70),('release_utc',72)]:p[key]=b.stamp(start+days*86400)['utc']
 p['approval_policy']['deadline_utc']=p['start_utc']
 dest=Path(root)/'synthetic_protocol.json';dest.write_text(json.dumps(p));return dest

def later(s,delta=15):
 result=copy.deepcopy(s)
 for key in ('epoch','valid_until_epoch','observation_monotonic'):result[key]+=delta
 result['time_remaining_seconds']-=delta;result['timestamp']=b.stamp(result['epoch'])
 for request in result['retrieval_health']['requests'].values():
  request['started_at_epoch']+=delta;request['finished_at_epoch']+=delta
 return result
