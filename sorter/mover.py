"""Выполнение плана перемещений: dry-run / apply, лог отмены, чистка папок."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config, folder_key, folder_keys
from .planner import Move, external_3d_path


@dataclass
class Result:
    planned: int = 0
    moved: int = 0
    skipped: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    # Оговорки: файл переехал, но не совсем так, как обещал план.
    notes: list[tuple[str, str]] = field(default_factory=list)
    undo_log: Path | None = None
    # Почему не записался журнал отмены. Пустая строка — записался.
    #
    # Отдельно от `errors` нарочно. Там лежат файлы, которые остались лежать в
    # загрузках, и отчёт печатает их под заголовком «Не переехали». Незаписанный
    # журнал не файл и никуда не переезжал: все файлы как раз доехали, а нет
    # только возможности вернуть их назад. Попав в общий список, он и врал
    # заголовком, и завышал счёт: «Перемещено: 7, ошибок: 1» на семи из семи
    # успешно переехавших файлов.
    undo_failed: str = ""


def _vacate_first(moves: list[Move]) -> list[Move]:
    """Ставит вперёд то, что занимает путь чужой папки назначения.

    Файл `Медиа` без расширения — обычный файл, и план у него правильный:
    уезжает в `Others/Misc`, как всё неопознанное. Но пока он лежит в корне,
    папки `Медиа` там быть не может, и `mkdir` для `Медиа/Videos` падает с
    `[WinError 183]`. Выполнялся план в том порядке, в каком его построили, а
    порядок этот задан именами файлов — то есть попадёт ли `клип.mp4` до или
    после виновника, решает алфавит.

    Отчёт в таком случае честный, но говорит про папку назначения, которую не
    создать, а не про файл, лежащий рядом и мешающий: связать одно с другим,
    глядя на `[WinError 183]`, нельзя. Со следующей уборки всё проходит само —
    виновник к тому времени уехал, — и это тоже плохо: поломка, которая
    исчезает при повторе, выглядит случайной.

    Сортировка устойчивая, поэтому всё, что чужих путей не занимает, идёт как
    шло. Свободное имя `apply` подбирает заново перед каждым перемещением
    (`_free_name`), так что перестановка ничьё имя не путает.
    """
    blocking = {parent for mv in moves for parent in mv.dst.parents}
    return sorted(moves, key=lambda mv: mv.src not in blocking)


def apply(moves: list[Move], config: Config, dry_run: bool = True) -> Result:
    result = Result(planned=len(moves))
    if dry_run:
        return result

    performed: list[dict[str, str]] = []
    for mv in _vacate_first(moves):
        try:
            if not mv.src.exists():
                raise FileNotFoundError(f"нет файла: {mv.src}")
            mv.dst.parent.mkdir(parents=True, exist_ok=True)
            # Свободное имя planner подбирал по состоянию на момент плана, а
            # между «Очистить» и «Применить» проходит сколько угодно времени.
            # Занятую за это время цель `shutil.move` затирает молча (файл) или
            # вкладывает в неё (папка) — и то и другое снаружи выглядит как
            # успешная сортировка. Проверяем ещё раз, прямо перед перемещением.
            dst = _free_name(mv.dst, as_dir=mv.src.is_dir())
            shutil.move(str(mv.src), str(dst))
            # Оговорку ставим только после того, как перемещение прошло. Раньше
            # она писалась заранее, и упавший `shutil.move` (файл открыт другой
            # программой, кончилось место) давал отчёт, который спорит сам с
            # собой: один и тот же файл стоял и в «Не переехали», и в «Легли под
            # другим именем». Человек шёл искать `клип (1).mp4`, которого нет.
            if dst != mv.dst:
                result.notes.append((
                    str(mv.src),
                    f"в цели уже есть «{mv.dst.name}», положили как «{dst.name}»"))
            performed.append({"src": str(mv.src), "dst": str(dst)})
            result.moved += 1
        except OSError as exc:
            result.errors.append((str(mv.src), str(exc)))

    # Пустой журнал — это запись «0 файлов» в истории, которая ничего не
    # откатывает. Когда двигать было нечего, истории об этом знать незачем.
    if performed:
        try:
            result.undo_log = _write_undo_log(performed, config)
        except OSError as exc:
            # Файлы уже переехали, а вернуть их назад теперь нечем. Молчать об
            # этом нельзя: снаружи всё выглядит как обычная успешная сортировка.
            #
            # И сказать надо своими словами, а не через список неудавшихся
            # перемещений, куда это складывали раньше. Печатается тот список
            # под заголовком «Не переехали», и оба его слова тут неправда:
            # переехали все до одного, а «журнал отмены» — не файл. Счёт ошибок
            # он завышал заодно, а по нему окно решает, каким окном отчитаться.
            result.undo_failed = str(exc)
    _cleanup_emptied(performed, config)
    return result


def _write_undo_log(performed: list[dict[str, str]], config: Config) -> Path:
    """Пишет журнал отмены и возвращает путь к нему.

    Имя журнала — метка времени с точностью до секунды, и две сортировки подряд
    укладываются в одну секунду запросто: нажал «Применить», поправил галочку,
    нажал снова. Занятое имя поэтому не перезаписываем, а дополняем номером —
    иначе прошлый журнал исчезает вместе с возможностью откатить ту сортировку.
    """
    log_dir = Path(config.downloads_path) / ".sorter"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"undo_{stamp}.json"
    serial = 2
    while log_path.exists():
        log_path = log_dir / f"undo_{stamp}_{serial}.json"
        serial += 1
    log_path.write_text(json.dumps(performed, ensure_ascii=False, indent=2), encoding="utf-8")
    return log_path


def _cleanup_emptied(performed: list[dict[str, str]], config: Config) -> None:
    """Убирает папки, которые опустели именно из-за этого прогона.

    Раньше чистка шла по всему списку `managed_folders` — и сносила любую пустую
    папку с подходящим именем, даже если пользователь создал её сам и программа
    к ней не прикасалась. Теперь отталкиваемся от того, откуда реально уносили
    файлы, и поднимаемся вверх, пока папки пустые и принадлежат программе.

    Своя папка — это категория или тип из `managed_folders`, а во внешней папке
    3D ещё и подпапка с именем расширения: `All_3d/gcode` создаёт сама
    программа (`plan_3d_folder`), но в `managed_folders` такого имени нет и
    быть не должно — это расширения, а не категории. Из-за этого откат
    возвращал файлы из All_3d, а пустые `gcode`, `stl`, `3mf` оставлял лежать
    навсегда: та самая половина отмены, ради которой уборку и добавляли.

    Вверх поднимаемся до корня загрузок или до самой папки 3D — их не трогаем
    никогда, это чужая территория, а не созданный программой каркас.

    Своё имя опознаётся через `folder_key`, как и при обходе: `Медиа` и `медиа`
    на Windows — одна папка, и уборка спотыкалась о регистр ровно там же, где
    спотыкался `scanner`.
    """
    root = Path(config.downloads_path)
    external = external_3d_path(config)
    stop = {root} if external is None else {root, external}
    managed = folder_keys(config.managed_folders)
    for entry in performed:
        folder = Path(entry["src"]).parent
        while (folder not in stop and folder.is_dir()
               and (folder_key(folder.name) in managed or folder.parent == external)):
            try:
                if any(folder.iterdir()):
                    break
                folder.rmdir()
            except OSError:
                break
            folder = folder.parent


def _free_name(path: Path, as_dir: bool | None = None) -> Path:
    """Свободное имя рядом с занятым: ` (1)`, ` (2)`…

    У папки расширения нет, но точки в имени бывают
    (`zapret-discord-youtube-1.9.2`), поэтому номер к ней приписывается
    к имени целиком, а не перед последней точкой.

    `as_dir` — папку ли мы кладём. По умолчанию смотрим на то, что уже лежит
    по этому пути, и обычно этого хватает: занимает место обычно такой же
    объект. Но файл может упереться и в папку с тем же именем — тогда номер
    надо ставить по природе того, что кладём (`клип (1).mp4`), а не того, что
    мешает (`клип.mp4 (1)`).
    """
    if not path.exists():
        return path
    if as_dir is None:
        as_dir = path.is_dir()
    stem, suffix = (path.name, "") if as_dir else (path.stem, path.suffix)
    i = 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def entries_of(raw) -> list[dict[str, str]]:
    """Оставляет из журнала только пары «откуда/куда».

    Журнал пишет программа, но лежит он в папке пользователя: правка руками,
    оборванная запись, чужой файл под тем же именем. Всё, что не похоже на
    пару путей, дальше не пускаем — иначе на такой записи падает и откат, и
    окно истории.
    """
    if not isinstance(raw, list):
        return []
    good: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        src, dst = entry.get("src"), entry.get("dst")
        if isinstance(src, str) and isinstance(dst, str) and src and dst:
            good.append({"src": src, "dst": dst})
    return good


def undo(undo_log: Path | str, config: Config | None = None) -> list[tuple[str, str]]:
    """Возвращает файлы на исходные места. Отдаёт список оговорок.

    Если исходный путь к моменту отката снова занят, `shutil.move` повёл бы себя
    коварно: папку он положил бы ВНУТРЬ одноимённой (`claude-usage/claude-usage`),
    а файл мог бы затереть. Поэтому занятый путь не трогаем — возвращаем рядом,
    под свободным именем, и сообщаем об этом наверх. Молчаливое вложение хуже
    любой ошибки: снаружи кажется, что откат прошёл, а данные перепутаны.

    `config` нужен, чтобы убрать за собой папки, опустевшие из-за отката. Без
    него откат возвращал файлы, но оставлял в загрузках весь каркас из папок
    программы — снаружи это выглядело как отмена наполовину.

    Про файл, которого по новому пути уже нет, тоже говорим вслух. Унесли
    руками, переименовали, удалили — вернуть нечего, и раньше такая запись
    просто пропускалась. Отменяя сортировку, где так вышло со всеми файлами,
    человек соглашался на «Вернуть 7 файлов?» и не получал ни файлов, ни
    единого слова, а запись при этом исчезала из истории — то есть и
    разбираться потом было уже не с чем.
    """
    log_path = Path(undo_log)
    try:
        entries = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Записи внутри журнала разбираются осторожно (`entries_of`), а сам файл
        # читался напрямую: на оборванной записи `json.loads` бросает ValueError,
        # который окно истории не ловит, — и программа падала целиком вместо
        # того, чтобы сказать, что откатывать нечем.
        return [(str(log_path), f"журнал отмены не читается: {exc}")]

    notes: list[tuple[str, str]] = []
    restored: list[dict[str, str]] = []
    for entry in reversed(entries_of(entries)):
        src, dst = Path(entry["src"]), Path(entry["dst"])
        if not dst.exists():
            notes.append((str(dst), "возвращать нечего: файла тут больше нет"))
            continue
        target = src
        if src.exists():
            # Номер ставим по природе того, что возвращаем, а не того, что
            # заняло место: иначе видео, упёршееся в папку `клип.mp4`, вернётся
            # как `клип.mp4 (1)` — расширение перестало быть последним, и файл
            # больше не открывается двойным щелчком.
            target = _free_name(src, as_dir=dst.is_dir())
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(target))
        except OSError as exc:
            notes.append((str(dst), str(exc)))
        else:
            # Про новое имя говорим только когда файл под ним и правда лежит.
            # Заранее поставленная оговорка после упавшего `shutil.move`
            # называла имя, которого на диске нет, — и это в том самом отчёте,
            # ради которого откат вообще отчитывается.
            if target != src:
                notes.append((str(src), f"путь занят, вернули как «{target.name}»"))
            restored.append({"src": str(dst), "dst": str(target)})

    if config is not None:
        # Откат опустошает ровно те же папки, которые наполнила сортировка,
        # поэтому и убирается тем же способом — от места, откуда унесли файл,
        # вверх, пока папки пустые и принадлежат программе.
        _cleanup_emptied(restored, config)
    return notes
