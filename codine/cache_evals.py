"""Offline repeated-prompt measurement harness; no live provider construction."""
import argparse
import hashlib
import json
import time
from .cache_metrics import measurement


def measure(complete, messages, schemas, model, *, repeats=2, clock=time.monotonic):
    if type(repeats) is not int or not 2<=repeats<=10:raise ValueError('Repeat count must be 2–10.')
    raw=json.dumps([messages,schemas,model],sort_keys=True,allow_nan=False)
    if len(raw.encode())>30000:raise ValueError('Repeat fixture exceeds 30 KB.')
    rows=[]
    for index in range(repeats):
        request=json.loads(raw);start=clock()
        # Exceptions intentionally abort: no retry, fallback or remaining repeats.
        reply,finish,usage=complete(*request,lambda *args:None)
        seconds=clock()-start
        if finish not in ('stop','length') or reply.get('tool_calls'):
            raise ValueError('Repeat measurement accepts text completions only; no actions execute.')
        rows.append({'repeat':index+1,'seconds':seconds,**measurement(usage)})
    return {'version':1,'request_sha256':hashlib.sha256(raw.encode()).hexdigest(),
            'results':rows,'notice':'Repeated identical inputs, not proof of cache causation or dollar savings. Caller owns all spending guards.'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline',action='store_true',required=True)
    args=parser.parse_args(argv)
    fixtures=iter([{'prompt_tokens':1024,'prompt_tokens_details':{'cached_tokens':0,'cache_write_tokens':1024}},
                   {'prompt_tokens':1024,'prompt_tokens_details':{'cached_tokens':1024,'cache_write_tokens':0}}])
    def fake(messages,schemas,model,emit):return {'role':'assistant','content':'fixture'},'stop',next(fixtures)
    report=measure(fake,[{'role':'user','content':'synthetic repeat fixture'}],[],'offline-fixture')
    report['mode']='offline synthetic values; not an observed provider benchmark'
    print(json.dumps(report,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
