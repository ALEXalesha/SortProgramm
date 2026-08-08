"""Построение плана перемещений (откуда → куда) с разрешением конфликтов."""
from __future__ import annotations

import codecs
from dataclasses import dataclass
from pathlib import Path

from .classifier import classify, explain_category, extension_of
from .config import (Config, external_3d_extensions, path_3d_reason,
                     usable_3d_path)
from .scanner import scan, scan_3d

TEXT_EXTENSIONS = {"txt", "md", "csv"}
CONTENT_PREVIEW_CHARS = 2000
# Байтов читаем с запасом: русская буква занимает два и в UTF-8, и в UTF-16, а
# в UTF-16 ещё и латиница по два. Лишнее отрезается уже по символам.
CONTENT_PREVIEW_BYTES = CONTENT_PREVIEW_CHARS * 4

# Метки, с которых начинается файл в UTF-16. Определять эту кодировку иначе
# нельзя: почти любая чётная строка байтов «раскодируется» как UTF-16 без
# ошибки, поэтому пробовать её вслепую значит объявить текстом набор иероглифов.
_UTF16_MARKS = (b"\xff\xfe", b"\xfe\xff")

# Пометка причины для того, что раскладывается по расширению, а не по категории.
# Место таким файлам выбирает расширение, и говорить о них словами таблицы
# категорий («слово», «не опознан») значит называть причину, которой не было.
BY_EXTENSION = "по расширению"


@dataclass(frozen=True)
class Move:
    """Одно перемещение. `note` — чем выбрана категория (правило/шаблон/слово)."""

    src: Path
    dst: Path
    note: str = ""


def _decode(raw: bytes) -> str:
    """Начало текстового файла словами. Не разобралось ничем — пустая строка.

    Читалось это раньше одним способом — как UTF-8 с `errors="ignore"`, — и для
    русского текста в cp1251 такое чтение равносильно отсутствию чтения: каждый
    нечитаемый байт выбрасывается, от строки остаются крохи латиницы, и ни одно
    слово в ней не находится. А cp1251 никуда не делся: так пишут `.txt`
    программы десятилетней давности и `.csv` — Excel на русской Windows.

    Заметить это было нельзя ничем. Пометка у такого файла — честное
    «не опознан», от «слова в тексте и правда нет» неотличимое; в имени
    искомого слова нет по определению (иначе сработала бы пометка «слово»), то
    есть перепроверить строку плана глазами не выйдет. Ключ DeepSeek читается
    во всех кодировках, которые предлагает Блокнот, с тех пор как из-за UTF-16
    гасло окно, — содержимое читалось в одной.

    Обрыв куска на середине символа поломкой не считается: неполный хвост
    держит у себя пошаговый декодер. Без этого обрезанный по границе байтов
    UTF-8 объявлялся бы битым и уезжал разбираться как cp1251 целиком.
    """
    encodings = ("utf-16",) if raw[:2] in _UTF16_MARKS else ("utf-8-sig", "cp1251")
    for encoding in encodings:
        try:
            return codecs.getincrementaldecoder(encoding)().decode(raw)
        except UnicodeDecodeError:
            continue
    return ""


def _read_content(path: Path) -> str:
    if extension_of(path.name) not in TEXT_EXTENSIONS:
        return ""
    try:
        with path.open("rb") as fh:
            raw = fh.read(CONTENT_PREVIEW_BYTES)
    except OSError:
        return ""
    return _decode(raw)[:CONTENT_PREVIEW_CHARS]


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


def external_3d_path(config: Config) -> Path | None:
    """Путь внешней папки 3D (All_3d), если он задан в конфиге и годится.

    Нужен не только планировщику: чистка пустых папок (`mover`) тоже должна
    знать, где кончается своё и начинается чужое.

    Неполный путь (`All_3d`, `C:`) здесь и отбивается — один раз на всех
    читателей. Считаться он будет от рабочей папки, а она у ярлыка какая
    угодно, так что модели уедут из загрузок неизвестно куда, и план при этом
    покажет строку, неотличимую от папки внутри загрузок (`usable_3d_path`).
    Отвечая None, мы приравниваем такой путь к «пути нет»: модели остаются в
    обычных категориях, а `external_3d_warning` говорит, почему.
    """
    if not isinstance(config.external_3d, dict):
        return None
    raw = config.external_3d.get("path", "")
    return Path(raw) if usable_3d_path(raw) else None


def external_3d_warning(config: Config, send_3d_external: bool) -> str:
    """Почему вынос 3D ничего не вынесет. Пустая строка — вынесет или не просили.

    Настройка, которая включена и не работает, — худший вид поломки в этой
    программе: план построен, файлы разложены, жалоб нет, а модели поехали не
    туда, куда человек велел. Отличить это от исправной работы можно только
    помня, куда они должны были поехать.

    Текст общий на три интерфейса нарочно. Раньше о пустом пути говорил
    `Config.load` — то есть по галочке, сохранённой в файле, — а окно PyQt
    считало то же самое ещё раз и по-своему. Консоль не говорила ничего:
    `--to3d` включает вынос поверх выключенной галочки, и тогда предупредить
    было некому. Проверка правил через консоль на том и держится, что консоль
    показывает то же, что окно.

    Про негодный путь говорим и при выключенной галочке: разбор корня All_3d
    по подпапкам расширений идёт всегда, когда путь задан, и негодный путь
    отменяет заодно и его. Пустой путь при выключенной галочке — не поломка,
    а обычная настройка «внешней папкой не пользуемся».
    """
    raw = config.external_3d.get("path", "") if isinstance(config.external_3d, dict) else ""
    reason = path_3d_reason(raw)
    if not reason:
        return ""
    if not raw and not send_3d_external:
        return ""
    tail = ("Модели поедут в обычные категории." if send_3d_external
            else "Папка 3D не разбирается.")
    return f"Вынос 3D: {reason}. {tail}"


def goes_by_extension(filename: str, config: Config, send_3d_external: bool) -> bool:
    """Выберет ли место этому файлу расширение, а не категория.

    Один ответ на всех: так решает `plan`, и так же должна решать кнопка «✨ИИ»,
    которой незачем платить за категорию, которую всё равно никто не спросит.
    """
    if not send_3d_external or external_3d_path(config) is None:
        return False
    return extension_of(filename) in external_3d_extensions(config.external_3d)


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
    external_path = external_3d_path(config)

    moves: list[Move] = []
    if taken is None:
        taken = set()

    for src in files:
        content = _read_content(src)
        category, reason = explain_category(src.name, content, config)
        _, file_type, extension = classify(src.name, content, config)

        if goes_by_extension(src.name, config, send_3d_external):
            # Место выбрало расширение, категория тут ни при чём. Причина её
            # выбора («слово», «не опознан») в такой строке плана врала: по
            # таблице в README «не опознан» значит «едет в Others», а файл едет
            # во внешнюю папку. Просматривать план README советует именно по
            # этой пометке — то есть врала она ровно там, где на неё смотрят.
            dst = external_path / extension / src.name
            reason = BY_EXTENSION
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

    Пометка причины та же, что у моделей, вынесенных из загрузок: назначение
    одно и то же, и объяснять две соседние строки плана по-разному (а раньше
    вторую не объясняли вовсе) незачем.
    """
    external_path = external_3d_path(config)
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
        moves.append(Move(src, dst, note=BY_EXTENSION))

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

    Папка All_3d разбирается всегда, если её путь задан в конфиге, и `deep`
    задаёт глубину и ей тоже: корень разбирается при любой уборке, а папки
    расширений (`All_3d/stl`, `All_3d/gcode`) проверяются заново только при
    переразложении — ровно как папки категорий в загрузках. Раньше внутрь
    All_3d не заглядывали никогда, и модель, однажды уехавшая не в ту подпапку,
    оставалась там навсегда: «Переразложить старое» до неё не доходило.
    """
    taken: set[Path] = set()

    downloads = scan(config.downloads_path, config, deep=deep)
    moves = plan(downloads, config, send_3d_external=send_3d_external, taken=taken)

    external_path = external_3d_path(config)
    if external_path is not None:
        # Если All_3d указывает на саму папку загрузок, второй скан вернёт те же
        # файлы. Без этого фильтра один файл попал бы в план дважды: первое
        # перемещение прошло бы, второе упало с «нет файла».
        planned = {mv.src for mv in moves}
        loose = [f for f in scan_3d(external_path, config, deep=deep)
                 if f not in planned]
        moves += plan_3d_folder(loose, config, taken=taken)

    return moves
