from __future__ import annotations

from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from booktrans.architecture_explorer.cli import main  # noqa: E402


raise SystemExit(main())
