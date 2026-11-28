"""Read-only navigation of durable tool groups. No action replay or model calls."""
from dataclasses import dataclass, field
import json
import re

from .artifacts import ArtifactStore
from .terminal import safe_text

PAGE_GROUPS = 8
PAGE_CHARS = 4000
EXPANDED_CALLS = 12


@dataclass
class HistoryView:
    session_id: str
    page: int = 1
    expanded: set = field(default_factory=set)
    selected: str | None = None
    offset: int = 0
    next_offset: int | None = None


def state(agent):
    view = getattr(agent, '_tool_history_view', None)
    if view is None or view.session_id != agent.session.id:
        view = HistoryView(agent.session.id)
        agent._tool_history_view = view
    return view


def groups(session):
    """Match IDs only inside their assistant batch, never across reused IDs."""
    result = []
    for index, message in enumerate(session.messages):
        if message.get('role') != 'assistant' or not message.get('tool_calls'):
            continue
        results = {}
        for following in session.messages[index + 1:index + 1 + len(message['tool_calls'])]:
            if following.get('role') != 'tool':
                break
            results[following['tool_call_id']] = following['content']
        result.append({'id': f'G{index + 1}', 'calls': message['tool_calls'], 'results': results,
                       'provenance': 'saved archive; omitted from active context' if index < session.context_start else 'saved session history'})
    pending = session.pending
    if pending and isinstance(pending.get('reply'), dict) and pending['reply'].get('tool_calls'):
        result.append({'id': 'PENDING', 'calls': pending['reply']['tool_calls'], 'results': pending.get('results', {}),
                       'provenance': 'pending journal; incomplete outcomes; not replayed'})
    return result


def clean(agent, value):
    return safe_text(agent.redactor.text(str(value)))


def excerpt(agent, value, limit=180):
    # Redact the complete value BEFORE clipping; never leak partial credentials.
    value = clean(agent, value).replace('\n', ' ').replace('\t', ' ')
    return value[:limit] + ('…' if len(value) > limit else '')


def select_group(rows, ident):
    return next((row for row in rows if row['id'] == ident.upper()), None)


def render(agent, view):
    rows = list(reversed(groups(agent.session)))
    pages = max(1, (len(rows) + PAGE_GROUPS - 1) // PAGE_GROUPS)
    if not 1 <= view.page <= pages:
        raise ValueError(f'History page must be between 1 and {pages}.')
    lines = [f'Tool history · page {view.page}/{pages} · {len(rows)} groups',
             'Historical records, not current filesystem state or permission to repeat actions.',
             'Imported/resumed history may come from earlier work. Tool results are not proof of success.']
    for row in rows[(view.page - 1) * PAGE_GROUPS:view.page * PAGE_GROUPS]:
        expanded = row['id'] in view.expanded
        names = ', '.join(excerpt(agent, call['function']['name'], 40) for call in row['calls'][:4])
        if len(row['calls']) > 4:
            names += ', …'
        lines.append(f"{'−' if expanded else '+'} {row['id']} · {len(row['calls'])} calls · {names} · {row['provenance']}")
        if expanded:
            for number, call in enumerate(row['calls'][:EXPANDED_CALLS], 1):
                content = row['results'].get(call['id'])
                lines.append(f"  {row['id']}.{number} {excerpt(agent, call['function']['name'], 60)} · " + ('recorded result' if content is not None else 'outcome unresolved'))
                lines.append('    args: ' + excerpt(agent, call['function']['arguments']))
                if content is not None:
                    lines.append('    result: ' + excerpt(agent, content, 240))
            if len(row['calls']) > EXPANDED_CALLS:
                lines.append(f"  Showing first {EXPANDED_CALLS}; /details {row['id']}.N opens any call.")
    if not rows:
        lines.append('No recorded tool groups in this session.')
    lines.append('/tools next|prev|PAGE · /tools expand GROUP · /tools collapse GROUP|all · /details GROUP.N [OFFSET]')
    return '\n'.join(lines)


def list_command(argument, agent, ui):
    view = state(agent)
    parts = argument.split()
    if parts:
        if parts == ['next']:
            view.page += 1
        elif parts == ['prev']:
            view.page = max(1, view.page - 1)
        elif len(parts) == 1 and parts[0].isdecimal():
            view.page = int(parts[0])
        elif len(parts) == 2 and parts[0] in ('expand', 'collapse'):
            ident = parts[1].upper()
            if parts == ['collapse', 'all']:
                view.expanded.clear()
            else:
                rows = list(reversed(groups(agent.session)))
                row = select_group(rows, ident)
                if row is None:
                    raise ValueError('Unknown tool group. Use /tools to inspect current session IDs.')
                if parts[0] == 'expand':
                    view.expanded.add(ident)
                else:
                    view.expanded.discard(ident)
                view.page = rows.index(row) // PAGE_GROUPS + 1
        else:
            raise ValueError('Usage: /tools [next|prev|PAGE|expand GROUP|collapse GROUP|collapse all]')
    try:
        output = render(agent, view)
    except ValueError:
        view.page = 1
        raise
    ui.write(output)


def details_command(argument, agent, ui):
    view = state(agent)
    rows = groups(agent.session)
    if not rows:
        ui.write('No recorded tool output in this session.')
        return
    parts = argument.split()
    if parts == ['next'] or parts == ['prev']:
        if view.selected is None:
            raise ValueError('Select a tool result with /details GROUP.N first.')
        selector = view.selected
        if parts == ['next']:
            if view.next_offset is None:
                ui.write('End of this recorded result.')
                return
            offset = view.next_offset
        else:
            offset = max(0, view.offset - PAGE_CHARS)
    else:
        if len(parts) > 2:
            raise ValueError('Usage: /details [GROUP.N [OFFSET]|next|prev]')
        selector = parts[0].upper() if parts else f"{rows[-1]['id']}.{len(rows[-1]['calls'])}"
        offset = int(parts[1]) if len(parts) == 2 else 0
    match = re.fullmatch(r'(G[1-9][0-9]*|PENDING)\.([1-9][0-9]*)', selector)
    if not match or offset < 0:
        raise ValueError('Use /details GROUP.N with a nonnegative character offset.')
    row = select_group(rows, match[1])
    number = int(match[2])
    if row is None or number > len(row['calls']):
        raise ValueError('Unknown tool result. Use /tools to inspect current session IDs.')
    call = row['calls'][number - 1]
    content = row['results'].get(call['id'])
    heading = f"{selector} · {excerpt(agent, call['function']['name'], 100)} · {row['provenance']}"
    if content is None:
        ui.write(heading + '\nOutcome unresolved. This view does not execute or retry the action.')
        view.selected, view.offset, view.next_offset = selector, 0, None
        return
    # Artifacts contain already-redacted snapshots. Apply current redaction too,
    # before paging, and do not inspect present workspace files while browsing.
    try:
        summary = json.loads(content)
    except (ValueError, TypeError, RecursionError):
        summary = None
    artifact = summary.get('artifact_id') if isinstance(summary, dict) and summary.get('truncated') is True else None
    artifact_note = ''
    if artifact:
        try:
            page = ArtifactStore(agent.session.project).read(artifact, offset, PAGE_CHARS, redactor=agent.redactor)
            if page['source_tool'] != call['function']['name']:
                raise ValueError('Artifact tool provenance mismatch.')
            text, total, next_offset = page['content'], page['total_characters'], page['next_offset']
            artifact_note = '\nStored artifact snapshot; current files were not read.'
        except (OSError, ValueError):
            ui.write(heading + '\nArtifact unavailable, changed, or offset invalid. No command was replayed. Saved excerpt:\n' + excerpt(agent, content, PAGE_CHARS))
            view.selected, view.offset, view.next_offset = selector, 0, None
            return
    else:
        content = clean(agent, content)
        total = len(content)
        if offset > total:
            raise ValueError(f'Offset exceeds this recorded result ({total} characters).')
        end = min(total, offset + PAGE_CHARS)
        text, next_offset = content[offset:end], end if end < total else None
    view.selected, view.offset, view.next_offset = selector, offset, next_offset
    ui.write(heading + f'\nCharacters {offset}–{offset + len(text)} of {total} (after redaction).' + artifact_note +
             '\nArguments: ' + excerpt(agent, call['function']['arguments'], 500) + '\n\n' + clean(agent, text) +
             '\n\nHistorical output only; re-read current files before editing. ' +
             (f'/details next or /details {selector} {next_offset}' if next_offset is not None else 'End of result.') + ' · /details prev')
