"""Построение плана перемещений (откуда → куда) с разрешением конфликтов."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .classifier import classify, explain_category, extension_of
from .config import (Config, external_3d_extensions, path_3d_reason,
                     usable_3d_path)
from .scanner import scan, scan_3d
from .util import decode_text

TEXT_EXTENSIONS = {"txt", "md", "csv"}
CONTENT_PREVIEW_CHARS = 2000
# Байтов читаем с запасом: русская буква занимает два и в UTF-8, и в UTF-16, а
# в UTF-16 ещё и латиница по два. Лишнее отрезается уже по символам.
CONTENT_PREVIEW_BYTES = CONTENT_PREVIEW_CHARS * 4

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


def _read_content(path: Path) -> str:
    """Начало текстового файла словами. Не прочиталось — пустая строка.

    Кодировку разбирает общий декодер (`util.decode_text`). Читалось это раньше
    одним способом — как UTF-8 с `errors="ignore"`, — и для русского текста в
    cp1251 такое чтение равносильно отсутствию чтения: каждый нечитаемый байт
    выбрасывается, от строки остаются крохи латиницы, и ни одно слово в ней не
    находится. А cp1251 никуда не делся: так пишут `.txt` программы
    десятилетней давности и `.csv` — Excel на русской Windows.

    Заметить это было нельзя ничем. Пометка у такого файла — честное
    «не опознан», от «слова в тексте и правда нет» неотличимое; в имени
    искомого слова нет по определению (иначе сработала бы пометка «слово»), то
    есть перепроверить строку плана глазами не выйдет.
    """
    if extension_of(path.name) not in TEXT_EXTENSIONS:
        return ""
    try:
        with path.open("rb") as fh:
            raw = fh.read(CONTENT_PREVIEW_BYTES)
    except OSError:
        return ""
    return decode_text(raw)[:CONTENT_PREVIEW_CHARS]


def _dedup(dst: Path, taken: set[Path], vacating: set[Path] | None = None) -> Path:
    """Свободное имя: при занятом добавляет ` (1)`, ` (2)`…

    `vacating` — файлы, которые этот же план уносит с их мест. Занятым такой
    путь не считается: к тому времени, когда до него дойдёт очередь, там будет
    пусто.

    Без этого номер приписывался за столкновение, которое план сам же и
    разрешает. Расклад самый обычный: правила поправили, `заметка.txt` из
    `Медиа/Documents` уезжает в `Учёбу`, а в корне лежит новая `заметка.txt`,
    которой место как раз в `Медиа/Documents`. Занятость проверялась по
    состоянию на момент построения плана — то есть по файлу, который в этом же
    плане стоит строкой ниже с пометкой «уезжает», — и новая заметка
    получала имя `заметка (1).txt`. Навсегда: `base_name` служебный номер при
    разборе снимает, так что следующая уборка файл не трогает, а
    `заметка.txt` рядом остаётся свободной. Отчёт при этом честный и оговорки
    не ставит — план обещал `заметка (1).txt`, файл так и лёг, — а человек
    получает переименованный файл там, где ничего не сталкивалось.

    Уступать дорогу по-настоящему приходится уже при выполнении: занятый путь
    надо освободить раньше, чем в него класть (`mover._vacate_first`). Здесь мы
    только перестаём резервировать номер; если освободить не выйдет,
    `apply` подберёт свободное имя сам и скажет об этом оговоркой.
    """
    vacating = vacating or set()

    def free(path: Path) -> bool:
        return path not in taken and (not path.exists() or path in vacating)

    if free(dst):
        return dst
    stem, suffix = dst.stem, dst.suffix
    i = 1
    while True:
        candidate = dst.with_name(f"{stem} ({i}){suffix}")
        if free(candidate):
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

    Считается план в два прохода. Сначала каждому файлу выбирается место, потом
    разрешаются столкновения имён — и только на втором проходе известно, кто из
    файлов со своего места уходит. Занятый уходящим путь столкновением не
    считается (`_dedup`), иначе номер ` (1)` приписывается за конфликт, который
    этот же план и разрешает.
    """
    root = Path(config.downloads_path)
    external_path = external_3d_path(config)

    if taken is None:
        taken = set()

    chosen: list[tuple[Path, Path, str]] = []
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

        chosen.append((src, dst, reason))

    # Файл, оставшийся на месте, сюда не попал — его путь занят по-настоящему.
    vacating = {src for src, _, _ in chosen}

    moves: list[Move] = []
    for src, dst, reason in chosen:
        dst = _dedup(dst, taken, vacating)
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

    if taken is None:
        taken = set()

    # Два прохода и тот же расчёт, что у `plan`: пока не выбраны все места,
    # неизвестно, кто со своего уходит, а занятый уходящим путь столкновением
    # не считается.
    chosen: list[tuple[Path, Path]] = []
    for src in files:
        extension = extension_of(src.name)
        if not extension:
            continue  # без расширения — не трогаем
        dst = external_path / extension / src.name
        if src == dst:
            continue  # уже в своей подпапке
        chosen.append((src, dst))

    vacating = {src for src, _ in chosen}

    moves: list[Move] = []
    for src, dst in chosen:
        dst = _dedup(dst, taken, vacating)
        taken.add(dst)
        moves.append(Move(src, dst, note=BY_EXTENSION))

    return moves


def build_plan(
    config: Config,
    send_3d_external: bool = False,
    deep: bool = False,
    problems: list[str] | None = None,
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

    `problems` — тот же список жалоб, каким отвечают разбор настроек и чтение
    журналов отмены. Сюда попадают папки, которые не удалось прочитать
    (`scanner._unreadable`): их файлы в план не попали, и без этой строки ноль
    в плане неотличим от прибранных загрузок. План строят все три интерфейса и
    зовут для этого именно `build_plan` — значит и жалоба должна выходить
    отсюда, а не оставаться в обходе.
    """
    taken: set[Path] = set()

    downloads = scan(config.downloads_path, config, deep=deep, problems=problems)
    moves = plan(downloads, config, send_3d_external=send_3d_external, taken=taken)

    external_path = external_3d_path(config)
    if external_path is not None:
        # Если All_3d указывает на саму папку загрузок, второй скан вернёт те же
        # файлы. Без этого фильтра один файл попал бы в план дважды: первое
        # перемещение прошло бы, второе упало с «нет файла».
        planned = {mv.src for mv in moves}
        loose = [f for f in scan_3d(external_path, config, deep=deep,
                                    problems=problems)
                 if f not in planned]
        moves += plan_3d_folder(loose, config, taken=taken)

    return moves
