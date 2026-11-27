"""Select complete recent user turns without deleting the session archive."""
import json
from .sessions import validate_messages


class ContextLimitError(ValueError):
    pass


def user_boundaries(session):
    return [index for index, message in enumerate(session.messages)
            if index >= session.context_start and message['role'] == 'user']


def request_message(message):
    """Keep provider reasoning details intact, omit only their exact text duplicate."""
    projected = dict(message)
    details = projected.get('reasoning_details')
    reasoning = projected.get('reasoning')
    if (isinstance(reasoning, str) and isinstance(details, list) and details and
            all(isinstance(item, dict) and item.get('type') == 'reasoning.text' and
                isinstance(item.get('text'), str) for item in details) and
            ''.join(item['text'] for item in details) == reasoning):
        projected.pop('reasoning')
    return projected


def project_context(session, schemas, budget, extra_system=""):
    boundaries = user_boundaries(session)
    start = boundaries[0] if boundaries else len(session.messages)
    fixed = len(json.dumps(schemas, ensure_ascii=False))
    while True:
        messages = [dict(session.messages[0])] + [request_message(message) for message in session.messages[start:]]
        messages[0]['content'] += extra_system
        if start > 1:
            messages[0]['content'] += '\nOlder conversation turns are omitted from this request. Do not invent their contents; the full archive remains in the local session.'
        if fixed + len(json.dumps(messages, ensure_ascii=False)) <= budget:
            validate_messages(messages)
            return messages, start
        if len(boundaries) <= 1:
            raise ContextLimitError('The current turn exceeds the context character budget. Use /clear or start a smaller task; do not retry the same oversized request.')
        boundaries.pop(0)
        start = boundaries[0]


def compact(session, keep=3):
    boundaries = user_boundaries(session)
    if len(boundaries) <= keep:
        return False
    session.context_start = boundaries[-keep]
    return True
