"""Retry only connection establishment on owned, non-redirecting SDK clients."""
import time
import ssl
import httpx2 as httpx
from openai import APIConnectionError


def certificate_failure(error):
    seen, pending = set(), [error]
    while pending and len(seen) < 16:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, ssl.SSLCertVerificationError):
            return True
        pending.extend(value for value in (getattr(item, '__cause__', None),
                       getattr(item, '__context__', None)) if value is not None)
    return False


def create_stream(client, request, emit, *, owned=False):
    # SDK-wide retries stay disabled: they can repeat requests after transmission.
    # Only our known client configuration (no redirects, max_retries=0) qualifies.
    attempts = 3 if owned else 1
    for attempt in range(attempts):
        try:
            return client.chat.completions.create(**request)
        except APIConnectionError as error:
            cause = error.__cause__
            if (not isinstance(cause, (httpx.ConnectError, httpx.ConnectTimeout)) or
                    certificate_failure(error) or attempt + 1 == attempts):
                raise
            # A terminal stop raised by emit prevents another connection attempt.
            delay = (0.25, 0.5)[attempt]
            emit('status', f'Connection setup failed before sending; retry {attempt + 1}/2 in {delay:g}s.')
            time.sleep(delay)
            emit('status', 'Checking cancellation before the next connection attempt.')
