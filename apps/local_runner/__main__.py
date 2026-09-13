"""`python -m apps.local_runner` 的入口。"""

from __future__ import annotations

import sys

from apps.local_runner.cli import main

if __name__ == "__main__":
    sys.exit(main())
