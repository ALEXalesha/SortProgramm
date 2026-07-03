"""Построение плана перемещений (откуда → куда) с разрешением конфликтов."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .classifier import classify, extension_of
from .config import Config

TEXT_EXTENSIONS = {"txt", "md", "csv"}
CONTENT_PREVIEW_CHARS = 2000


@dataclass(frozen=True)
class Move:
    src: Path
    dst: Path


def _read_content(path: Path) -> str:
    if extension_of(path.name) not in TEXT_EXTENSIONS:
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(CONTENT_PREVIEW_CHARS)
    except OSError:
        return ""


def _dedup(dst: Path, taken: set[Path]) -> Path:
    if dst not in taken and not dst.exists():
        return dst
    stem, suffix = dst.stem, dst.suffix
    i = 1
    while True:
        candidate = dst.with_name(f"{stem} ({i}){suffix}")
        if candidate not in taken and not candidate.exists():
            return candidate
        i += 1


def plan(files: list[Path], config: Config, send_3d_external: bool = False) -> list[Move]:
    """План перемещений. Структура: Категория/Тип/файл (без подпапки расширения).

    Если send_3d_external=True, файлы 3D-моделей (по config.external_3d) едут
    во внешнюю папку, разложенные по подпапкам с именем расширения
    (например C:/Drive/Alexey/All_3d/gcode, .../3mf).
    """
    root = Path(config.downloads_path)
    ext_3d = {e.lower() for e in config.external_3d.get("extensions", [])}
    external_path = Path(config.external_3d.get("path", "")) if config.external_3d else None

    moves: list[Move] = []
    taken: set[Path] = set()

    for src in files:
        category, file_type, extension = classify(src.name, _read_content(src), config)

        if send_3d_external and external_path is not None and extension in ext_3d:
            dst = external_path / extension / src.name
        else:
            dst = root / category / file_type / src.name

        if src == dst:
            continue  # уже на месте

        dst = _dedup(dst, taken)
        taken.add(dst)
        moves.append(Move(src, dst))

    return moves
