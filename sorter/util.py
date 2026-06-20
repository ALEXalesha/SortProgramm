"""Мелкие общие помощники."""
from __future__ import annotations

from pathlib import Path


def rel_to(path: Path, root: Path) -> str:
    """Путь относительно корня; абсолютный, если он вне корня (напр. All_3d)."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
