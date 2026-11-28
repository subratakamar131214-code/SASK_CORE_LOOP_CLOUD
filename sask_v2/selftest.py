"""Run the SASK v2.0 test suite: ``python -m sask_v2.selftest``."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    suite = unittest.TestLoader().discover(str(root / "tests_v2"), top_level_dir=str(root))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
