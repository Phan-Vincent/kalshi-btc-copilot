"""Shared process-local Kalshi read pacing; no network or filesystem access."""
import contextvars
import math
import threading
import time
from contextlib import contextmanager

BACKGROUND=contextvars.ContextVar('background_read',default=False)

class PaceError(RuntimeError):pass

class Pacer:
    def __init__(self,clock=time.monotonic,sleep=time.sleep):
        self.clock=clock;self.sleep=sleep;self.lock=threading.Lock()
        self.updated=clock();self.tokens=0.;self.background_tokens=0.
        self.cooldown_until=0.;self.strikes=0
    def _refill(self,now):
        if not math.isfinite(now) or now<self.updated:raise PaceError('Pacing clock invalid')
        elapsed=now-self.updated;self.updated=now
        self.tokens=min(60.,self.tokens+60.*elapsed)
        self.background_tokens=min(10.,self.background_tokens+20.*elapsed)
    def try_acquire(self,cost,background=False):
        if type(cost) not in (int,float) or not math.isfinite(cost) or not 0<cost<=60:raise PaceError('Invalid request token cost')
        if background and cost>10:raise PaceError('Background route exceeds approved cost')
        with self.lock:
            now=self.clock();self._refill(now)
            wait=max(0.,self.cooldown_until-now,(cost-self.tokens)/60.)
            if background:wait=max(wait,(cost-self.background_tokens)/20.)
            if wait>0:return wait
            self.tokens-=cost
            if background:self.background_tokens-=cost
            return 0.
    def acquire(self,cost,background=False,max_wait=5.):
        if type(max_wait) not in (int,float) or not math.isfinite(max_wait) or not 0<=max_wait<=5:raise PaceError('Invalid pacing deadline')
        deadline=self.clock()+max_wait
        while True:
            if self.clock()>deadline:raise PaceError('Read pacing deadline exceeded')
            wait=self.try_acquire(cost,background)
            if not wait:return
            remaining=deadline-self.clock()
            if wait>remaining or remaining<=0:raise PaceError('Read pacing deadline exceeded')
            # No reservations: canceled/expired requests cannot build a dispatch burst.
            self.sleep(min(wait,.1))
    def throttled(self):
        with self.lock:
            now=self.clock();self._refill(now)
            self.strikes=min(self.strikes+1,5)
            self.cooldown_until=max(self.cooldown_until,now+min(30.,2.**self.strikes))
            self.tokens=0.;self.background_tokens=0.
    # No automatic retry and no success reset: repeated 429s stay conservative.

GLOBAL_PACER=Pacer()

def cost_for(path):
    return 50 if path.startswith('/cfbenchmarks/') else 10

@contextmanager
def background_reads():
    token=BACKGROUND.set(True)
    try:yield
    finally:BACKGROUND.reset(token)

def before_read(path):GLOBAL_PACER.acquire(cost_for(path),BACKGROUND.get())
def on_throttled():GLOBAL_PACER.throttled()
