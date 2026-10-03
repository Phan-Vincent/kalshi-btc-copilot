"""Single-writer evidence storage. No transport, credentials, advice or order methods.

The database is the sole authority; old JSON state/output files are never read.
SQLite DELETE journaling + FULL sync provide OS-level crash recovery, not a
claim about hardware power-loss guarantees. An operator must review a halted
cohort; this module never resets clocks, deletes evidence or creates a new one.
"""
from contextlib import contextmanager, closing
from pathlib import Path
import copy, fcntl, hashlib, json, math, os, shutil, sqlite3, stat, subprocess, sys, time, threading

from btc_copilot_research import Audit
from btc_copilot_evidence import Study
from study_policy import check_clock, validate_protocol

class RuntimeBlocked(ValueError):pass

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),default=str,allow_nan=False)

def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()

def boot_identity():
    if sys.platform=='darwin':
        value=subprocess.run(['/usr/sbin/sysctl','-n','kern.boottime'],capture_output=True,text=True,check=True,timeout=2).stdout.strip()
    elif sys.platform.startswith('linux'):
        value=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    else:raise RuntimeBlocked('Unsupported boot identity source')
    if not value:raise RuntimeBlocked('Missing boot identity')
    return hashlib.sha256(value.encode()).hexdigest()

def finite(value):return type(value) in (int,float) and math.isfinite(value)

def no_links(path):
    for item in (path,*path.parents):
        if item.is_symlink():raise RuntimeBlocked('Linked runtime path forbidden')
    if path.exists() and path.is_file() and path.stat().st_nlink!=1:
        raise RuntimeBlocked('Hard-linked runtime file forbidden')

def single_writer(method):
    def guarded(self,*args,**kwargs):
        if not self.operation_lock.acquire(blocking=False):raise RuntimeBlocked('Concurrent runtime mutation rejected')
        try:return method(self,*args,**kwargs)
        finally:self.operation_lock.release()
    return guarded

class EvidenceStore:
    def __init__(self,root,protocol_path,model_version,sources,*,quota_bytes=8*1024**3,
                 free_reserve_bytes=64*1024**2,boot_id=None,wall=time.time,
                 validator=validate_protocol):
        self.root=Path(root).absolute();self.path=self.root/'evidence.sqlite3'
        self.session_owned=False;self.lock_fd=None;self.wall=wall;self.boot_id=boot_id or boot_identity()
        self.operation_lock=threading.RLock()
        self.halted=False;self.quota=quota_bytes;self.reserve=free_reserve_bytes
        if type(quota_bytes) is not int or quota_bytes<8*1024**2:raise RuntimeBlocked('Invalid storage quota')
        if type(free_reserve_bytes) is not int or free_reserve_bytes<1024**2:raise RuntimeBlocked('Invalid free-space reserve')
        no_links(self.root)
        if not self.root.exists():self.root.mkdir(mode=0o700)
        if self.root.stat().st_uid!=os.getuid() or stat.S_IMODE(self.root.stat().st_mode)&0o077:
            raise RuntimeBlocked('Runtime directory must be private to its owner')
        try:
            lock=self.root/'writer.lock';no_links(lock)
            self.lock_fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            if os.fstat(self.lock_fd).st_nlink!=1:raise RuntimeBlocked('Linked writer lock')
            try:fcntl.flock(self.lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeBlocked('Another runtime writer holds the lock') from None
            session=self.root/'RUNNING';no_links(session)
            if session.exists():raise RuntimeBlocked('Unclean shutdown requires operator review; no automatic resume')
            fd=os.open(session,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            self.session_owned=True
            try:os.write(fd,b'UNCLEAN_UNTIL_ORDERLY_CLOSE');os.fsync(fd)
            finally:os.close(fd)
            self._sync_directory()
            self._capacity()
            if (self.root/'HALTED').exists():raise RuntimeBlocked('Persisted halt requires review')
            existed=self.path.exists()
            if existed:
                with self._connect() as c:
                    if c.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise RuntimeBlocked('Database integrity failure')
                    config=c.execute('SELECT config,boot FROM runtime_control').fetchone()
                    if not config:raise RuntimeBlocked('Incomplete runtime identity')
                    if config!=(canonical({'version':model_version,'quota':quota_bytes,'reserve':free_reserve_bytes}),self.boot_id):
                        self._halt('RUNTIME_OR_BOOT_CHANGED');raise RuntimeBlocked('Runtime configuration or boot changed')
            # Schemas are established before any observation transaction. Never
            # run executescript (implicit COMMIT) inside a generation transaction.
            self.study=Study(self.path,protocol_path,model_version,sources,validator=validator)
            self.audit=Audit(self.path,retain_all=True)
            with self._connect() as c:
                c.executescript('''
                CREATE TABLE IF NOT EXISTS runtime_control(id INTEGER PRIMARY KEY CHECK(id=1),config TEXT,boot TEXT,degraded INTEGER,reason TEXT);
                CREATE TABLE IF NOT EXISTS generations(id INTEGER PRIMARY KEY,epoch REAL UNIQUE,identity TEXT UNIQUE,publication_hash TEXT,state_hash TEXT,prior_hash TEXT,chain_hash TEXT,audit_id INTEGER UNIQUE);
                CREATE TABLE IF NOT EXISTS publication(id INTEGER PRIMARY KEY CHECK(id=1),generation INTEGER,body TEXT,state TEXT);
                CREATE TABLE IF NOT EXISTS runtime_events(id INTEGER PRIMARY KEY,epoch REAL,kind TEXT);
                ''')
                c.execute('INSERT OR IGNORE INTO runtime_control VALUES (1,?,?,1,?)',
                          (canonical({'version':model_version,'quota':quota_bytes,'reserve':free_reserve_bytes}),self.boot_id,'NO_OBSERVATION'))
                self._verify(c,deep=True)
        except BaseException:
            self.close(clean=False);raise

    def _sync_directory(self):
        fd=os.open(self.root,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    def close(self,clean=True):
        if self.lock_fd is not None:
            try:
                if clean and self.session_owned and not self.halted:
                    (self.root/'RUNNING').unlink();self._sync_directory()
            finally:os.close(self.lock_fd);self.lock_fd=None
        self.session_owned=False
    def __enter__(self):return self
    def __exit__(self,exc_type,*args):self.close(clean=exc_type is None)
    def _owned(self):
        if self.lock_fd is None:raise RuntimeBlocked('Closed runtime')
        if self.halted or (self.root/'HALTED').exists():raise RuntimeBlocked('Runtime halted')
    def _halt(self,reason):
        self.halted=True
        try:
            path=self.root/'HALTED';no_links(path)
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:os.write(fd,reason.encode());os.fsync(fd)
            finally:os.close(fd)
            fd=os.open(self.root,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        except OSError:pass # RUNNING was fsynced before work; its presence blocks unclean restart.
    def _capacity(self):
        no_links(self.path)
        total=0
        for path in self.root.iterdir():
            no_links(path)
            if not path.is_file():raise RuntimeBlocked('Unexpected runtime directory')
            total+=path.stat().st_size
        if total>=self.quota or shutil.disk_usage(self.root).free<self.reserve:
            self._halt('STORAGE_LIMIT');raise RuntimeBlocked('Storage limit or free-space reserve reached')
    @contextmanager
    def _connect(self):
        no_links(self.path)
        with closing(sqlite3.connect(self.path,timeout=0,isolation_level=None)) as c:
            c.execute('PRAGMA journal_mode=DELETE');c.execute('PRAGMA synchronous=FULL')
            c.execute('PRAGMA foreign_keys=ON')
            # Reserve twice database size for rollback journal plus metadata.
            page_size=c.execute('PRAGMA page_size').fetchone()[0]
            maximum=(self.quota-1024**2)//(3*page_size)
            actual=c.execute('PRAGMA max_page_count='+str(maximum)).fetchone()[0]
            if actual>maximum:raise RuntimeBlocked('Existing database exceeds storage budget')
            yield c
    def _verify(self,c,deep=False):
        row=c.execute('SELECT generation,body,state FROM publication').fetchone()
        last=c.execute('SELECT id,publication_hash,state_hash FROM generations ORDER BY id DESC LIMIT 1').fetchone()
        if (row is None)!=(last is None):raise RuntimeBlocked('Incomplete publication')
        if row and (row[0]!=last[0] or digest(json.loads(row[1]))!=last[1] or digest(json.loads(row[2]))!=last[2]):
            raise RuntimeBlocked('Publication digest mismatch')
        if not deep:return
        # Hash-chain metadata detects accidental edits; it is not an adversarial signature.
        prior='0'*64;count=0
        for g in c.execute('SELECT id,epoch,identity,publication_hash,state_hash,prior_hash,chain_hash,audit_id FROM generations ORDER BY id'):
            if g[0]!=count+1 or g[5]!=prior or digest(list(g[:6])+[g[7]])!=g[6]:raise RuntimeBlocked('Generation chain mismatch')
            prior=g[6];count+=1
        if c.execute('SELECT COUNT(*) FROM observations').fetchone()[0]!=count:raise RuntimeBlocked('Audit generation mismatch')
    def _state(self,c):
        self._verify(c)
        row=c.execute('SELECT state FROM publication').fetchone()
        result=json.loads(row[0]) if row else {'contracts':{},'previous':None}
        degraded=c.execute('SELECT degraded FROM runtime_control').fetchone()[0]
        if degraded and result.get('previous'):result['previous']['confirmation_valid']=False
        return result
    def state(self):
        self._owned()
        with self._connect() as c:return self._state(c)
    @single_writer
    def failure(self,kind):
        self._owned()
        if kind not in {'COLLECTION_FAILED','PUBLICATION_FAILED','SETTLEMENT_FAILED'}:raise RuntimeBlocked('Unknown failure category')
        try:
            self._capacity()
            with self._connect() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT INTO runtime_events(epoch,kind) VALUES (?,?)',(self.wall(),kind))
                c.execute('UPDATE runtime_control SET degraded=1,reason=?',(kind,));c.commit()
        except Exception:
            self._halt('FAILURE_RECORD_UNAVAILABLE');raise
    @single_writer
    def publish(self,copilot,snapshot,*,interrupt=lambda stage:None):
        self._owned();self._capacity()
        from btc_copilot import observation_digest, report
        s=copy.deepcopy(snapshot)
        if (s.get('decision')!='NO TRADE' or s.get('entry') is not None or
            s.get('position',{}).get('guidance_valid') is not False or
            not s.get('validation') or s['validation'].get('demonstrated_edge') is not False):
            raise RuntimeBlocked('Evidence-only output required')
        if copilot.positions:raise RuntimeBlocked('Private position collection forbidden')
        raw={k:v for k,v in copilot.raw.items() if k not in ('position','fills')}
        identity=digest({'snapshot':{k:v for k,v in s.items() if k not in ('scorecard','study')},
                         'raw':raw,'previous':copilot.prior,'settings':copilot.settings})
        def fresh():
            now=self.wall()
            if not finite(now) or not finite(s.get('epoch')) or not finite(s.get('valid_until_epoch')) or not s['epoch']<=now<s['valid_until_epoch']:
                raise RuntimeBlocked('Expired or future publication')
        try:
            fresh();check_clock(s)
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    self._verify(db)
                    last=db.execute('SELECT id,epoch,identity,chain_hash FROM generations ORDER BY id DESC LIMIT 1').fetchone()
                    if last and s['epoch']==last[1]:
                        if last[2]!=identity:raise RuntimeBlocked('Conflicting duplicate')
                        db.rollback();copilot.state=self.state();return last[0]
                    prior=self._state(db)
                    if copilot.state!=prior:raise RuntimeBlocked('Analysis did not use current durable state')
                    if last:
                        saved=json.loads(db.execute('SELECT body FROM publication').fetchone()[0])['snapshot']
                        check_clock(s,saved['epoch'],saved['observation_monotonic'])
                    staged=copilot._record_pending(s,copy.deepcopy(prior),observation_digest(s),persist=False)
                    self.audit.transaction=db;self.study.transaction=db
                    self.audit.record(s,raw,copilot.prior,copilot.settings,self.root/'unused-journal')
                    audit_id=db.execute('SELECT last_insert_rowid()').fetchone()[0]
                    interrupt('after_audit')
                    s['scorecard']=self.audit.scorecard()
                    self.study.record(s,raw,copilot.prior,copilot.settings)
                    interrupt('after_study')
                    s['study']=self.study.status(self.wall())
                    staged['previous']['confirmation_valid']=staged['previous']['data_confirmation_valid']
                    staged['last_observation'].update(logged=True,committed=True)
                    generation=(last[0]+1) if last else 1
                    body={'generation':generation,'snapshot':s,'text':report(s),'advice_enabled':False}
                    prior_hash=last[3] if last else '0'*64
                    chain=[generation,float(s['epoch']),identity,digest(body),digest(staged),prior_hash]
                    db.execute('INSERT INTO generations VALUES (?,?,?,?,?,?,?,?)',tuple(chain)+(digest(chain+[audit_id]),audit_id))
                    db.execute('INSERT OR REPLACE INTO publication VALUES (1,?,?,?)',(generation,canonical(body),canonical(staged)))
                    db.execute('UPDATE runtime_control SET degraded=0,reason=NULL')
                    interrupt('before_commit');fresh();self._capacity();db.commit()
                    interrupt('after_commit')
                    copilot.state=staged
                    return generation
                except BaseException:
                    if db.in_transaction:db.rollback()
                    raise
                finally:self.audit.transaction=None;self.study.transaction=None
        except Exception as error:
            if isinstance(error,(RuntimeBlocked,ValueError)) and ('clock' in str(error).lower() or 'reversed' in str(error).lower()):self._halt('CLOCK_DISCONTINUITY')
            else:self.failure('PUBLICATION_FAILED')
            raise
    @single_writer
    def settle_one(self,client,*,interrupt=lambda stage:None):
        """One public result per call; all durable effects follow the GET.

        Retry after a crash remains eligible. The frozen release cutoff and
        quarantine policy are unchanged. No report exposes holdout outcomes.
        """
        from study_policy import epoch
        self._owned();self._capacity();now=self.wall()
        if not finite(now):raise RuntimeBlocked('Invalid settlement clock')
        if now>=epoch(self.study.protocol['release_utc']):return False
        with self._connect() as db:
            row=db.execute('SELECT ticker,result FROM markets WHERE closed<? AND (result IS NULL OR settlement_attempt<?) ORDER BY settlement_attempt,closed LIMIT 1',
                           (now-60,now-self.study.protocol['outcome_policy']['recheck_seconds'])).fetchone()
        if not row:return False
        ticker,original=row
        started=time.monotonic()
        try:
            response=client.market(ticker)['data']['market']
            interrupt('after_get')
            ended=self.wall();elapsed=time.monotonic()-started
            if not finite(ended) or not 0<=ended-now<=20 or abs(ended-now-elapsed)>1 or ended>=epoch(self.study.protocol['release_utc']):
                self._halt('SETTLEMENT_CLOCK_DISCONTINUITY');raise RuntimeBlocked('Settlement clock discontinuity')
            if response.get('ticker')!=ticker:raise RuntimeBlocked('Settlement ticker mismatch')
            status=response.get('status');result=response.get('result')
            if not isinstance(status,str) or len(status)>32 or result not in (None,'','yes','no'):
                raise RuntimeBlocked('Malformed settlement result')
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    db.execute('INSERT INTO outcome_events VALUES (?,?,?,?)',(ticker,ended,status,str(result)))
                    db.execute('UPDATE markets SET settlement_attempt=? WHERE ticker=?',(ended,ticker))
                    final=status=='finalized' and result in ('yes','no')
                    if original is not None and (not final or int(result=='yes')!=original):
                        db.execute('UPDATE markets SET quarantined=1 WHERE ticker=?',(ticker,))
                    elif final:
                        db.execute('UPDATE markets SET result=COALESCE(result,?),result_at=COALESCE(result_at,?),confirmed_at=? WHERE ticker=?',(int(result=='yes'),ended,ended,ticker))
                        db.execute("UPDATE signals SET checked=1,status='missed_observation' WHERE ticker=? AND checked=0",(ticker,))
                    interrupt('before_settlement_commit');self._capacity();db.commit()
                    return True
                except BaseException:
                    if db.in_transaction:db.rollback()
                    raise
        except Exception:
            if not self.halted:self.failure('SETTLEMENT_FAILED')
            raise

    def latest(self):
        """A single envelope contains text and JSON for the same generation."""
        try:
            self._owned();self._capacity()
            with self._connect() as c:
                self._verify(c)
                degraded=c.execute('SELECT degraded,reason FROM runtime_control').fetchone()
                if degraded[0]:raise RuntimeBlocked(degraded[1])
                row=c.execute('SELECT body FROM publication').fetchone()
                if not row:raise RuntimeBlocked('No observation')
                body=json.loads(row[0]);s=body['snapshot'];now=self.wall()
                if not finite(now) or not s['epoch']<=now<s['valid_until_epoch']:raise RuntimeBlocked('Observation stale or clock reversed')
                return body
        except (OSError,ValueError,sqlite3.Error,KeyError,TypeError):
            return {'decision':'NO TRADE','status':'DATA UNAVAILABLE','advice_enabled':False,'snapshot':None,'text':'NO TRADE / DATA UNAVAILABLE'}
