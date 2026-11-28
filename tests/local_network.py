"""Register only the exact bound local fixture with the optional CI guard."""
from contextlib import nullcontext
import sys


def allow_loopback(address):
    guard = sys.modules.get('sitecustomize')
    allow = getattr(guard, 'allow_loopback', None)
    return allow(address) if allow else nullcontext()
