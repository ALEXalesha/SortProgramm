"""История перемещений: чтение логов сортировок из `.sorter/undo_*.json`.

Каждое применение плана `mover.apply` пишет лог отмены `undo_YYYYMMDD_HHMMSS.json`
со списком `{src, dst}`. Здесь эти логи читаются как история операций и, при
желании, откатываются через `mover.undo`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .mover import undo as _undo

_PREFIX = "undo_"
_SUFFIX = ".json"
_STAMP_FMT = "%Y%m%d_%H%M%S"


@dataclass
class Operation:
    """Одна выполненная сортировка (одна запись в истории)."""
    log_path: Path
    when: datetime
    entries: list[dict[str, str]]

    @property
    def count(self) -> int:
        return len(self.entries)


def _parse_stamp(name: str) -> datetime | None:
    if not (name.startswith(_PREFIX) and name.endswith(_SUFFIX)):
        return None
    stamp = name[len(_PREFIX):-len(_SUFFIX)]
    try:
        return datetime.strptime(stamp, _STAMP_FMT)
    except ValueError:
        return None


def list_operations(downloads_path: str | Path) -> list[Operation]:
    """Возвращает историю сортировок, самые свежие — первыми.

    Битые/чужие файлы в `.sorter` тихо пропускаются, чтобы история не падала.
    """
    log_dir = Path(downloads_path) / ".sorter"
    if not log_dir.is_dir():
        return []

    ops: list[Operation] = []
    for path in log_dir.glob(f"{_PREFIX}*{_SUFFIX}"):
        when = _parse_stamp(path.name)
        if when is None:
            continue
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(entries, list):
            continue
        ops.append(Operation(path, when, entries))

    ops.sort(key=lambda op: op.when, reverse=True)
    return ops


def undo_operation(op: Operation) -> None:
    """Откатывает операцию (возвращает файлы на места) и удаляет её лог из истории."""
    _undo(op.log_path)
    try:
        op.log_path.unlink()
    except OSError:
        pass
