"""Provider-reported token accounting; omitted fields never become measured zero."""
FIELDS = {
    'input': ('prompt_tokens',),
    'output': ('completion_tokens',),
    'total': ('total_tokens',),
    'cache_read': ('prompt_tokens_details', 'cached_tokens'),
    'reasoning': ('completion_tokens_details', 'reasoning_tokens'),
}


def field(value, path):
    for key in path:
        value = value.get(key) if isinstance(value, dict) else getattr(value, key, None)
    return value if type(value) is int and value >= 0 else None


def record(state, usage):
    if not state:
        state.update({'requests': 0, 'last': {}, 'totals': {}, 'reported': {}})
    state['requests'] += 1
    state['last'] = {name: field(usage, path) for name, path in FIELDS.items()}
    for name, value in state['last'].items():
        state['totals'].setdefault(name, 0)
        state['reported'].setdefault(name, 0)
        if value is not None:
            state['totals'][name] += value
            state['reported'][name] += 1


def validate(state):
    if state == {}:
        return
    if not isinstance(state, dict) or set(state) != {'requests', 'last', 'totals', 'reported'}:
        raise ValueError('Invalid detailed usage.')
    count = state['requests']
    if type(count) is not int or count < 1:
        raise ValueError('Invalid usage request count.')
    for section in ('last', 'totals', 'reported'):
        values = state[section]
        if not isinstance(values, dict) or set(values) != set(FIELDS):
            raise ValueError('Invalid detailed usage fields.')
        for value in values.values():
            if section == 'last' and value is None:
                continue
            if type(value) is not int or value < 0 or (section == 'reported' and value > count):
                raise ValueError('Invalid detailed usage count.')


def describe(state):
    if not state:
        return 'No detailed provider usage recorded. Older session counters have unknown coverage.'
    count = state['requests']
    lines = [f'Provider usage · {count} completed responses tracked since detailed accounting began']
    for name in FIELDS:
        last = state['last'][name]
        coverage = state['reported'][name]
        total = str(state['totals'][name]) if coverage else 'unknown'
        lines.append(f'  {name}: last {last if last is not None else "unknown"}; reported sum {total} ({coverage}/{count} responses)')
    lines.append('Cache-read tokens are an input subset; reasoning tokens are an output subset. Do not add them again.')
    lines.append('Missing counts remain unknown. Interrupted or rejected responses are not inferred. /budget shows authoritative cost state.')
    return '\n'.join(lines)
