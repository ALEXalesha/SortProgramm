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
    """Файлы внутри управляемой папки, кроме корзины целых папок.

    В `folder_bucket` (`_Папки`) лежат перенесённые целиком папки: мир Minecraft,
    git-репозиторий, мод. Если войти туда и разложить их содержимое по типам,
    папка перестанет существовать как единица — `level.dat` уедет в Misc,
    исходники в Code, и восстановить это можно будет только вручную.

    Поэтому обход ручной, со стеком: `rglob` не умеет отсекать поддеревья.
    """
    bucket = config.folder_bucket
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
                if entry.name != bucket:
                    stack.append(entry)
            elif not _is_ignored(entry.name, config.ignore):
                files.append(entry)
    return sorted(files)
