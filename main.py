"""Codine entry point. Read PRD.md for the architecture and learning guide."""
from codine.launcher import main
from codine.workspace import preview_change  # Keep the original learning exercise importable.

if __name__ == '__main__':
    raise SystemExit(main())
