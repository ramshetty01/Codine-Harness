"""Reported cache measurements, without inferred savings or inferred missing zeros."""
from .usage import field


def measurement(usage):
    prompt=field(usage,('prompt_tokens',))
    read=field(usage,('prompt_tokens_details','cached_tokens'))
    write=field(usage,('prompt_tokens_details','cache_write_tokens'))
    valid=prompt is not None and read is not None and read<=prompt
    return {'input_tokens':prompt,'cache_read_tokens':read,'cache_write_tokens':write,
            'cache_hit':None if read is None else read>0,
            'cache_read_fraction':read/prompt if valid and prompt else None,
            'cache_counts_consistent':None if prompt is None or read is None else valid}


def describe(state):
    if not state:return 'No provider cache measurements recorded. Missing counts are unknown, not zero.'
    lines=['Provider-reported prompt cache usage (no inferred dollar savings)']
    for name in ('cache_read','cache_write'):
        coverage=state['reported'].get(name,0)
        total=state['totals'].get(name) if coverage else None
        lines.append(f"{name}: last {state['last'].get(name) if state['last'].get(name) is not None else 'unknown'}; sum {total if total is not None else 'unknown'}; coverage {coverage}/{state['requests']} completed responses")
    lines.append('Counts do not prove savings or latency improvement. Cache pricing, minimum sizes and expiration depend on the upstream provider; /budget remains authoritative.')
    return '\n'.join(lines)
