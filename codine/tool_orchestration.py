"""Sequential external-call bounds over the Agent's existing durable journal."""
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class ExternalLimits:
    per_batch: int = 4
    per_turn: int = 12

    def __post_init__(self):
        if (type(self.per_batch) is not int or type(self.per_turn) is not int or
                not 1 <= self.per_batch <= 16 or not self.per_batch <= self.per_turn <= 64):
            raise ValueError('External limits require 1–16 per batch and batch–64 per turn.')


class ExternalCalls:
    def __init__(self, limits=None):
        self.limits = limits or ExternalLimits()
        self.total = 0
        self.begin_batch()

    def begin_batch(self):
        self.batch = 0
        self.stopped = None

    @property
    def exhausted(self): return self.total >= self.limits.per_turn

    def before(self, external):
        if self.stopped: return self.skip(self.stopped)
        if not external: return None
        if self.exhausted:
            self.stopped = 'External tool turn limit reached; no further batch action started.'
        elif self.batch >= self.limits.per_batch:
            self.stopped = 'External tool batch limit reached; reconsider skipped actions in a future step.'
        if self.stopped: return self.skip(self.stopped)
        self.batch += 1
        self.total += 1
        return None

    def skip(self, reason):
        return json.dumps({'status':'skipped','action_executed':False,'reason':reason,
                           'external_attempts_this_turn':self.total})

    def observe(self, external, result):
        if not external or len(result.encode()) > 2_000_000: return
        try: value = json.loads(result)
        except (ValueError, RecursionError): return
        if not isinstance(value, dict): return
        failed = ('error' in value and value['error'] not in (None, False, '')) or value.get('status') in ('declined','failed','error','blocked')
        mcp = value.get('untrusted_server_result')
        if isinstance(mcp, dict) and mcp.get('isError') is True: failed = True
        if failed:
            self.stopped = 'A prior external tool reported failure or declined approval; remaining batch actions were not started. Inspect its saved result before replanning.'
