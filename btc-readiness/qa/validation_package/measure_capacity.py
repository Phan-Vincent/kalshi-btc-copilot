"""Sizing sample only. Fake public inputs; no client, collector or retained state."""
import copy,importlib.metadata,json,platform,sqlite3,subprocess,sys,tempfile,zlib
from pathlib import Path
HERE=Path(__file__).resolve().parent;AUDIT=HERE.parent
sys.path.insert(0,str(AUDIT));sys.argv.insert(1,'candidate')
import remediation_audit as a
from unittest.mock import patch

def main():
    packages={}
    for name in ['cryptography']:
        try:packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:packages[name]='not installed'
    runtime={'python':sys.version,'python_executable':sys.executable,'sqlite':sqlite3.sqlite_version,'node':subprocess.run(['node','--version'],capture_output=True,text=True,check=True).stdout.strip(),'platform':platform.platform(),'optional_authenticated_read_dependency':packages,'authenticated_reads_tested':False,'installation_or_upgrade_performed':False,'future_deployment_dependency_lock_complete':False}
    (HERE/'runtime_inventory.json').write_text(json.dumps(runtime,indent=2)+'\n')
    raw=copy.deepcopy(a.RAW);raw['trades']['data']={'trades':[]};s=a.collect(raw)
    # Refresh the HTML fixture with this source-bound offline snapshot.
    (AUDIT/'candidate_ui_fixture.json').write_text(a.b.dump(s))
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);audit=a.r.Audit(root/'sizing.sqlite3');c=a.make(raw);c.state_path=root/'state.json';c.raw=raw;c.prior=None;c.settings=a.SETTINGS
        for index in range(100):
            snap=copy.deepcopy(s);snap['epoch']+=index*15;snap['valid_until_epoch']+=index*15
            # Fixed public fixture repeatedly recorded: storage measurement, not a valid market replay.
            audit.record(snap,raw,None,a.SETTINGS,root/'absent')
            with patch.object(a.b,'WORK',root):a.b.Copilot.record(c,snap)
        with audit.connect() as db:
            compressed=db.execute('SELECT avg(length(payload)) FROM observations').fetchone()[0]
            observations=db.execute('SELECT count(*) FROM observations').fetchone()[0]
        log_bytes=(root/'btc_copilot_observations.jsonl').stat().st_size
        sample={'synthetic_records':100,'observations_in_sample':observations,'database_file_bytes_including_schema_and_source':(root/'sizing.sqlite3').stat().st_size,'compressed_observation_payload_mean_bytes':compressed,'diagnostic_jsonl_mean_bytes':log_bytes/100,'study_checkpoint_payload_example_bytes':len(zlib.compress(json.dumps({'snapshot':s,'raw':raw},default=str).encode()))}
    daily=86400//15;count=daily*70;retained=daily*7
    result={'status':'MEASURED_SAMPLE_NOT_ENFORCED_CAPACITY_LIMIT','sample':sample,'proposed_70_day_observations':count,'proposed_7_day_retained_observations':retained,'estimated_rolling_payload_bytes':round(retained*compressed),'estimated_unrotated_diagnostic_bytes':round(count*sample['diagnostic_jsonl_mean_bytes']),'proposed_70_day_slots':70*96,'limitations':['Repeated synthetic fixture; not a market worst-case or throughput soak test','No full study/signals/outcome archive or WAL growth measured','SQLite deletes do not guarantee file shrinkage','HTTP bodies, evidence reports, journal and diagnostic rows have no established global byte bound','Current runtime has no enforced disk quota/capacity monitor/retention schedule; proposal cannot rely on these as safeguards'],'proposed_not_enforced_limits':{'dedicated_evidence_volume_bytes':10*1024**3,'minimum_free_bytes_before_poll':2*1024**3,'retained_evidence_warning_bytes':5*1024**3,'action_on_limit':'halt, retain evidence; no automatic deletion; independent sizing/soak and stop implementation required'}}
    (HERE/'capacity_measurement.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
