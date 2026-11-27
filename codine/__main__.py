"""Support python -m codine in checkouts and installed environments."""
from .cli import main

if __name__ == '__main__':
    raise SystemExit(main())
