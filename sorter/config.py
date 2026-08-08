"""Конфигурация сортировщика: правила раскладки и настройки пользователя.

Разнесены по двум файлам намеренно.

`rules.json` — правила: категории, шаблоны, типы, подсказки. Их поставляет
программа и обновляет при каждой установке.

`config.json` — настройки пользователя: где искать загрузки и куда выносить
3D-модели. Их программа пишет сама и при обновлении не трогает.

Раньше всё лежало в одном `config.json`, который установщик помечал
«не перезаписывать» — чтобы не стереть пути пользователя. Побочный эффект:
новые категории не доезжали до уже установленной копии никогда. Разделение
снимает противоречие: правила обновляются, настройки живут своей жизнью.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

RULES_FILENAME = "rules.json"
OVERRIDES_FILENAME = "overrides.json"

# Расширения 3D-моделей по умолчанию. Окно сохраняет в config.json только
# «включено» и «путь», поэтому список расширений из файла пропадал после первого
# же закрытия — и галочка «3D → отдельная папка» молча переставала работать.
DEFAULT_3D_EXTENSIONS = ["3mf", "obj", "stl", "gcode"]

# Куда смотреть, если папка загрузок в настройках не указана или файл испорчен.
DEFAULT_DOWNLOADS = str(Path.home() / "Downloads")

# Что относится к правилам, а что к настройкам. Ключи, не попавшие ни туда, ни
# сюда, сохраняются как есть — чужую правку конфига мы не выбрасываем.
RULE_KEYS = (
    "categories",
    "patterns",
    "category_hints",
    "type_map",
    "managed_folders",
    "ignore",
    "fallback_category",
    "fallback_type",
)
USER_KEYS = ("downloads_path", "external_3d")


_SEPARATORS = re.compile(r"[\\/]")


def folder_key(name: str) -> str:
    """Имя папки в виде, пригодном для сравнения с тем, что лежит на диске.

    Windows считает `Медиа` и `медиа` одной и той же папкой, а `mkdir` не
    переименовывает уже существующую: стоит ей появиться раньше в другом
    регистре — от прошлой версии правил, от руки, от другой программы, — и
    файлы молча укладываются в неё. Снаружи всё в порядке: план показан, файлы
    разложены. А сверка с `managed_folders` шла строка в строку, поэтому такая
    папка переставала быть своей: «Переразложить старое» в неё не заходило,
    новые категории до лежащего внутри не доезжали никогда, и пустой её никто
    не убирал. Чёрная дыра ровно того вида, о котором предупреждает
    `_check_managed`, — только заметить её нечем.

    `os.path.normcase` делает нужное и ровно там, где нужно: на Windows
    приводит регистр, на Linux и macOS оставляет имя как есть — там `Медиа` и
    `медиа` и правда разные папки, и заходить во вторую было бы уже вторжением
    в чужое.
    """
    return os.path.normcase(name)


def folder_keys(names) -> set[str]:
    """Набор имён папок для сравнения (см. `folder_key`)."""
    return {folder_key(name) for name in names}


def usable_3d_path(raw) -> bool:
    """Годится ли строка как путь внешней папки 3D.

    С категорией всё наоборот: там путь, вписанный вместо имени папки, уносит
    файлы из загрузок неизвестно куда (`_is_folder_name`). Здесь ждут именно
    путь — и неполный опасен ровно тем же. `Path("All_3d")` считается от
    рабочей папки, а у ярлыка она какая угодно: папка программы, рабочий стол,
    последняя папка проводника. Модели уезжают туда, куда никто не смотрит.

    Заметить это нечем: план показывает `All_3d\\stl\\деталь.stl` — строку,
    неотличимую от папки внутри загрузок. Имя без диска попадает в настройку
    легко: опечатка при правке `config.json` руками, набранное вручную поле
    вместо «Обзор…», перенос конфига с другой машины.

    `C:` без косой черты тоже неполный: Windows считает от текущей папки на
    диске C, и это снова не то место, которое имели в виду.
    """
    return isinstance(raw, str) and bool(raw) and Path(raw).is_absolute()


def path_3d_reason(raw) -> str:
    """Что не так с путём внешней папки 3D. Пустая строка — всё в порядке.

    Причина живёт здесь одна на всех, потому что говорят о ней в двух местах:
    при чтении настроек (жалоба на файл, её показывают окна при старте) и при
    построении плана (`planner.external_3d_warning` — про этот конкретный
    прогон, где галочку мог перебить флаг `--to3d`). Консоль печатает и то, и
    другое подряд, и без общего куска текста она повторяла бы одно и то же
    двумя разными фразами.
    """
    if usable_3d_path(raw):
        return ""
    if not raw:
        return "путь к папке не указан"
    return f"путь «{raw}» неполный — по нему не видно ни диска, ни папки"


def clean_extensions(raw) -> list[str]:
    """Расширения в том виде, в каком их отдаёт `classifier.extension_of`.

    То есть без точки, в нижнем регистре и без пустых записей. Приводить
    приходится потому, что в одном проекте живут три разных написания одного и
    того же: слова категорий пишутся с точкой (`".stl"`), `type_map` — без
    (`"stl"`), а `external_3d.extensions` сверяется с расширением файла, то есть
    тоже без. Написать `".stl"` в настройках после правки rules.json руками —
    самая обычная ошибка, и она была из тех, что не видно: сверка шла с
    расширением без точки, не совпадало ничего, и вынос 3D переставал выносить.
    Ни ошибки, ни предупреждения — путь-то годный, галочка стоит, план построен.

    Пустая строка выбрасывается по той же причине, что и пустое слово в
    `_rule_map`, только исход у неё другой: `extension_of` отдаёт пустую строку
    для файла без расширения, и такая запись увела бы во внешнюю папку 3D все
    файлы без расширения разом.
    """
    good: list[str] = []
    for value in raw:
        text = str(value).strip().lstrip(".").lower()
        if text and text not in good:
            good.append(text)
    return good


def _is_folder_name(value: str) -> bool:
    """Годится ли строка как имя папки внутри загрузок.

    Категория и тип уходят прямо в `root / категория / тип`, а `Path` устроен
    так, что абсолютный кусок отбрасывает всё слева: `Path("D:/Загрузки") /
    "C:/Windows/Temp"` — это просто `C:/Windows/Temp`. Значит, полный путь,
    вписанный вместо категории (опечатка, вставка не в то поле, правка руками),
    молча уносит файлы из загрузок совсем в другое место. `..` делает то же
    самое. Это худший исход из возможных: программа отчитывается об успешной
    сортировке, а файлов в загрузках больше нет и искать их негде.

    Вложенная категория (`Учёба/2026`) — обычное дело, её не трогаем.
    """
    parts = [p for p in _SEPARATORS.split(value) if p]
    if not parts or value[0] in "\\/":
        return False
    if ":" in parts[0]:  # буква диска
        return False
    return ".." not in parts and "." not in parts


def _read_json(path: Path, problems: list[str]) -> dict | None:
    """Читает JSON. При поломке возвращает None и дописывает жалобу в problems.

    Молча подставить пустоту нельзя: без правил всё уедет в Others, и это надо
    показать пользователю до того, как он нажмёт «Применить».
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"{path.name}: не читается ({exc}). Файл пропущен.")
        return None
    if not isinstance(data, dict):
        problems.append(f"{path.name}: ожидался объект JSON. Файл пропущен.")
        return None
    return data


def _clean_3d(raw, problems: list[str]) -> dict:
    """Приводит настройку внешней папки 3D к словарю с полным набором ключей.

    Читателей у этой настройки пятеро: планировщик, оба окна и две проверки
    пути. Раньше половина звала `.get` прямо, а половина сначала проверяла тип —
    и строка вместо объекта (`"external_3d": "C:/All_3d"` после правки руками)
    роняла программу на запуске, даже когда вынос 3D был выключен. Разбираемся
    с этим здесь, один раз, чтобы дальше все читатели имели дело со словарём.
    """
    if not isinstance(raw, dict):
        if raw:
            problems.append(
                "config.json: external_3d — не объект. Настройка 3D сброшена.")
        # Расширения нужны и здесь. Пустой словарь — это не только «правку
        # руками не разобрали», это ещё и обычный config.json нового
        # пользователя, где ключа `external_3d` нет вовсе. Такой словарь
        # доезжал до планировщика без списка расширений, и первая же попытка
        # включить вынос 3D прямо в окне — галочка, путь через «Обзор…» —
        # не выносила ни одного файла: совпадать было не с чем. Ни ошибки, ни
        # предупреждения: путь-то годный, `external_3d_warning` молчит. Само
        # чинилось только после закрытия и повторного открытия окна, потому
        # что `save` дописывает расширения на выходе. Ветка ниже давно
        # подставляет их для словаря без ключа — здесь было то же самое
        # упущение, только этажом выше.
        return {"extensions": list(DEFAULT_3D_EXTENSIONS)}
    data = dict(raw)
    extensions = data.get("extensions")
    cleaned = clean_extensions(extensions) if isinstance(extensions, list) else []
    if not cleaned:
        # Список был, но после чистки в нём не осталось ничего — значит внутри
        # лежали одни пустые строки. Молча взять список по умолчанию нельзя:
        # человек ограничивал вынос нарочно, а получил бы все расширения сразу.
        if isinstance(extensions, list) and extensions:
            problems.append(
                "config.json: external_3d.extensions — ни одного пригодного "
                "расширения. Взят список по умолчанию: "
                + ", ".join(DEFAULT_3D_EXTENSIONS) + ".")
        cleaned = list(DEFAULT_3D_EXTENSIONS)
    data["extensions"] = cleaned
    # Путь уходит и в `Path()`, и в поле ввода — обоим нужна строка. Проверка
    # самой настройки на «объект» тут не помогает: объект может быть правильный,
    # а путь внутри — числом после съехавшей замены в редакторе. Программа тогда
    # не открывалась вовсе, то есть починить настройку через окно уже нельзя.
    path = data.get("path")
    if path is not None and not isinstance(path, str):
        problems.append(
            "config.json: external_3d.path — не строка. Путь к папке 3D сброшен.")
        data["path"] = ""
    # Галочка стоит, а пути нет — и планировщик ведёт себя ровно так, будто
    # галочки тоже нет: `external_3d_path` без годного пути отдаёт None, и
    # модели едут в обычные категории. Снаружи это неотличимо от исправной
    # работы: настройка включена, план построен, жалоб никаких. Тот же исход,
    # что у забытого `extensions` парой строк выше, — настройка выглядит
    # рабочей, но не выносит ничего.
    #
    # Неполный путь до этой правки вёл себя иначе и хуже: он работал, но не
    # туда. Теперь он приравнен к «пути нет» — но сказать о нём надо отдельно,
    # потому что в поле ввода такой путь на вид совершенно исправен.
    reason = path_3d_reason(data.get("path"))
    if data.get("enabled") and reason:
        problems.append(
            f"config.json: вынос 3D включён, но {reason}. "
            "Модели поедут в обычные категории.")
    return data


def _rule_map(raw, where: str, key: str, problems: list[str]) -> dict[str, list[str]]:
    """Раздел вида «категория → список слов» (`categories`, `patterns`, `type_map`).

    Правила README предлагает править руками, а отсюда они уходят прямо в
    `.items()`: раздел списком вместо объекта ронял программу на первом же
    файле. Список слов строкой не ронял ничего — и это хуже: `"Медиа": "клип"`
    перебирается по буквам, каждая работает как ключевое слово, и в «Медиа»
    уезжает вообще всё. Молчаливую неверную раскладку замечают, когда файлы
    уже разложены, поэтому такой раздел выбрасываем с жалобой.

    Пустая строка внутри списка делает ровно то же самое и приходит тем же
    путём — недописанная правка, стёртое слово, список, собранный скриптом.
    `match_category` ищет вхождение подстроки, а пустая строка входит в любое
    имя: первая же категория с такой записью забирает себе всю папку загрузок.
    Отличить это от исправной работы нельзя ничем — план построен, файлы
    разложены, жалоб нет, и в предпросмотре у каждой строки честная пометка
    «слово», та самая, которой README велит доверять. У шаблонов пустое
    выражение подходит к любому имени и бьёт ещё раньше (они проверяются до
    слов), а в `type_map` пустая строка равна расширению файла без расширения
    и выдаёт таким файлам настоящий тип вместо запасного.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.append(f"{where}: {key} — не объект. Раздел пропущен.")
        return {}
    good: dict[str, list[str]] = {}
    for name, values in raw.items():
        if not isinstance(name, str):
            continue
        # Имя раздела — это имя папки: категория или тип. Путь вместо него
        # уводит файлы из загрузок (см. `_is_folder_name`).
        if not _is_folder_name(name):
            problems.append(
                f"{where}: {key} → «{name}» — это путь, а не имя папки. Пропущено.")
            continue
        if not isinstance(values, list):
            problems.append(f"{where}: {key} → «{name}» — не список. Пропущено.")
            continue
        kept: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            if not value:
                problems.append(
                    f"{where}: {key} → «{name}»: пустая строка вместо слова. "
                    "Она подходит к любому файлу — пропущена.")
                continue
            kept.append(value)
        good[name] = kept
    return good


def _pattern_map(raw, where: str, key: str, problems: list[str]) -> dict[str, list[str]]:
    """Раздел `patterns`: то же, что `_rule_map`, плюс проверка самих выражений.

    Регулярку пишут руками, и опечатка в ней — лишняя скобка, незакрытый класс,
    забытая фигурная скобка — не роняет ничего: `match_pattern` ловит `re.error`
    и идёт дальше. В этом и беда. Снаружи битое выражение выглядит как исправное
    правило, которое почему-то ни разу не сработало: скриншоты уезжают в Others,
    план построен, жалоб нет. Проверить нечем — пометку «шаблон» предпросмотр
    ставит только когда шаблон подошёл, а «не опознан» ничем не отличается от
    честного «такого правила и не было».

    Битое выражение поэтому выбрасываем здесь и говорим о нём вслух. Соседние
    выражения той же категории остаются: одна опечатка не должна уносить с собой
    работающие шаблоны. Проверку в `match_pattern` не снимаем — конфиг собирают
    и напрямую, из тестов и из CLI.
    """
    good = _rule_map(raw, where, key, problems)
    for name, expressions in good.items():
        checked: list[str] = []
        for expression in expressions:
            try:
                re.compile(expression)
            except re.error as exc:
                problems.append(
                    f"{where}: {key} → «{name}»: выражение «{expression}» "
                    f"не разбирается ({exc}). Пропущено.")
                continue
            checked.append(expression)
        good[name] = checked
    return good


def _text_map(
    raw, where: str, key: str, problems: list[str], folders: bool = False
) -> dict[str, str]:
    """Раздел вида «имя → строка» (`overrides`, `category_hints`).

    Категория из `overrides.json` уходит в `Path()`, и число вместо неё роняло
    построение плана целиком — и в окне, и в CLI.

    `folders=True` — значение станет именем папки (так у `overrides`). Тогда
    путь вместо категории отбраковывается: файл уехал бы из загрузок
    неизвестно куда, см. `_is_folder_name`. Подсказки категорий (`category_hints`)
    — обычный текст, там проверять нечего.

    Пустая категория тоже отбраковывается — и по той же причине, по какой
    отбраковывают битую регулярку: запись выглядит правилом, а правилом не
    является. `explain_category` берёт её через `or`, то есть проваливается
    дальше к шаблонам и словам, зато окно ИИ смотрит на само наличие ключа —
    и ответ модели про такое имя выбрасывает, «сберегая» несуществующее
    правило. Имя при этом в запрос уходит (`_has_rule` пустую строку правилом
    не считает): деньги платятся, ответ выбрасывается, окно отчитывается
    «свои правила сохранены», а файл остаётся неразобранным навсегда.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.append(f"{where}: {key} — не объект. Раздел пропущен.")
        return {}
    good: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, str):
            problems.append(f"{where}: запись «{name}» — не строка. Пропущена.")
            continue
        if folders and not value:
            problems.append(
                f"{where}: «{name}» → категория не названа. "
                "Такая запись ничего не решает. Пропущена.")
            continue
        if folders and not _is_folder_name(value):
            problems.append(
                f"{where}: «{name}» → «{value}» — это путь, а не категория. Пропущено.")
            continue
        good[name] = value
    return good


def _text_list(raw, where: str, key: str, problems: list[str]) -> list[str]:
    """Список строк (`managed_folders`, `ignore`). Не список — пустой список."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        problems.append(f"{where}: {key} — не список. Раздел пропущен.")
        return []
    return [v for v in raw if isinstance(v, str)]


def _text(raw, default: str, where: str, key: str, problems: list[str]) -> str:
    """Имя запасной папки (`fallback_category`/`fallback_type`).

    Сюда попадает всё, что не опознано, — то есть путь вместо запасной категории
    утащил бы из загрузок не один файл, а весь неопознанный хвост.
    """
    if raw is None:
        return default
    if not isinstance(raw, str) or not raw:
        problems.append(f"{where}: {key} — не строка. Взято «{default}».")
        return default
    if not _is_folder_name(raw):
        problems.append(
            f"{where}: {key} — это путь, а не имя папки. Взято «{default}».")
        return default
    return raw


def _check_managed(
    categories: list[str],
    types: list[str],
    managed: list[str],
    where: str,
    problems: list[str],
) -> None:
    """Жалуется на категории и типы, которых нет в `managed_folders`.

    Файл уезжает в `Загрузки/Категория/Тип`, но обратно программа заходит
    только в свои папки — те, чьё имя есть в `managed_folders`. Категория, эту
    строку не получившая, работает ровно один раз: файлы в неё складываются
    нормально, а дальше папка становится чёрной дырой. «Переразложить старое»
    её не видит, значит новые правила к лежащему внутри уже не применятся
    никогда. Опустевшей её тоже никто не уберёт.

    Заметить это невозможно: раскладка выглядит правильной, жалоб нет, а
    последствия вылезают через месяц и совсем в другом месте. Ровно этот исход
    README разбирает на переименованной `fallback_category` — там его починили
    поимённо, а общей проверки не было. Правила README предлагает править
    руками, и забыть вторую строку при добавлении категории — самая обычная
    ошибка: `categories` и `managed_folders` лежат в файле далеко друг от друга.

    Вложенная категория (`Учёба/2026`) — это две папки, и обход спускается по
    ним по очереди, поэтому нужны обе. Пустой `managed_folders` не трогаем: там
    ничего не забыли, там просто не пользуются разрешённым списком, и жалоба на
    каждую категорию завалила бы окно шумом.
    """
    if not managed:
        return
    known = folder_keys(managed)
    seen: set[str] = set()
    for kind, names in (("категория", categories), ("тип", types)):
        for name in names:
            for part in _SEPARATORS.split(name):
                key = folder_key(part)
                if not part or key in known or key in seen:
                    continue
                seen.add(key)
                problems.append(
                    f"{where}: {kind} «{part}» не указана в managed_folders. "
                    "Файлы в неё разложатся, но «Переразложить старое» в эту "
                    "папку больше не зайдёт и пустой её не уберёт.")


def _check_type_map(
    type_map: dict[str, list[str]], where: str, problems: list[str]
) -> None:
    """Жалуется на расширение, названное сразу в двух типах.

    `match_type` отдаёт первый подошедший тип, значит вторая запись не работает
    никогда. Снаружи она выглядит как обычное правило: строка в файле есть,
    ошибок нет, а файл ложится в другую подпапку — и понять, почему `.exr`
    оказался в `Images`, если в `type_map["3D"]` он тоже написан, можно только
    зная про этот порядок. Ровно тот же случай, что у битой регулярки: запись
    похожа на рабочее правило, но правилом не является, и молчание тут хуже
    жалобы. В поставляемых правилах такая запись и нашлась.

    Регистр не важен, как и в самом `match_type`: `"PDF"` и `"pdf"` — одно
    расширение, и написать их в разных типах так же легко.
    """
    seen: dict[str, str] = {}
    for type_name, extensions in type_map.items():
        for value in extensions:
            key = str(value).lower()
            if key in seen:
                if seen[key] != type_name:
                    problems.append(
                        f"{where}: расширение «{value}» названо и в типе "
                        f"«{seen[key]}», и в «{type_name}». Работает только "
                        f"первый — «{seen[key]}».")
            else:
                seen[key] = type_name


@dataclass
class Config:
    downloads_path: str
    categories: dict[str, list[str]] = field(default_factory=dict)
    patterns: dict[str, list[str]] = field(default_factory=dict)
    category_hints: dict[str, str] = field(default_factory=dict)
    type_map: dict[str, list[str]] = field(default_factory=dict)
    managed_folders: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    overrides: dict[str, str] = field(default_factory=dict)
    external_3d: dict = field(default_factory=dict)
    fallback_category: str = "Others"
    fallback_type: str = "Misc"
    extra: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        """Читает настройки из `path` и правила из `rules.json` рядом.

        Если `rules.json` нет — это конфиг старой версии, где правила лежали
        вместе с настройками. Тогда берём их оттуда, чтобы программа не
        осталась вовсе без категорий.

        Испорченный файл не мешает запуску: о нём пишем в `problems`, а
        работаем с тем, что есть. Это касается и самого `config.json` — его
        программа переписывает при каждом закрытии окна, и оборванная запись
        (нет места, выключили питание) не должна превращать её в кирпич со
        стеком вместо окна. Кирпич хуже вдвойне: поправить путь через интерфейс
        уже не выйдет, потому что интерфейс не открывается.
        """
        path = Path(path)
        problems: list[str] = []
        data = _read_json(path, problems) or {}

        downloads = data.get("downloads_path")
        if not isinstance(downloads, str) or not downloads:
            problems.append(
                "config.json: папка загрузок не указана. "
                f"Взята папка по умолчанию: {DEFAULT_DOWNLOADS}")
            downloads = DEFAULT_DOWNLOADS

        rules_path = path.with_name(RULES_FILENAME)
        rules = _read_json(rules_path, problems) if rules_path.exists() else None
        # Правила лежат в самом config.json — старая установка. Тогда они и есть
        # незнакомые ключи: выбросить их при сохранении значит стереть
        # единственную копию, взять её обратно неоткуда.
        inline_rules = rules is None
        if rules is None:
            rules = data

        rules_name = path.name if inline_rules else RULES_FILENAME
        overrides_name = path.with_name(OVERRIDES_FILENAME).name
        skip = USER_KEYS if inline_rules else RULE_KEYS + USER_KEYS

        # Разделы разбираются по порядку: жалобы в `problems` должны идти в том
        # же порядке, в каком они лежат в файле, — так их проще искать глазами.
        categories = _rule_map(rules.get("categories"), rules_name, "categories", problems)
        patterns = _pattern_map(rules.get("patterns"), rules_name, "patterns", problems)
        category_hints = _text_map(
            rules.get("category_hints"), rules_name, "category_hints", problems)
        type_map = _rule_map(rules.get("type_map"), rules_name, "type_map", problems)
        managed_folders = _text_list(
            rules.get("managed_folders"), rules_name, "managed_folders", problems)
        ignore = _text_list(rules.get("ignore"), rules_name, "ignore", problems)
        overrides = _text_map(
            _read_json(path.with_name(OVERRIDES_FILENAME), problems),
            overrides_name, "правила", problems, folders=True)
        external_3d = _clean_3d(data.get("external_3d"), problems)
        fallback_category = _text(
            rules.get("fallback_category"), "Others",
            rules_name, "fallback_category", problems)
        fallback_type = _text(
            rules.get("fallback_type"), "Misc", rules_name, "fallback_type", problems)

        # Раскладывать не по чему. Снаружи это выглядит как обычная работа:
        # план построен, файлы разложены, жалоб нет — только все до одного
        # уехали в запасную папку, и отличить это от честно неопознанных
        # загрузок нельзя ничем.
        #
        # Дорога сюда короткая: `rules.json` лежит рядом с программой отдельным
        # файлом, и пропасть ему просто — из архива скопировали один `.exe`,
        # антивирус унёс файл в карантин, установку перенесли наполовину. Тогда
        # `Config` считает конфиг старым (правила раньше лежали в config.json) и
        # молча берёт правила оттуда, где их нет. Пустой при этом и
        # `managed_folders`, то есть «Переразложить старое» в запасную папку не
        # зайдёт и разгрести не поможет, пока файл правил не вернётся на место.
        #
        # Раскладка на одних шаблонах или одних ручных правилах — настройка
        # рабочая, поэтому жалуемся, только когда пусто везде.
        if not categories and not patterns and not overrides:
            missing = ("" if rules_path.exists()
                       else f" Файла {RULES_FILENAME} рядом с настройками нет.")
            problems.append(
                "Правил раскладки нет ни одного: ни категорий, ни шаблонов, ни "
                f"ручных правил. Все файлы уедут в «{fallback_category}»."
                + missing)

        # Расширение, названное в двух типах, работает только в первом. Вторая
        # запись — правило-призрак: на вид рабочая, на деле мёртвая.
        _check_type_map(type_map, rules_name, problems)

        # Папку создаёт любая категория, откуда бы она ни пришла: из правил, из
        # шаблона, из ручной записи в overrides.json или из запасной строки.
        _check_managed(
            [*categories, *patterns, *overrides.values(), fallback_category],
            [*type_map, fallback_type],
            managed_folders, rules_name, problems)

        return cls(
            downloads_path=downloads,
            categories=categories,
            patterns=patterns,
            category_hints=category_hints,
            type_map=type_map,
            managed_folders=managed_folders,
            ignore=ignore,
            overrides=overrides,
            external_3d=external_3d,
            fallback_category=fallback_category,
            fallback_type=fallback_type,
            extra={k: v for k, v in data.items() if k not in skip},
            problems=problems,
        )

    def save(self, path: str | Path) -> None:
        """Пишет только настройки пользователя. Правила не трогает.

        Незнакомые ключи из файла возвращаются на место: если кто-то дописал
        своё в config.json, сохранение из окна не должно это стирать.

        Список расширений 3D дописывается сам: окно про него не знает и знать не
        должно, а без него настройка выглядит рабочей, но не делает ничего.

        В конце файла — перевод строки. Без него закрытие окна каждый раз
        превращало config.json в изменённый файл: содержимое то же, а `git diff`
        показывает «\\ No newline at end of file». В репозитории программы это
        шум при каждом запуске, а редакторы и консольные утилиты последнюю
        строку без перевода показывают склеенной со следующей.
        """
        data = {
            **self.extra,
            "downloads_path": self.downloads_path,
            "external_3d": _clean_3d(self.external_3d, []) if self.external_3d else {},
        }
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
