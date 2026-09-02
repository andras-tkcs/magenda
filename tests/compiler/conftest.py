"""Puts scripts/ on sys.path so tests/compiler/* can `from compiler import
xml_ops` etc. -- scripts/compiler/ is a plain directory (not part of the
installed magenda package; see docs/design/remove-libreoffice-runtime-
dependency.md), the same way scripts/compile_template.py itself reaches it.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
