"""Select complete recent user turns without deleting the session archive."""
import json
from .sessions import validate_messages


class ContextLimitError(ValueError):
    pass


def user_boundaries(session):
    return [index for index, message in enumerate(session.messages)
            if index >= session.context_start and message['role'] == 'user']


def project_context(session, schemas, budget, extra_system=""):
    boundaries = user_boundaries(session)
    start = boundaries[0] if boundaries else len(session.messages)
    fixed = len(json.dumps(schemas)) + len(json.dumps(session.messages[0]))
    while True:
        messages = [dict(session.messages[0])] + session.messages[start:]
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
