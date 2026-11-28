"""Reviewed historical conversation forks; no filesystem or external-effect rollback."""
from dataclasses import asdict
import hashlib
import json

from .session_forks import fork, require_idle, settled_shared_budget
from .sessions import validate_messages

NOTICE = ('Conversation rewind creates a child session at a completed turn. The original archive remains. '
          'Project files and reviewed project memory stay as they are; shell/network effects are not undone. '
          'Current settled spending and usage remain; no tools are replayed. '
          'The child clears summaries and its plan so later-turn claims cannot leak backward. '
          'Use /rewind for grouped file rollback or /undo for a file checkpoint; neither undoes shell/network effects.')


def state_hash(session, redactor):
    """Compare all saved semantic state; save-time updated timestamp is not content."""
    data = redactor.data(asdict(session))
    data.pop('updated', None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def completed_turns(messages):
    validate_messages(messages)
    starts = [index for index, row in enumerate(messages) if row['role'] == 'user']
    rows = []
    for number, start in enumerate(starts, 1):
        end = starts[number] if number < len(starts) else len(messages)
        final = messages[end-1]
        if final['role'] != 'assistant' or final.get('tool_calls'):
            continue
        # An assistant tool-call batch and its results are never a selectable turn end.
        validate_messages(messages[:end])
        content = messages[start].get('content')
        if isinstance(content, list):
            content = next((item.get('text','') for item in content if isinstance(item,dict) and item.get('type')=='text'), '')
        snippet = ' '.join(content.split())[:120] if isinstance(content,str) else '(non-text request)'
        rows.append({'turn': number, 'start': start, 'end': end, 'request': snippet})
    return rows


def rewind(argument, agent, workspace, ui):
    require_idle(agent, workspace)
    rows = completed_turns(agent.session.messages)
    if not argument:
        ui.write(NOTICE)
        ui.write('\n'.join(f"{row['turn']}: through message {row['end']} · {row['request']}" for row in rows[-50:]) or 'No completed user turns to select.')
        if len(rows)>50:ui.write('Showing the latest 50 completed turns; an earlier known turn number remains selectable.')
        ui.write('Usage: /rewind-context TURN_NUMBER (preview, then explicit approval)')
        return None
    if not argument.isascii() or not argument.isdigit() or len(argument)>8:
        raise ValueError('Use /rewind-context TURN_NUMBER from the completed-turn list.')
    selected = next((row for row in rows if row['turn']==int(argument)),None)
    if selected is None:raise ValueError('Select a completed user turn from /rewind-context.')
    with settled_shared_budget(agent):
        expected = state_hash(agent.session, agent.redactor)
    ui.write(NOTICE)
    ui.write(json.dumps({'parent':agent.session.id,'through_turn':selected['turn'],
        'retained_messages':selected['end'],'later_messages_excluded':len(agent.session.messages)-selected['end'],
        'request':selected['request'],'reviewed_state_sha256':expected},indent=2))
    if not ui.approve('Create and switch to this historical conversation child? Files and external effects remain unchanged.'):
        ui.write('Conversation rewind declined; active session unchanged.')
        return None
    return fork(agent, workspace, f"Rewind to turn {selected['turn']} of {agent.session.id}"[:120],
                end=selected['end'], expected_state=expected)
