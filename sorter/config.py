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
        return {}
    data = dict(raw)
    extensions = data.get("extensions")
    if not isinstance(extensions, list) or not extensions:
        data["extensions"] = list(DEFAULT_3D_EXTENSIONS)
    # Путь уходит и в `Path()`, и в поле ввода — обоим нужна строка. Проверка
    # самой настройки на «объект» тут не помогает: объект может быть правильный,
    # а путь внутри — числом после съехавшей замены в редакторе. Программа тогда
    # не открывалась вовсе, то есть починить настройку через окно уже нельзя.
    path = data.get("path")
    if path is not None and not isinstance(path, str):
        problems.append(
            "config.json: external_3d.path — не строка. Путь к папке 3D сброшен.")
        data["path"] = ""
    return data


def _rule_map(raw, where: str, key: str, problems: list[str]) -> dict[str, list[str]]:
    """Раздел вида «категория → список слов» (`categories`, `patterns`, `type_map`).

    Правила README предлагает править руками, а отсюда они уходят прямо в
    `.items()`: раздел списком вместо объекта ронял программу на первом же
    файле. Список слов строкой не ронял ничего — и это хуже: `"Медиа": "клип"`
    перебирается по буквам, каждая работает как ключевое слово, и в «Медиа»
    уезжает вообще всё. Молчаливую неверную раскладку замечают, когда файлы
    уже разложены, поэтому такой раздел выбрасываем с жалобой.
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
        if not isinstance(values, list):
            problems.append(f"{where}: {key} → «{name}» — не список. Пропущено.")
            continue
        good[name] = [v for v in values if isinstance(v, str)]
    return good


def _text_map(raw, where: str, key: str, problems: list[str]) -> dict[str, str]:
    """Раздел вида «имя → строка» (`overrides`, `category_hints`).

    Категория из `overrides.json` уходит в `Path()`, и число вместо неё роняло
    построение плана целиком — и в окне, и в CLI.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.append(f"{where}: {key} — не объект. Раздел пропущен.")
        return {}
    good: dict[str, str] = {}
    for name, value in raw.items():
        if isinstance(name, str) and isinstance(value, str):
            good[name] = value
        else:
            problems.append(f"{where}: запись «{name}» — не строка. Пропущена.")
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
    """Строковая настройка с запасным значением (`fallback_category`/`_type`)."""
    if raw is None:
        return default
    if not isinstance(raw, str) or not raw:
        problems.append(f"{where}: {key} — не строка. Взято «{default}».")
        return default
    return raw


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

        return cls(
            downloads_path=downloads,
            categories=_rule_map(rules.get("categories"), rules_name, "categories", problems),
            patterns=_rule_map(rules.get("patterns"), rules_name, "patterns", problems),
            category_hints=_text_map(
                rules.get("category_hints"), rules_name, "category_hints", problems),
            type_map=_rule_map(rules.get("type_map"), rules_name, "type_map", problems),
            managed_folders=_text_list(
                rules.get("managed_folders"), rules_name, "managed_folders", problems),
            ignore=_text_list(rules.get("ignore"), rules_name, "ignore", problems),
            overrides=_text_map(
                _read_json(path.with_name(OVERRIDES_FILENAME), problems),
                overrides_name, "правила", problems),
            external_3d=_clean_3d(data.get("external_3d"), problems),
            fallback_category=_text(
                rules.get("fallback_category"), "Others",
                rules_name, "fallback_category", problems),
            fallback_type=_text(
                rules.get("fallback_type"), "Misc", rules_name, "fallback_type", problems),
            extra={k: v for k, v in data.items() if k not in skip},
            problems=problems,
        )

    def save(self, path: str | Path) -> None:
        """Пишет только настройки пользователя. Правила не трогает.

        Незнакомые ключи из файла возвращаются на место: если кто-то дописал
        своё в config.json, сохранение из окна не должно это стирать.

        Список расширений 3D дописывается сам: окно про него не знает и знать не
        должно, а без него настройка выглядит рабочей, но не делает ничего.
        """
        data = {
            **self.extra,
            "downloads_path": self.downloads_path,
            "external_3d": _clean_3d(self.external_3d, []) if self.external_3d else {},
        }
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
