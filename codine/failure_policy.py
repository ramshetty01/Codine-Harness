"""Sanitized provider failure advice and bounded retries for metadata reads only."""
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
import math
import random
import time
import urllib.error

from openai import APIConnectionError, APITimeoutError

from .connection_retry import certificate_failure


@dataclass(frozen=True)
class FailureInfo:
    category: str
    transient: bool
    retry_safe: bool
    retry_after_seconds: float | None
    message: str
    action: str

    def as_dict(self):
        return asdict(self)


def retry_after(error, now=None):
    """Parse hints without exposing headers or allowing an unbounded delay."""
    response = getattr(error, 'response', None)
    headers = getattr(response, 'headers', None)
    if headers is None:
        headers = getattr(error, 'headers', None)
    if headers is None:
        return None
    value = headers.get('Retry-After')
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = date.timestamp() - (time.time() if now is None else now)
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    # Values above the retry delay cap cause retries to stop, never an early retry.
    return min(seconds, 86400.0)


def classify_failure(error, *, operation='completion', now=None):
    if operation not in ('completion', 'metadata'):
        raise ValueError('Unknown provider operation.')
    status = getattr(error, 'status_code', None)
    if isinstance(error, urllib.error.HTTPError):
        status = error.code
    transient = False
    code = getattr(error, 'code', None)
    if code in ('context_length_exceeded', 'context_window_exceeded', 'max_tokens_exceeded'):
        category, message = 'context_limit', 'Provider rejected the context or output-token limit.'
        action = 'Inspect /context and model limits; reduce context or output reservation before a deliberate retry.'
    elif status == 401:
        category, message = 'authentication', 'Provider rejected authentication (HTTP 401).'
        action = 'Check the selected provider key; shell variables override .env.'
    elif status == 403:
        category, message = 'permission', 'Provider denied access (HTTP 403).'
        action = 'Check model/provider access permissions and account restrictions.'
    elif status == 404:
        category, message = 'not_found', 'Provider endpoint or model was not found (HTTP 404).'
        action = 'Check the configured endpoint and exact model ID.'
    elif status == 402 or code in ('insufficient_quota', 'billing_hard_limit_reached'):
        category, message = 'quota', 'Provider account quota or billing limit was reached.'
        action = 'Inspect account limits and available balance; waiting alone may not resolve this.'
    elif status == 413:
        category, message = 'request_size', 'Provider rejected the request size (HTTP 413).'
        action = 'Reduce context or output limits before a deliberate retry.'
    elif status == 429:
        category, transient = 'rate_limit', True
        message = 'Provider rate limit reached (HTTP 429).'
        action = 'Wait for the provider limit to reset; inspect account quota and request size.'
    elif status in (408, 409, 425) or (isinstance(status, int) and 500 <= status <= 599):
        category, transient = 'provider_unavailable', True
        message = f'Provider temporarily unavailable (HTTP {status}).'
        action = 'Wait and check provider availability.'
    elif isinstance(error, (APITimeoutError, TimeoutError)):
        category, transient = 'timeout', True
        message = 'Provider operation timed out.'
        action = 'Check connectivity and provider activity; a timeout does not prove the request was uncharged.'
    elif certificate_failure(error) or (
            isinstance(error, urllib.error.URLError) and certificate_failure(error.reason)):
        category, message = 'tls_configuration', 'Provider TLS certificate verification failed.'
        action = 'Fix the local certificate configuration; do not disable TLS verification.'
    elif isinstance(error, (APIConnectionError, urllib.error.URLError, ConnectionError)):
        category, transient = 'connection', True
        message = 'Provider connection failed or was interrupted.'
        action = 'Check connectivity; the remote operation may have completed even when its response was lost.'
    elif isinstance(status, int) and 400 <= status <= 499:
        category, message = 'request_rejected', f'Provider rejected the request (HTTP {status}).'
        action = 'Check request parameters, model capabilities and account settings.'
    else:
        category, message = 'provider_failure', 'Provider operation failed without a classified response.'
        action = 'Inspect provider activity and local configuration before a deliberate retry.'
    safe = operation == 'metadata' and transient
    if operation == 'completion':
        action += (' No automatic completion retry. If a charge reservation is pending, check provider activity '
                   'and use /budget for its request ID, then /budget reconcile REQUEST_ID AMOUNT before continuing.')
    return FailureInfo(category, transient, safe, retry_after(error, now), message, action)


def retry_metadata_read(read, *, attempts=3, per_attempt_timeout=5.0, total_timeout=20.0,
                        base_delay=0.5, max_delay=2.0, sleep=time.sleep,
                        clock=time.monotonic, wall_clock=time.time, jitter=random.random):
    """read(timeout) must perform only an idempotent, non-inference metadata GET.

    The deadline bounds scheduling and backoff, not arbitrary caller execution.
    The HTTP reader must also enforce its socket/read deadline and response cap.
    """
    if (type(attempts) is not int or not 1 <= attempts <= 5 or
            any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                for value in (per_attempt_timeout, total_timeout, base_delay, max_delay)) or
            per_attempt_timeout > 20 or total_timeout > 60 or max_delay > 10):
        raise ValueError('Invalid bounded metadata retry policy.')
    deadline = clock() + total_timeout
    for attempt in range(attempts):
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError('Metadata retry deadline exceeded.')
        try:
            return read(min(per_attempt_timeout, remaining))
        except Exception as error:
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            info = classify_failure(error, operation='metadata', now=wall_clock())
            if not info.retry_safe or attempt + 1 == attempts:
                raise
            random_part = jitter()
            if not isinstance(random_part, (int, float)) or not 0 <= random_part <= 1:
                raise ValueError('Invalid retry jitter.') from None
            delay = min(max_delay, base_delay * (2 ** attempt)) * (0.75 + 0.25 * random_part)
            if info.retry_after_seconds is not None:
                delay = max(delay, info.retry_after_seconds)
            # A long server hint stops this operation instead of retrying before the hint.
            if delay > max_delay or clock() + delay >= deadline:
                raise
            sleep(delay)
