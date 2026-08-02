"""Построение плана перемещений (откуда → куда) с разрешением конфликтов."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .classifier import classify, explain_category, extension_of
from .config import Config
from .scanner import scan

TEXT_EXTENSIONS = {"txt", "md", "csv"}
CONTENT_PREVIEW_CHARS = 2000


@dataclass(frozen=True)
class Move:
    """Одно перемещение. `note` — чем выбрана категория (правило/шаблон/слово)."""

    src: Path
    dst: Path
    note: str = ""


def _read_content(path: Path) -> str:
    if extension_of(path.name) not in TEXT_EXTENSIONS:
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(CONTENT_PREVIEW_CHARS)
    except OSError:
        return ""


def _dedup(dst: Path, taken: set[Path]) -> Path:
    """Свободное имя: при занятом добавляет ` (1)`, ` (2)`…"""
    if dst not in taken and not dst.exists():
        return dst
    stem, suffix = dst.stem, dst.suffix
    i = 1
    while True:
        candidate = dst.with_name(f"{stem} ({i}){suffix}")
        if candidate not in taken and not candidate.exists():
            return candidate
        i += 1


def _external_3d_path(config: Config) -> Path | None:
    """Путь внешней папки 3D (All_3d), если он задан в конфиге."""
    if not isinstance(config.external_3d, dict):
        return None
    raw = config.external_3d.get("path", "")
    return Path(raw) if raw else None


def plan(
    files: list[Path],
    config: Config,
    send_3d_external: bool = False,
    taken: set[Path] | None = None,
) -> list[Move]:
    """План перемещений. Структура: Категория/Тип/файл (без подпапки расширения).

    Если send_3d_external=True, файлы 3D-моделей (по config.external_3d) едут
    во внешнюю папку, разложенные по подпапкам с именем расширения
    (например C:/Drive/Alexey/All_3d/gcode, .../3mf).

    taken — общий набор уже занятых назначений; передаётся, когда план строится
    по нескольким источникам (загрузки + All_3d), чтобы имена не сталкивались.
    """
    root = Path(config.downloads_path)
    ext_3d = {e.lower() for e in config.external_3d.get("extensions", [])}
    external_path = _external_3d_path(config)

    moves: list[Move] = []
    if taken is None:
        taken = set()

    for src in files:
        content = _read_content(src)
        category, reason = explain_category(src.name, content, config)
        _, file_type, extension = classify(src.name, content, config)

        if send_3d_external and external_path is not None and extension in ext_3d:
            dst = external_path / extension / src.name
        else:
            dst = root / category / file_type / src.name

        if src == dst:
            continue  # уже на месте

        dst = _dedup(dst, taken)
        taken.add(dst)
        moves.append(Move(src, dst, note=reason))

    return moves


def plan_3d_folder(
    files: list[Path],
    config: Config,
    taken: set[Path] | None = None,
) -> list[Move]:
    """Раскладка файлов из корня All_3d по подпапкам с именем расширения.

    Каждый файл едет в All_3d/<расширение>/имя (part.gcode → All_3d/gcode/part.gcode).
    Файлы без расширения оставляем на месте. ИИ здесь не нужен — только расширение.
    """
    external_path = _external_3d_path(config)
    if external_path is None:
        return []

    moves: list[Move] = []
    if taken is None:
        taken = set()

    for src in files:
        extension = extension_of(src.name)
        if not extension:
            continue  # без расширения — не трогаем
        dst = external_path / extension / src.name
        if src == dst:
            continue  # уже в своей подпапке
        dst = _dedup(dst, taken)
        taken.add(dst)
        moves.append(Move(src, dst))

    return moves


def build_plan(
    config: Config,
    send_3d_external: bool = False,
    deep: bool = False,
) -> list[Move]:
    """Полный план: загрузки + внешняя папка All_3d (всегда по расширениям).

    deep задаёт глубину разбора загрузок:

    - False — только файлы в корне загрузок. Обычная уборка: разложенное
      не ворошим.
    - True — корень плюс папки, которые программа сама создала
      (`managed_folders`), рекурсивно. Это переразложение: уже разложенные
      файлы проверяются заново, поэтому новые категории и шаблоны применяются
      и к старым загрузкам.

    Чужие папки — распакованные архивы, миры игр, репозитории — не трогаются
    ни в одном режиме. Программа двигает только то, что создала сама.

    Папка All_3d разбирается всегда, если её путь задан в конфиге.
    """
    taken: set[Path] = set()

    downloads = scan(config.downloads_path, config, deep=deep)
    moves = plan(downloads, config, send_3d_external=send_3d_external, taken=taken)

    external_path = _external_3d_path(config)
    if external_path is not None:
        loose = scan(external_path, config, deep=False)
        moves += plan_3d_folder(loose, config, taken=taken)

    return moves
