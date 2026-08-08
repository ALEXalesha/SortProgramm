"""Сбор файлов для сортировки. В чужие папки не заходит."""
from __future__ import annotations

import fnmatch
from pathlib import Path

from .classifier import extension_of
from .config import (Config, extension_key, external_3d_extensions,
                     folder_key, folder_keys)


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


def is_extension_folder(folder: Path, config: Config) -> bool:
    """Подпапка внешней папки 3D, которую создала сама программа.

    Своей она считается двумя способами, и хватает любого.

    Первый — по имени: оно есть в `external_3d.extensions`. Такие папки
    (`stl`, `gcode`, `3mf`) создаёт сама программа (`plan_3d_folder`), и
    спорить тут не о чем. Раньше этого признака не было, и одного второго не
    хватало ровно в том случае, ради которого разбор папок расширений и
    добавляли: в `All_3d/stl` не осталось ни единого `.stl` — расширение убрали
    из настройки, файлы переименовали, папку набили руками из проводника, — и
    «Переразложить старое» проходило мимо. То есть модель, уехавшая не туда,
    оставалась там навсегда, а починка молчала о том, что не сработала.

    Второй — по содержимому: внутри лежит хотя бы один файл ровно с тем
    расширением, которым папка названа. Он нужен для всего, что программа
    насыпала по расширениям, не значащимся в настройке (`zip`, `blend`), — имя
    там любое. Одного имени в таких случаях мало: `модели`, `запчасти`,
    `проекты` выглядят так же, а раскладывал их человек руками, и
    переразложение вынесло бы файлы наверх, разобрав по расширениям порядок,
    который никто не просил трогать. Это то же правило, по которому обход не
    заходит в `Учёба/Documents/9 класс`, только опознавательный знак здесь
    другой: списка `managed_folders` для All_3d нет и быть не может — там
    расширения, а не категории.

    Файл-свидетель ищется только прямо внутри папки. Глубже начинается уже
    чужая раскладка: `All_3d/stl/корпус/деталь.stl` человек разложил сам.
    """
    key = extension_key(folder.name)
    if not key:
        return False
    if key in external_3d_extensions(config.external_3d):
        return True
    try:
        entries = folder.iterdir()
    except OSError:
        return False
    for entry in entries:
        if entry.is_file() and extension_of(entry.name) == key:
            return True
    return False


def scan_3d(root: str | Path, config: Config, deep: bool = False) -> list[Path]:
    """Файлы внешней папки 3D (All_3d).

    deep=False — только корень: файлы, которые туда положили руками или
                 скачали, и которых ещё не касалась раскладка по расширениям.
    deep=True  — плюс папки расширений, созданные самой программой
                 (`is_extension_folder`). Режим «Переразложить старое».

    Разбор корня был всегда, а внутрь программа не заглядывала ни разу — то
    есть однажды уехавшая не в ту подпапку модель оставалась там навсегда.
    Попасть туда просто: расширение убрали из `external_3d.extensions`, файл
    переименовали, папку набили руками из проводника. «Переразложить старое»
    для загрузок это чинит с самого начала, а для All_3d не делало ничего:
    галочка стояла, план строился, папка 3D в нём не участвовала.

    Файл, лежащий в папке своего расширения, никуда не поедет — `plan_3d_folder`
    сравнивает откуда с куда и пропускает совпавшее. То есть на прибранной
    папке этот режим просто ничего не находит.
    """
    root = Path(root)
    found = scan(root, config, deep=False)
    if not deep or not root.is_dir():
        return found
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return found
    for entry in entries:
        if entry.is_dir() and is_extension_folder(entry, config):
            found.extend(scan(entry, config, deep=False))
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
