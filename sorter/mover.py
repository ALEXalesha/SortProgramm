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
    # Оговорки: файл переехал, но не совсем так, как обещал план.
    notes: list[tuple[str, str]] = field(default_factory=list)
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
            # Свободное имя planner подбирал по состоянию на момент плана, а
            # между «Очистить» и «Применить» проходит сколько угодно времени.
            # Занятую за это время цель `shutil.move` затирает молча (файл) или
            # вкладывает в неё (папка) — и то и другое снаружи выглядит как
            # успешная сортировка. Проверяем ещё раз, прямо перед перемещением.
            dst = _free_name(mv.dst, as_dir=mv.src.is_dir())
            if dst != mv.dst:
                result.notes.append((
                    str(mv.src),
                    f"в цели уже есть «{mv.dst.name}», положили как «{dst.name}»"))
            shutil.move(str(mv.src), str(dst))
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
            result.errors.append(("журнал отмены", f"не записан: {exc}"))
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
            continue
        target = src
        if src.exists():
            # Номер ставим по природе того, что возвращаем, а не того, что
            # заняло место: иначе видео, упёршееся в папку `клип.mp4`, вернётся
            # как `клип.mp4 (1)` — расширение перестало быть последним, и файл
            # больше не открывается двойным щелчком.
            target = _free_name(src, as_dir=dst.is_dir())
            notes.append((str(src), f"путь занят, вернули как «{target.name}»"))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(target))
        except OSError as exc:
            notes.append((str(dst), str(exc)))
        else:
            restored.append({"src": str(dst), "dst": str(target)})

    if config is not None:
        # Откат опустошает ровно те же папки, которые наполнила сортировка,
        # поэтому и убирается тем же способом — от места, откуда унесли файл,
        # вверх, пока папки пустые и принадлежат программе.
        _cleanup_emptied(restored, config)
    return notes
