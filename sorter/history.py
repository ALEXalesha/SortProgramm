"""История перемещений: чтение логов сортировок из `.sorter/undo_*.json`.

Каждое применение плана `mover.apply` пишет лог отмены `undo_YYYYMMDD_HHMMSS.json`
со списком `{src, dst}`. Здесь эти логи читаются как история операций и, при
желании, откатываются через `mover.undo`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .mover import entries_of, undo as _undo
from .util import read_text

_PREFIX = "undo_"
_SUFFIX = ".json"
_STAMP_FMT = "%Y%m%d_%H%M%S"

# `undo_20260802_193157.json` и `undo_20260802_193157_2.json`: номер приписывает
# `mover`, когда в одну секунду уложилось несколько сортировок.
_NAME_RE = re.compile(rf"^{_PREFIX}(\d{{8}}_\d{{6}})(?:_(\d+))?\{_SUFFIX}$")


@dataclass
class Operation:
    """Одна выполненная сортировка (одна запись в истории)."""
    log_path: Path
    when: datetime
    entries: list[dict[str, str]]
    serial: int = 1

    @property
    def count(self) -> int:
        return len(self.entries)


def _parse_stamp(name: str) -> tuple[datetime, int] | None:
    """Время сортировки и её номер внутри секунды. None, если имя чужое.

    Номер нужен для порядка: без него две сортировки одной секунды встают в
    списке как попало, и «отменить последнюю» отменяет не ту.
    """
    match = _NAME_RE.match(name)
    if not match:
        return None
    stamp, serial = match.groups()
    try:
        when = datetime.strptime(stamp, _STAMP_FMT)
    except ValueError:
        return None
    return when, int(serial or 1)


def list_operations(downloads_path: str | Path) -> list[Operation]:
    """Возвращает историю сортировок, самые свежие — первыми.

    Битые/чужие файлы в `.sorter` тихо пропускаются, чтобы история не падала.
    Кодировку разбирает общий декодер (`util.read_text`): журнал лежит в папке
    пользователя, и пересохранённый Блокнотом «в UTF-8 с BOM» файл переставал
    читаться — то есть целая сортировка исчезала из списка, и откатить её было
    уже нечем.
    """
    log_dir = Path(downloads_path) / ".sorter"
    if not log_dir.is_dir():
        return []

    ops: list[Operation] = []
    for path in log_dir.glob(f"{_PREFIX}*{_SUFFIX}"):
        stamp = _parse_stamp(path.name)
        if stamp is None:
            continue
        try:
            raw = json.loads(read_text(path))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, list):
            continue
        when, serial = stamp
        ops.append(Operation(path, when, entries_of(raw), serial))

    ops.sort(key=lambda op: (op.when, op.serial), reverse=True)
    return ops


def undo_operation(op: Operation, config=None) -> list[tuple[str, str]]:
    """Откатывает операцию и убирает её лог. Возвращает список оговорок.

    `config` передаётся дальше в `mover.undo`, чтобы за откатом убрались папки
    программы, опустевшие из-за него.

    Оговорка — это когда файл вернулся не туда, куда собирался: исходный путь
    оказался занят. Список пустой, если всё легло на свои места. Показать его
    обязательно: молчаливый «успешный» откат, после которого данные лежат под
    другим именем, — худший из возможных исходов.

    Журнал удаляется, только когда возвращать больше нечего. Файл держит другая
    программа, исходной папки не стало, диск сняли — такой файл остаётся лежать
    не там, где был, и попытку надо будет повторить. Раньше журнал исчезал в
    любом случае, а вместе с ним и сама возможность: запись пропадала из
    истории, файл оставался на новом месте, и связать одно с другим было нечем.

    Что вернулось, а что нет, спрашиваем у файловой системы: пропал файл по
    новому пути — значит, уехал обратно.
    """
    notes = _undo(op.log_path, config)
    left = [e for e in op.entries if Path(e["dst"]).exists()]
    try:
        if left:
            op.log_path.write_text(
                json.dumps(left, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            op.log_path.unlink()
    except OSError:
        pass
    return notes
