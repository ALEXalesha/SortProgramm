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
    _cleanup_empty_managed(config)
    return result


def _write_undo_log(performed: list[dict[str, str]], config: Config) -> Path:
    root = Path(config.downloads_path)
    log_dir = root / ".sorter"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"undo_{stamp}.json"
    log_path.write_text(json.dumps(performed, ensure_ascii=False, indent=2), encoding="utf-8")
    return log_path


def _cleanup_empty_managed(config: Config) -> None:
    root = Path(config.downloads_path)
    for name in config.managed_folders:
        folder = root / name
        if folder.is_dir():
            _remove_if_empty_tree(folder)


def _remove_if_empty_tree(folder: Path) -> None:
    for child in sorted(folder.iterdir(), reverse=True):
        if child.is_dir():
            _remove_if_empty_tree(child)
    if folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()


def undo(undo_log: Path | str) -> None:
    """Возвращает файлы на исходные места по логу последней сортировки."""
    entries = json.loads(Path(undo_log).read_text(encoding="utf-8"))
    for entry in reversed(entries):
        src, dst = Path(entry["src"]), Path(entry["dst"])
        if dst.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
