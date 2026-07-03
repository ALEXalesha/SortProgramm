"""Сбор файлов для сортировки. В чужие папки не заходит."""
from __future__ import annotations

import fnmatch
from pathlib import Path

from .config import Config


def _is_ignored(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)


def scan(root: str | Path, config: Config, deep: bool = True) -> list[Path]:
    """Файлы для сортировки из корня папки.

    deep=True  — как раньше: файлы корня + рекурсивно из управляемых папок
                 (config.managed_folders). Используется в режиме ИИ.
    deep=False — только файлы, лежащие прямо в корне; ни в какие подпапки
                 не заходим. Используется в обычной сортировке без ИИ и при
                 разборе внешней папки All_3d.

    Папки, которых нет в config.managed_folders, не обходятся никогда — это
    чужие папки программ и игр.
    """
    root = Path(root)
    found: list[Path] = []
    if not root.is_dir():
        return found

    for entry in sorted(root.iterdir()):
        if entry.is_file():
            if not _is_ignored(entry.name, config.ignore):
                found.append(entry)
        elif deep and entry.is_dir() and entry.name in config.managed_folders:
            found.extend(_walk_managed(entry, config))

    return found


def _walk_managed(folder: Path, config: Config) -> list[Path]:
    files: list[Path] = []
    for entry in sorted(folder.rglob("*")):
        if entry.is_file() and not _is_ignored(entry.name, config.ignore):
            files.append(entry)
    return files
