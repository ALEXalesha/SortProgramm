"""Выполнение плана перемещений: dry-run / apply, лог отмены, чистка папок."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config
from .planner import Move


@dataclass
class Result:
    planned: int = 0
    moved: int = 0
    skipped: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    undo_log: Path | None = None


def apply(moves: list[Move], config: Config, dry_run: bool = True) -> Result:
    result = Result(planned=len(moves))
    if dry_run:
        return result

    performed: list[dict[str, str]] = []
    for mv in moves:
        try:
            if not mv.src.exists():
                raise FileNotFoundError(f"нет файла: {mv.src}")
            mv.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(mv.src), str(mv.dst))
            performed.append({"src": str(mv.src), "dst": str(mv.dst)})
            result.moved += 1
        except OSError as exc:
            result.errors.append((str(mv.src), str(exc)))

    result.undo_log = _write_undo_log(performed, config)
    _cleanup_emptied(performed, config)
    return result


def _write_undo_log(performed: list[dict[str, str]], config: Config) -> Path:
    root = Path(config.downloads_path)
    log_dir = root / ".sorter"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"undo_{stamp}.json"
    log_path.write_text(json.dumps(performed, ensure_ascii=False, indent=2), encoding="utf-8")
    return log_path


def _cleanup_emptied(performed: list[dict[str, str]], config: Config) -> None:
    """Убирает папки, которые опустели именно из-за этого прогона.

    Раньше чистка шла по всему списку `managed_folders` — и сносила любую пустую
    папку с подходящим именем, даже если пользователь создал её сам и программа
    к ней не прикасалась. Теперь отталкиваемся от того, откуда реально уносили
    файлы, и поднимаемся вверх, пока папки пустые и принадлежат программе.
    """
    root = Path(config.downloads_path)
    managed = set(config.managed_folders)
    for entry in performed:
        folder = Path(entry["src"]).parent
        while folder != root and folder.name in managed and folder.is_dir():
            try:
                if any(folder.iterdir()):
                    break
                folder.rmdir()
            except OSError:
                break
            folder = folder.parent


def _free_name(path: Path) -> Path:
    """Свободное имя рядом с занятым: ` (1)`, ` (2)`…

    У папки расширения нет, но точки в имени бывают
    (`zapret-discord-youtube-1.9.2`), поэтому номер к ней приписывается
    к имени целиком, а не перед последней точкой.
    """
    if not path.exists():
        return path
    stem, suffix = (path.name, "") if path.is_dir() else (path.stem, path.suffix)
    i = 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def undo(undo_log: Path | str) -> list[tuple[str, str]]:
    """Возвращает файлы на исходные места. Отдаёт список оговорок.

    Если исходный путь к моменту отката снова занят, `shutil.move` повёл бы себя
    коварно: папку он положил бы ВНУТРЬ одноимённой (`claude-usage/claude-usage`),
    а файл мог бы затереть. Поэтому занятый путь не трогаем — возвращаем рядом,
    под свободным именем, и сообщаем об этом наверх. Молчаливое вложение хуже
    любой ошибки: снаружи кажется, что откат прошёл, а данные перепутаны.
    """
    entries = json.loads(Path(undo_log).read_text(encoding="utf-8"))
    notes: list[tuple[str, str]] = []
    for entry in reversed(entries):
        src, dst = Path(entry["src"]), Path(entry["dst"])
        if not dst.exists():
            continue
        target = src
        if src.exists():
            target = _free_name(src)
            notes.append((str(src), f"путь занят, вернули как «{target.name}»"))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(target))
        except OSError as exc:
            notes.append((str(dst), str(exc)))
    return notes
