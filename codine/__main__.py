"""Support python -m codine in checkouts and installed environments."""
from .launcher import main

if __name__ == '__main__':
    raise SystemExit(main())
