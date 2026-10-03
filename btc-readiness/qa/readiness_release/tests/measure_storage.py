"""Temporary synthetic storage sizing; no network, account or protected state.

Retiming an already-computed public fixture exercises persistence only, not
market-data validation or strategy correctness. Output goes to stdout; the
caller chooses whether to retain it as review evidence.
"""
import copy
import hashlib
import json
from pathlib import Path
import platform
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from unittest.mock import patch
import zlib
import runtime_fixture as f
from coherent_runtime import EvidenceStore, canonical

ROOT=Path(__file__).resolve().parents[1]
GIB=1024**3


def denied(*args,**kwargs):
    raise AssertionError('Network forbidden in synthetic capacity measurement')


def measure(samples=256,warmup=64):
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='btc-synthetic-capacity-') as temporary:
        work=Path(temporary).resolve()
        protocol=f.protocol_file(work)
        c,base=f.fixture()
        now=[f.NOW]
        sources={name:(ROOT/'candidate'/name).read_text() for name in
                 ('coherent_runtime.py','btc_copilot.py','btc_copilot_research.py','btc_copilot_evidence.py','study_policy.py')}
        source_hashes={name:hashlib.sha256(content.encode()).hexdigest() for name,content in sources.items()}
        with EvidenceStore(work/'runtime',protocol,base['model_version'],sources,
                           boot_id='synthetic-capacity-only',wall=lambda:now[0],quota_bytes=128*GIB,
                           free_reserve_bytes=1024**2) as store:
            sizes=[{'generations':0,'database_bytes':store.path.stat().st_size}]
            opened=f.b.dt(base['open_time']).timestamp()
            original_raw=copy.deepcopy(c.raw)
            for index in range(samples):
                delta=15*index
                s=f.later(base,delta)
                slot=int((s['epoch']-opened)//900)
                s['ticker']='SYNTHETIC-CAPACITY-%06d'%slot
                s['open_time']=f.b.stamp(opened+slot*900)['utc']
                s['close_time']=f.b.stamp(opened+(slot+1)*900)['utc']
                s['time_remaining_seconds']=opened+(slot+1)*900-s['epoch']
                c.state=store.state()
                c.prior=copy.deepcopy(c.state.get('previous'))
                c.raw=copy.deepcopy(original_raw)
                now[0]=s['epoch']
                store.publish(c,s)
                if (index+1)%warmup==0 or index+1==samples:
                    sizes.append({'generations':index+1,'database_bytes':store.path.stat().st_size})
            with closing(sqlite3.connect(store.path)) as db:
                tables={name:db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in
                        ('generations','observations','markets','checkpoints','signals','outcome_events','implementations')}
                blob=db.execute('SELECT MIN(LENGTH(payload)),AVG(LENGTH(payload)),MAX(LENGTH(payload)) FROM observations').fetchone()
                source=db.execute('SELECT SUM(LENGTH(source)) FROM implementations').fetchone()[0]
                meta_sources=db.execute("SELECT LENGTH(value) FROM meta WHERE key='sources'").fetchone()[0]
                publication=db.execute('SELECT LENGTH(body),LENGTH(state) FROM publication').fetchone()
                page_size=db.execute('PRAGMA page_size').fetchone()[0]
            end_size=store.path.stat().st_size
    if any(hashlib.sha256((ROOT/'candidate'/name).read_bytes()).hexdigest()!=expected for name,expected in source_hashes.items()):
        raise RuntimeError('Candidate source changed during capacity measurement; repeat on stable source')
    windows=[(b['database_bytes']-a['database_bytes'])/(b['generations']-a['generations'])
             for a,b in zip(sizes,sizes[1:]) if a['generations']>=warmup]
    observed_per_generation=max(windows)
    observations=70*86400//15
    markets=70*96
    main_projection=observations*observed_per_generation+sizes[0]['database_bytes']
    # Deliberately additive although some checkpoints/signals are already in the
    # measured main growth. This avoids claiming a benign fixture is worst case.
    signal_checkpoint_extra=markets*(1+2*8)*max(blob)
    outcome_extra=(72*86400//15)*4096
    source_extra=max(source+meta_sources,1024**2)
    subtotal=main_projection+signal_checkpoint_extra+outcome_extra+source_extra
    uncertainty_factor=2
    recommended_main=subtotal*uncertainty_factor
    recommended_quota_gib=((int(recommended_main*3/GIB)+15)//16)*16
    default_main=(8*GIB-1024**2)//(3*page_size)*page_size
    free=shutil.disk_usage(ROOT).free
    return {
      'scope':'SYNTHETIC_TEMPORARY_CAPACITY_MEASUREMENT_NOT_STRATEGY_VALIDATION',
      'candidate_source_sha256':source_hashes,
      'measurement':{'samples':samples,'warmup_generations':warmup,'cadence_seconds':15,
         'network_blocked':True,'temporary_database_removed':True,'public_fixture_bytes':(ROOT.parent/'original/work/btc_copilot_test_fixture.json').stat().st_size,
         'runtime_seconds':round(time.monotonic()-started,3),'database_size_samples':sizes,
         'post_warmup_window_bytes_per_generation':windows,'conservative_observed_bytes_per_generation':observed_per_generation,
         'observation_payload_compressed_bytes':{'min':blob[0],'mean':blob[1],'max':blob[2]},
         'retained_source_bytes':source+meta_sources,'latest_publication_bytes':publication[0],'latest_state_bytes':publication[1],
         'table_counts':tables,'sqlite_page_size':page_size,'sqlite_version':sqlite3.sqlite_version,'python_version':platform.python_version()},
      'projection':{'collection_days':70,'release_wait_days':2,'generations_70_days':observations,'markets_70_days':markets,
         'extrapolated_generation_database_bytes':int(main_projection),'additional_all_markets_one_checkpoint_plus_two_blobs_per_eight_arms_bytes':int(signal_checkpoint_extra),
         'additional_outcome_rows_one_per_poll_72_days_at_4096_bytes':outcome_extra,
         'additional_source_budget_bytes':source_extra,'subtotal_main_database_bytes':int(subtotal),
         'uncertainty_multiplier':uncertainty_factor,'planning_main_database_bytes':int(recommended_main),
         'recommended_explicit_total_quota_gib':recommended_quota_gib,'recommended_minimum_free_space_reserve_gib':16,
         'default_total_quota_gib':8,'default_effective_main_database_bytes':default_main,
         'default_main_budget_estimated_days_at_fixture_rate':round(default_main/observed_per_generation*15/86400,3),
         'default_quota_sufficient_for_projection':default_main>=recommended_main,
         'quota_factor_explanation':'Runtime max_page_count reserves roughly 2/3 total quota for rollback journal and metadata; only about1/3 is available for main database.'},
      'filesystem_metadata':{'available_bytes_at_measurement':free,'sufficient_for_recommended_quota_plus_16gib_reserve':free>=(recommended_quota_gib+16)*GIB},
      'limits':['Repeated public fixture is compressible; this is not a worst-case bound or throughput benchmark.',
                'Retimed synthetic snapshots exercise storage only, not genuine time-consistent market replay.',
                'All generations retained. No VACUUM, deletion, downsampling, compressed archival or production changes were performed.',
                'Additional checkpoint/signal budget intentionally double-counts any already observed instances.',
                'Outcome budget assumes at most one settle_one event per15-secondpoll; different scheduling must be measured again.',
                'Two-times planning headroom is a proposal, not proof that varying API payloads fit.',
                'Free disk is momentary filesystem metadata, not reserved capacity or deployment approval.',
                'Actual source changes require remeasurement and source/config/package review before activation.'],
      'activation_allowed':False
    }


if __name__=='__main__':
    with patch.object(socket,'socket',side_effect=denied),patch.object(socket,'create_connection',side_effect=denied):
        print(json.dumps(measure(),indent=2))
