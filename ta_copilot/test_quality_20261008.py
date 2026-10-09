"""Synthetic coverage and actual app.js presentation regressions."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import ta_copilot as ta
from test_fix_20261007b import APP_HARNESS

class CoverageTests(unittest.TestCase):
    def test_missing_elapsed_samples_keep_sigma_and_projection(self):
        # Missing settlement-minute samples are counted, but sigma stays: the
        # Q12 uncertainty for them belongs to batch C, which widens it.
        for left in (59, 30, 1):
            samples = [{"time": t, "value": 100} for t in range(940, 1000-left)]
            complete = ta.window_distance(100, 110, left, .01, 1000, samples)
            self.assertEqual(complete['missing_seconds'], 0)
            row = ta.window_distance(100, 110, left, .01, 1000, samples[1:])
            self.assertEqual(row['missing_seconds'], 1)
            self.assertNotIn('sigma_unavailable_reason', row)
            self.assertIsNotNone(row['distance_sigmas'])
            reference = ((len(samples)-1)*100+(left+1)*110)/60
            self.assertAlmostEqual(row['distance'], reference-100)
            self.assertAlmostEqual(row['sigma_1m_usd'], .01*reference)  # logged in minute rows
            self.assertGreaterEqual(row['distance_sd_usd'], .01*reference*math.sqrt((left/60)**3/3)-1e-12)

    def test_second_in_progress_is_not_missing(self):
        # The sampler stamps second S at about S+0.05; a state built before that
        # must not report S as missing, and an early sample must not hide a gap.
        for second in (941, 970, 999):
            before = [{"time": t, "value": 100} for t in range(940, second)]
            row = ta.window_distance(100, 100, 1000-(second+.02), .01, 1000, before)
            self.assertEqual((row['observed_seconds'], row['missing_seconds']), (second-940, 0))
            with_current = before + [{"time": second, "value": 100}]
            row = ta.window_distance(100, 100, 1000-(second+.5), .01, 1000, with_current)
            self.assertEqual((row['observed_seconds'], row['missing_seconds']), (second-939, 0))
            gap = [p for p in with_current if p['time'] != 940]
            row = ta.window_distance(100, 100, 1000-(second+.5), .01, 1000, gap)
            self.assertEqual(row['missing_seconds'], 1)
            # Once second S has ended without a sample, it is missing.
            row = ta.window_distance(100, 100, 1000-(second+1), .01, 1000, before)
            self.assertEqual(row['missing_seconds'], 1)

    def test_coverage_partitions_all_closes_preserves_last_200(self):
        logger = ta.TallyLogger.__new__(ta.TallyLogger)
        # The 5 oldest windows err by $50, the rest by $1: only the latest 200 count.
        logger.closes = {str(i): {'samples':60,'proxy_avg_60s':150 if i < 5 else 101,'close_time':str(i).zfill(4)}
                         for i in range(205)}
        logger.expirations = {str(i):100 for i in range(205)}
        logger.settled = set(logger.expirations)
        logger.closes.update({'partial':{'samples':59},
                              'pending':{'samples':60,'proxy_avg_60s':100},            # not settled yet
                              'value_pending':{'samples':60,'proxy_avg_60s':100},      # value still asked for
                              'gave_up':{'samples':60,'proxy_avg_60s':100},            # value never came
                              'invalid':{'samples':60,'proxy_avg_60s':None},
                              'pending_invalid':{'samples':60,'proxy_avg_60s':None}})  # settlement checked first
        logger.settled |= {'value_pending', 'gave_up', 'invalid'}
        logger.expirations.update({'value_pending':None,'gave_up':None,'invalid':100})
        logger.value_pending = {'value_pending': 1000}
        logger.refresh_proxy()
        self.assertEqual(logger.proxy['coverage'], {'total_closes':211,'scored':205,'incomplete_samples':1,
                         'awaiting_settlement':3,'missing_settlement':1,'invalid_proxy':1})
        self.assertEqual(logger.proxy['n'],200)
        self.assertEqual((logger.proxy['median_abs'],logger.proxy['p90_abs'],logger.proxy['bias']),(1,1,1))
        logger.settled.add('pending'); logger.expirations['pending']=100
        logger.refresh_proxy()
        self.assertEqual(logger.proxy['coverage']['awaiting_settlement'],2)
        self.assertEqual(logger.proxy['coverage']['scored'],206)

    def test_giving_up_on_a_value_moves_it_out_of_awaiting(self):
        logger = ta.TallyLogger.__new__(ta.TallyLogger)
        logger.closes = {'A': {'samples':60,'proxy_avg_60s':100}}
        logger.opened, logger.settled = {}, {'A'}
        logger.expirations, logger.value_pending = {'A':None}, {'A':0}
        logger.refresh_proxy()
        self.assertEqual(logger.proxy['coverage']['awaiting_settlement'],1)
        logger.settle_pending(ta.SETTLE_VALUE_RETRY+1)  # past the retry window: no request is made
        self.assertEqual(logger.value_pending,{})
        self.assertEqual((logger.proxy['coverage']['awaiting_settlement'],logger.proxy['coverage']['missing_settlement']),(0,1))

SCENARIOS = APP_HARNESS[:APP_HARNESS.index('const out = {};')] + r'''
// The page clock follows the fake clock, so window expiry can be simulated.
const RealDate=Date;
class FakeDate extends RealDate{constructor(...a){a.length?super(...a):super(T0+now);} static now(){return T0+now;}}
context.Date=FakeDate;
const out={};
const displayed=()=>({lean:el('lean').innerHTML,families:el('families').innerHTML,
 warning:el('tallyStale').textContent,width:el('bBull').style.width,count:el('nBull').textContent,
 readings:el('readings').innerHTML,mtf:el('mtf').innerHTML,sigma:el('sig').textContent,
 coverage:el('settlementCoverage').textContent,live:el('liveText').textContent});
function withWindow(s,extra={}){const at=RealDate.parse(s.server_time);
 s.window={ticker:'T',age:0,strike:100,distance:24,distance_sigmas:2,distance_sd_usd:12,
 distance_source:'projected 60s average',observed_seconds:30,missing_seconds:0,
 open_time:new RealDate(at-60000).toISOString(),close_time:new RealDate(at+840000).toISOString(),
 price_path:[],yes_mid_path:[],recent_trades:[],display_quotes:{},...extra};return s;}
const fit=()=>vm.runInContext('fitNext',context);
(async()=>{
 vm.createContext(context);vm.runInContext(code,context);await flush();
 const S=streams[0];
 S.emit('state',withWindow(state('1m',100,0)));out.healthy=displayed();
 S.emit('state',withWindow(state('1m',100,500),{distance_source:'composite',observed_seconds:0}));out.beforeFinalMinute=displayed();
 let s=withWindow(state('1m',100,1000));s.history_stale=['1m'];S.emit('state',s);out.stale=displayed();
 s=withWindow(state('1m',100,2000));s.frames['1m'].history.gap=true;S.emit('state',s);out.gap=displayed();
 S.emit('state',withWindow(state('1m',100,3000),{missing_seconds:1}));out.missing=displayed();
 s=withWindow(state('1m',100,3500),{distance:null,distance_sigmas:null,distance_sd_usd:null,observed_seconds:0});
 s.composite=null;s.stale=true;S.emit('state',s);out.noComposite=displayed();
 s=withWindow(state('1m',100,4000));s.stale=true;S.emit('state',s);out.staleSpot=displayed();
 S.emit('state',withWindow(state('1m',100,5000)));out.recovered=displayed();
 await advance(12000);out.silent=displayed();
 // A fresh poll is enough to recover even while the SSE reconnect is pending.
 fetches.at(-1).respond(withWindow(state('1m',100,18000)));await flush();out.streamRecovered=displayed();
 // The window closes during a silence: the badge keeps saying "No update".
 await advance(5000);
 s=withWindow(state('1m',100,25000),{close_time:new RealDate(T0+40000).toISOString()});
 streams.at(-1).emit('state',s);await advance(12000);out.silentBeforeClose=displayed();
 for(let i=0;i<40&&el('countdown').textContent!=='No open window';i++)await advance(250);
 out.closedDuringSilence=displayed();out.closedCountdown=el('countdown').textContent;
 // Per-timeframe: the 5m tab with a healthy 1m headline.
 buttons[1].onclick();
 streams.at(-1).emit('state',withWindow(state('5m',100,50000,['1m','5m'])));out.healthy5m=displayed();
 s=withWindow(state('5m',100,51000,['1m','5m']));s.frames['5m'].history.gap=true;
 streams.at(-1).emit('state',s);out.gap5m=displayed();
 s=withWindow(state('5m',100,52000,['1m','5m']));s.stale=true;streams.at(-1).emit('state',s);out.allSuppressed=displayed();
 // A pending chart fit (tab switch) survives the silence re-render of the old timeframe.
 buttons[0].onclick();out.fitAfterSwitch=fit();
 await advance(11000);out.fitAfterSilence=fit();out.silentAfterSwitch=displayed();
 out.silentStreamClosed=streams.at(-1).closed;
 fetches.at(-1).respond(withWindow(state('1m',100,70000)));await flush();out.fitAfterData=fit();
 out.afterSwitchData=displayed();
 buttons[1].onclick();streams.at(-1).emit('state',state('5m',100,71000,['5m']));out.no1m=displayed();
 s=state('5m',100,72000,['1m','5m']);s.frames['1m'].family.flow_ready=false;
 s.frames['1m'].family.votes.flow='neutral';s.frames['1m'].family.flow_coverage_seconds=5;
 s.venues={Coinbase:{price:100,age:1,included:true,age_basis:'ticker'},Kraken:{price:102,age:1,included:true,age_basis:'trade'},Gemini:{price:101,age:1,included:false,age_basis:'fetch'}};
 s.proxy_record={n:1,median_abs:2,p90_abs:2,bias:2,too_few:true,coverage:{total_closes:7,scored:1,
  incomplete_samples:3,awaiting_settlement:1,missing_settlement:2,invalid_proxy:0}};
 streams.at(-1).emit('state',s);out.quality=el('compositeQuality').textContent;out.venues=el('venues').innerHTML;
 out.record=el('tallyRecord').textContent;out.proxy=el('proxyCoverage').textContent;out.warming=el('families').innerHTML;
 s.server_time=new RealDate(T0+73000).toISOString();s.venues.Kraken.included=false;streams.at(-1).emit('state',s);out.single=el('compositeQuality').textContent;
 s.server_time=new RealDate(T0+74000).toISOString();s.venues.Coinbase.included=false;s.composite=null;streams.at(-1).emit('state',s);out.noVenues=el('compositeQuality').textContent;
 console.log(JSON.stringify(out));
})().catch(e=>{console.error(e);process.exit(1)});
'''

SIGMA = '+2.00σ · sd $12'

@unittest.skipUnless(shutil.which('node'),'node not installed')
class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run=subprocess.run(['node','-'],input=SCENARIOS,text=True,capture_output=True,
                           env={**os.environ,'APP_JS':str(Path(__file__).with_name('app.js'))},timeout=30)
        if run.returncode: raise AssertionError(run.stderr)
        cls.out=json.loads(run.stdout)

    def assertHealthy(self, row):
        self.assertIn('Bullish',row['lean'])
        self.assertEqual(row['width'],'100%')
        self.assertIn('badge bull',row['readings'])
        self.assertIn('▲',row['mtf']); self.assertIn('1/0',row['mtf'])
        self.assertEqual(row['sigma'],SIGMA)

    def test_degraded_readings_and_recovery(self):
        reasons = {'stale':'1m history stale','gap':'1m history has missing bars',
                   'staleSpot':'spot or composite unavailable','silent':'no update for over 10 s',
                   'no1m':'1m history unavailable'}
        for name, reason in reasons.items():
            with self.subTest(name=name):
                row=self.out[name]
                self.assertIn('Unavailable',row['lean'])
                self.assertEqual(row['width'],'0%')
                self.assertEqual(row['count'],'')
                self.assertNotIn('class="bull"',row['families'])
                self.assertIn(reason,row['warning'])
        for name in ('stale','gap','staleSpot','silent'):
            with self.subTest(name=name):
                row=self.out[name]
                self.assertNotIn('badge bull',row['readings']); self.assertIn('Unavailable',row['readings'])
                self.assertIn(reasons[name],row['readings'])
                self.assertNotIn('class="bull"',row['mtf']); self.assertNotIn('1/0',row['mtf'])
                self.assertEqual(row['sigma'],'Unavailable · '+reasons[name])
        for name in ('healthy','recovered','streamRecovered'):
            with self.subTest(name=name):
                self.assertHealthy(self.out[name])

    def test_silence_names_its_own_cause(self):
        # Page silence is not reported as a spot/composite outage, even when
        # the window closes during it.
        self.assertTrue(self.out['silent']['live'].startswith('No update for'))
        self.assertNotIn('spot or composite',self.out['silent']['families'])
        self.assertEqual(self.out['closedCountdown'],'No open window')
        self.assertTrue(self.out['closedDuringSilence']['live'].startswith('No update for'),
                        self.out['closedDuringSilence']['live'])
        self.assertIn('no update for over 10 s',self.out['closedDuringSilence']['warning'])

    def test_missing_samples_keep_sigma_and_label_coverage(self):
        self.assertEqual(self.out['missing']['sigma'],SIGMA)
        self.assertEqual(self.out['missing']['coverage'],'30/60 s observed · 30 s projected (1 missing elapsed '
                         'seconds; remainder still ahead). Historical proxy error is separate.')
        self.assertEqual(self.out['noComposite']['coverage'],'')
        self.assertEqual(self.out['beforeFinalMinute']['coverage'],'')
        self.assertEqual(self.out['beforeFinalMinute']['sigma'],SIGMA)
        self.assertIn('30/60 s observed',self.out['silentBeforeClose']['coverage'])
        self.assertEqual(self.out['closedDuringSilence']['coverage'],'')
        self.assertIn('warming up (5/60 s)',self.out['warming'])

    def test_each_timeframe_is_judged_on_its_own_history(self):
        self.assertEqual(self.out['healthy5m']['mtf'].count('▲'),2)
        self.assertIn('badge bull',self.out['healthy5m']['readings'])
        row=self.out['gap5m']
        self.assertIn('Bullish',row['lean']); self.assertEqual(row['sigma'],SIGMA)  # the headline and sigma use 1m
        self.assertNotIn('badge bull',row['readings']); self.assertIn('5m history has missing bars',row['readings'])
        self.assertEqual(row['mtf'].count('▲'),1); self.assertEqual(row['mtf'].count('1/0'),1)
        self.assertIn('title="5m history has missing bars">—</td>',row['mtf'])
        self.assertTrue(row['mtf'].endswith('<td class="muted" title="5m history has missing bars">—</td></tr>'))
        # Every column suppressed: short marks only, so the table fits a phone.
        self.assertNotIn('Unavailable',self.out['allSuppressed']['mtf'])
        self.assertNotIn('1/0',self.out['allSuppressed']['mtf'])

    def test_silence_rerender_keeps_a_pending_fit(self):
        self.assertTrue(self.out['fitAfterSwitch'])
        self.assertTrue(self.out['fitAfterSilence'])
        self.assertIn('no update for over 10 s',self.out['silentAfterSwitch']['warning'])
        self.assertTrue(self.out['silentStreamClosed'])  # the snapshot below arrives by poll
        self.assertFalse(self.out['fitAfterData'])
        self.assertHealthy(self.out['afterSwitchData'])

    def test_quality_and_performance_labels(self):
        self.assertIn('2/4 venues',self.out['quality']);self.assertIn('$2',self.out['quality'])
        rows={r.split('</td>')[0].removeprefix('<tr><td>').split()[0]:r for r in self.out['venues'].split('</tr>') if r}
        self.assertIn('(exchange timestamp age)',rows['Coinbase'])
        self.assertIn('(trade age)',rows['Kraken'])
        self.assertIn('(fetch age)',rows['Gemini'])
        self.assertIn('degraded: single venue',self.out['single'])
        self.assertIn('Composite unavailable',self.out['noVenues'])
        self.assertIn('Legacy 1m opening lean vs settlement',self.out['record'])
        self.assertEqual(self.out['proxy'],'All recorded closes: 1/7 scored · 3 incomplete samples · '
                         '1 awaiting settlement · 2 settlement value unavailable · 0 invalid proxy '
                         '(each close counted once, in this order).')

if __name__=='__main__': unittest.main()
