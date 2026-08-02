"""Сбор файлов для сортировки. В чужие папки не заходит."""
from __future__ import annotations

import fnmatch
from pathlib import Path

from .config import Config


def _is_ignored(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)


def scan(root: str | Path, config: Config, deep: bool = True) -> list[Path]:
    """Файлы для сортировки из корня папки.

    deep=True  — файлы корня + рекурсивно из папок, которые программа создала
                 сама (config.managed_folders). Режим переразложения: старые
                 загрузки проверяются заново по текущим правилам.
    deep=False — только файлы, лежащие прямо в корне; ни в какие подпапки
                 не заходим. Обычная уборка и разбор внешней папки All_3d.

    Папки, которых нет в config.managed_folders, не обходятся никогда — это
    чужие папки программ и игр. Их программа не двигает и не разбирает.
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
    """Все файлы внутри управляемой папки, рекурсивно.

    Сюда попадают только папки из `managed_folders` — те, что программа создала
    сама. Чужие папки (распакованные архивы, миры игр, репозитории) в этот
    обход не приходят: их отсеивает `scan`.
    """
    files: list[Path] = []
    stack = [folder]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                stack.append(entry)
            elif not _is_ignored(entry.name, config.ignore):
                files.append(entry)
    return sorted(files)
