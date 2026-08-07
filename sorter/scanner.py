"""Сбор файлов для сортировки. В чужие папки не заходит."""
from __future__ import annotations

import fnmatch
from pathlib import Path

from .config import Config, folder_key, folder_keys


def _is_ignored(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)


def scan(root: str | Path, config: Config, deep: bool = True) -> list[Path]:
    """Файлы для сортировки из корня папки.

    deep=True  — файлы корня + рекурсивно из папок, которые программа создала
                 сама (config.managed_folders). Режим переразложения: старые
                 загрузки проверяются заново по текущим правилам.
    deep=False — только файлы, лежащие прямо в корне; ни в какие подпапки
                 не заходим. Обычная уборка и разбор внешней папки All_3d.

    Папки, которых нет в config.managed_folders, не обходятся никогда — это
    чужие папки программ и игр. Их программа не двигает и не разбирает.

    Своё имя опознаётся через `folder_key`: на Windows `Медиа` и `медиа` — одна
    и та же папка, и сверка строка в строку делала из второй чёрную дыру.
    """
    root = Path(root)
    found: list[Path] = []
    if not root.is_dir():
        return found

    managed = folder_keys(config.managed_folders)

    # Куда уже заходили — по настоящему пути, а не по тому, каким пришли.
    # Общий на весь обход: две управляемые папки могут оказаться стыками на
    # одну и ту же настоящую папку.
    visited = {_real(root)}

    # Прочитать папку может не выйти: права, отключённый сетевой диск, вынутая
    # флешка. Внутри управляемых папок этот случай уже обработан
    # (`_walk_managed`), и в корне он ничем не лучше — окно падать не должно.
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return found

    for entry in entries:
        if entry.is_file():
            if not _is_ignored(entry.name, config.ignore):
                found.append(entry)
        elif deep and entry.is_dir() and folder_key(entry.name) in managed:
            found.extend(_walk_managed(entry, config, visited))

    return found


def _real(path: Path) -> Path:
    """Настоящий путь папки: стыки и симлинки развёрнуты.

    Не вышло развернуть (нет прав, отключился диск) — берём как есть: хуже
    от этого не будет, а падать на ровном месте незачем.
    """
    try:
        return path.resolve()
    except OSError:
        return path


def _walk_managed(folder: Path, config: Config, visited: set[Path]) -> list[Path]:
    """Файлы внутри управляемой папки. Вглубь — только по своим папкам.

    Своя папка — та, чьё имя есть в `managed_folders`: категория или тип. Всё
    остальное создал пользователь, и это чужое даже внутри `Учёба/Documents`.
    Подпапка `9 класс` — ручная раскладка; зайдя туда, переразложение вынесло
    бы файлы наверх и молча уничтожило порядок, который наводили руками.

    Правило то же, что и для корня: программа трогает только то, что создала.

    `visited` хранит настоящие пути уже пройденных папок. Стык Windows
    (junction) на папку-предка `iterdir` проходит как обычную папку, и обход
    заворачивался в петлю: один файл попадал в список заново на каждом витке —
    под путями `Медиа/Videos/Медиа/Videos/...`, — пока Windows не упирался в
    предел длины пути. Все витки — один и тот же файл, поэтому план получался
    такой: первое перемещение переименовывало лежащий на месте файл в
    `клип (1).mp4`, следующее уже не находило его и падало, и так шесть
    десятков раз. На последнем витке `is_dir()` не отвечал и сам стык уезжал
    в список файлов — то есть переносилась целая папка.

    Файлом считается только то, что отвечает `is_file()`, — ровно как в корне.
    Раньше сюда попадало всё, что не ответило `is_dir()`, а это не одно и то
    же: у оборванного ярлыка, ссылки на удалённую папку и стыка на путь длиннее
    предела Windows обе проверки дают False. Такая запись доезжала до плана, и
    `apply` спотыкался о неё при каждой уборке — «нет файла» в отчёте, имя в
    списке ошибок как настоящая потеря, и убрать её оттуда можно было только
    руками через проводник.
    """
    managed = folder_keys(config.managed_folders)
    files: list[Path] = []
    stack = [folder]
    while stack:
        current = stack.pop()
        real = _real(current)
        if real in visited:
            continue
        visited.add(real)
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if folder_key(entry.name) in managed:
                    stack.append(entry)
            elif entry.is_file() and not _is_ignored(entry.name, config.ignore):
                files.append(entry)
    return sorted(files)
