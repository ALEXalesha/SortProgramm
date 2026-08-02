"""Построение плана перемещений (откуда → куда) с разрешением конфликтов."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .classifier import classify, extension_of
from .config import Config
from .folders import classify_folder, scan_folders
from .scanner import scan

TEXT_EXTENSIONS = {"txt", "md", "csv"}
CONTENT_PREVIEW_CHARS = 2000


@dataclass(frozen=True)
class Move:
    """Одно перемещение. `note` — почему так решили (для папок), пусто для файлов."""

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


def _dedup(dst: Path, taken: set[Path], split_extension: bool = True) -> Path:
    """Свободное имя: при занятом добавляет ` (1)`, ` (2)`…

    split_extension=False — для папок. У папки нет расширения, но точки в имени
    есть: `zapret-discord-youtube-1.9.2` разбилось бы на stem `...-1.9` и suffix
    `.2` и превратилось в `zapret-discord-youtube-1.9 (1).2`. Номер должен идти
    в конец целиком.
    """
    if dst not in taken and not dst.exists():
        return dst
    stem, suffix = (dst.stem, dst.suffix) if split_extension else (dst.name, "")
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


def plan_folders(
    folders: list[Path],
    config: Config,
    taken: set[Path] | None = None,
) -> list[Move]:
    """План для целых папок: Категория/<folder_bucket>/имя.

    Отдельная корзина (`_Папки`) вместо типа: у папки нет расширения, а мешать
    её с файлами в `Игры/Installers` — значит потерять границу между «программа»
    и «набор файлов программы». Подчёркивание в начале держит корзину сверху
    списка в проводнике.
    """
    root = Path(config.downloads_path)
    bucket = config.folder_bucket

    moves: list[Move] = []
    if taken is None:
        taken = set()

    for src in folders:
        verdict = classify_folder(src, config)
        dst = root / verdict.category / bucket / src.name
        if src == dst:
            continue
        dst = _dedup(dst, taken, split_extension=False)
        taken.add(dst)
        moves.append(Move(src, dst, note=verdict.reason))

    return moves


def build_plan(
    config: Config,
    send_3d_external: bool = False,
    deep: bool = False,
    include_folders: bool = False,
) -> list[Move]:
    """Полный план: загрузки + внешняя папка All_3d (всегда по расширениям).

    deep управляет только загрузками: False — без ИИ (только корень загрузок),
    True — режим ИИ (корень + управляемые папки рекурсивно). Папка All_3d
    разбирается всегда, если её путь задан в конфиге, независимо от галочки 3D.

    include_folders добавляет в план целые папки из корня загрузок. По умолчанию
    выключено: перенос папки заметнее и рискованнее переноса файла, поэтому это
    осознанный выбор, а не поведение по умолчанию.
    """
    taken: set[Path] = set()

    downloads = scan(config.downloads_path, config, deep=deep)
    moves = plan(downloads, config, send_3d_external=send_3d_external, taken=taken)

    if include_folders:
        moves += plan_folders(scan_folders(config.downloads_path, config), config, taken=taken)

    external_path = _external_3d_path(config)
    if external_path is not None:
        loose = scan(external_path, config, deep=False)
        moves += plan_3d_folder(loose, config, taken=taken)

    return moves
