import copy,io,json,os,shutil,socket,subprocess,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import runtime_fixture as f
import evidence_runner as run
from coherent_runtime import EvidenceStore,RuntimeBlocked

class EvidenceRunnerTests(unittest.TestCase):
 def test_cli_collect_rejects_before_runtime_or_network(self):
  with patch.object(socket,'create_connection',side_effect=AssertionError('NO NETWORK')),patch.object(run,'EvidenceStore',side_effect=AssertionError('NO STORE')):
   self.assertEqual(run.main(['--collect']),2)
  self.assertFalse((f.ROOT/'runtime').exists())
 def test_gate_denies_even_with_approval_file(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td).resolve()/'candidate';shutil.copytree(f.ROOT,root)
   (root/'activation_approval.json').write_text(json.dumps({'approved':True,'activation_allowed':True,'fee_rules_verified':True}))
   with self.assertRaises(RuntimeBlocked):run.CandidateGate(root)()
   self.assertFalse((root/'runtime').exists())
 def test_narrow_client_blocks_private_account_order_and_setup_routes(self):
  client=run.EvidenceClient(config='/nonexistent/synthetic',guard=lambda *a:None)
  client._headers=Mock(side_effect=AssertionError('NO SIGNING'));client.opener.open=Mock(side_effect=AssertionError('NO TRANSPORT'))
  for route in ('/portfolio/orders','/portfolio/fills','/portfolio/positions','/account/limits','/cfbenchmarks/history/values','/exchange/schedule','/markets/../orders'):
   with self.subTest(route=route),self.assertRaises((RuntimeBlocked,RuntimeError)):client.get(route)
  client._headers.assert_not_called();client.opener.open.assert_not_called()
 def test_client_gate_runs_before_authentication_or_transport(self):
  gate=Mock(side_effect=RuntimeBlocked('NO APPROVAL'));client=run.EvidenceClient(config='/nonexistent/synthetic',guard=gate)
  client._headers=Mock(side_effect=AssertionError('NO SIGNING'));client.opener.open=Mock(side_effect=AssertionError('NO TRANSPORT'))
  with patch('request_pacing.before_read'):
   with self.assertRaises(RuntimeBlocked):client.get('/cfbenchmarks/values')
  gate.assert_called_once_with('/cfbenchmarks/values');client._headers.assert_not_called();client.opener.open.assert_not_called()
 def test_forced_authentication_flag_rejected(self):
  client=run.EvidenceClient(config='/nonexistent/synthetic',guard=lambda *a:None)
  with self.assertRaises(RuntimeBlocked):client.get('/markets',authenticated=True)
 def test_coinbase_gate_before_any_transport(self):
  with patch.object(f.b,'build_opener',side_effect=AssertionError('NO TRANSPORT')):
   with self.assertRaises(RuntimeBlocked):f.b.spot_get(guard=lambda path:(_ for _ in ()).throw(RuntimeBlocked('NO APPROVAL')))
 def test_actual_engine_collector_and_store_share_one_generation(self):
  c,s=f.fixture();raw=copy.deepcopy(c.raw)
  gate=Mock(return_value={'fee_verified_at_utc':f.b.stamp(f.NOW)['utc']})
  engine=run.EvidenceCopilot(c.client,gate)
  with tempfile.TemporaryDirectory() as td:
   root=Path(td).resolve();protocol=f.protocol_file(root)
   with EvidenceStore(root/'runtime',protocol,s['model_version'],{},boot_id='synthetic',wall=lambda:f.NOW) as store:
    with patch.object(f.b.time,'time',return_value=f.NOW),patch.object(f.b,'spot_get',return_value=raw['spot']),patch.object(f.b,'load_settings',return_value=c.settings):
     result=run.Collector(engine,store,gate).once()
    self.assertEqual(result['generation'],1);self.assertEqual(store.latest()['snapshot']['decision'],'NO TRADE')
    self.assertTrue(store.state()['last_observation']['committed']);self.assertGreaterEqual(gate.call_count,3)
    with store._connect() as db:
     self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],1)
     self.assertEqual(db.execute('SELECT COUNT(*) FROM markets').fetchone()[0],1)
    self.assertFalse((root/'runtime/btc_copilot_state.json').exists())
 def test_legacy_state_record_is_explicitly_forbidden(self):
  engine=run.EvidenceCopilot(None,None)
  with self.assertRaises(RuntimeBlocked):engine.record({})
 def test_revoked_gate_after_collection_never_publishes(self):
  store=SimpleNamespace(state=lambda:{'contracts':{},'previous':None},publish=Mock(),failure=Mock(),halted=False)
  engine=SimpleNamespace(collect=Mock(return_value={}))
  gate=Mock(side_effect=[{},RuntimeBlocked('revoked')])
  with self.assertRaises(RuntimeBlocked):run.Collector(engine,store,gate).once()
  store.publish.assert_not_called();store.failure.assert_called_once_with('COLLECTION_FAILED')
 def test_factory_denied_before_every_side_effect(self):
  factory=Mock(side_effect=AssertionError('NO CONSTRUCTION'))
  with self.assertRaises(RuntimeBlocked):run.run_authorized(f.ROOT,run.CandidateGate(f.ROOT),client_factory=factory,store_factory=factory,server_factory=factory)
  factory.assert_not_called()
 def factory_fixture(self):
  operating=json.loads((f.ROOT/'operating_limits.json').read_text())
  context={'credential_config':'/nonexistent/synthetic','runtime_dir':str(f.ROOT/'runtime'),**{k:operating[k] for k in ('quota_bytes','free_reserve_bytes','port')}}
  gate=Mock(return_value=context)
  store=SimpleNamespace(state=Mock(return_value={'contracts':{},'previous':None}),publish=Mock(return_value=1),failure=Mock(),halted=False,settle_one=Mock(return_value=False),close=Mock())
  client=SimpleNamespace();engine=SimpleNamespace(client=client,collect=Mock(return_value={}))
  server=SimpleNamespace(serve_forever=Mock(),shutdown=Mock(),server_close=Mock())
  kwargs={'client_factory':Mock(return_value=client),'engine_factory':Mock(return_value=engine),'store_factory':Mock(return_value=store),'server_factory':Mock(return_value=server),'wall':lambda:run.epoch('2026-12-14T00:05:00+00:00'),'monotonic':lambda:1000,'sleep':Mock()}
  return gate,store,engine,server,kwargs
 def test_executable_lifecycle_uses_explicit_limits_and_closes_cleanly(self):
  gate,store,engine,server,kwargs=self.factory_fixture()
  result=run.run_authorized(f.ROOT,gate,max_cycles=1,**kwargs)
  self.assertEqual(result['cycles'],1);self.assertFalse(result['advice_enabled'])
  kwargs['server_factory'].assert_called_once_with(('127.0.0.1',8769),run.EvidenceView)
  self.assertEqual(kwargs['store_factory'].call_args.kwargs['quota_bytes'],128*1024**3)
  store.publish.assert_called_once();store.close.assert_called_once_with(clean=True);server.server_close.assert_called_once()
 def test_transient_read_error_records_degradation_and_next_poll_recovers(self):
  gate,store,engine,server,kwargs=self.factory_fixture()
  engine.collect.side_effect=[run.ReadError('synthetic outage'),{}]
  result=run.run_authorized(f.ROOT,gate,max_cycles=2,**kwargs)
  self.assertEqual(result['cycles'],2);self.assertEqual(store.publish.call_count,1)
  store.failure.assert_called_once_with('COLLECTION_FAILED');store.close.assert_called_once_with(clean=True)
 def test_factory_refuses_unbound_destination_or_relaxed_storage_before_client(self):
  for key,value in [('runtime_dir','/unapproved/runtime'),('quota_bytes',999999999999)]:
   gate,store,engine,server,kwargs=self.factory_fixture();gate.return_value[key]=value
   with self.assertRaises(RuntimeBlocked):run.run_authorized(f.ROOT,gate,max_cycles=1,**kwargs)
   kwargs['client_factory'].assert_not_called();kwargs['store_factory'].assert_not_called()
 def test_fatal_gate_or_clock_failure_keeps_unclean_runtime(self):
  gate,store,engine,server,kwargs=self.factory_fixture();kwargs['wall']=lambda:float('nan')
  with self.assertRaises(RuntimeBlocked):run.run_authorized(f.ROOT,gate,max_cycles=1,**kwargs)
  store.close.assert_called_once_with(clean=False);server.shutdown.assert_called_once()
 def handler(self,path='/latest.json',host='127.0.0.1:8769',origin=None):
  handler=object.__new__(run.EvidenceView);handler.path=path;handler.headers={'Host':host}
  if origin:handler.headers['Origin']=origin
  handler.server=SimpleNamespace(server_port=8769,store=SimpleNamespace(latest=Mock(return_value={'generation':7,'snapshot':{'decision':'NO TRADE'},'text':'<script>synthetic</script>','advice_enabled':False})))
  handler.wfile=io.BytesIO();handler.send_response=Mock();handler.send_header=Mock();handler.end_headers=Mock();handler.send_error=Mock()
  return handler
 def test_one_envelope_for_json_and_html_escapes_untrusted_text(self):
  h=self.handler();h.do_GET();body=json.loads(h.wfile.getvalue());self.assertEqual(body['generation'],7);h.server.store.latest.assert_called_once()
  h=self.handler('/');h.do_GET();self.assertNotIn(b'<script>',h.wfile.getvalue());self.assertIn(b'&lt;script&gt;',h.wfile.getvalue())
 def test_reader_rejects_untrusted_host_origin_and_arbitrary_paths(self):
  for path,host,origin in [('/latest.json','evil.test',None),('/latest.json','127.0.0.1:8769','https://evil.test'),('/runtime/evidence.sqlite3','127.0.0.1:8769',None),('/latest.json?path=/secrets','127.0.0.1:8769',None)]:
   h=self.handler(path,host,origin);h.do_GET();h.send_error.assert_called_once();h.server.store.latest.assert_not_called()
 def test_reader_unavailable_envelope_never_reuses_snapshot(self):
  h=self.handler();h.server.store.latest.return_value={'decision':'NO TRADE','status':'DATA UNAVAILABLE','snapshot':None,'advice_enabled':False,'text':'NO TRADE / DATA UNAVAILABLE'}
  h.do_GET();self.assertIsNone(json.loads(h.wfile.getvalue())['snapshot'])

if __name__=='__main__':
 with patch.object(socket,'create_connection',side_effect=AssertionError('QA NETWORK DISABLED')):unittest.main(verbosity=2)
