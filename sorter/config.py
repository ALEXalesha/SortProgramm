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
        if rules is None:
            rules = data

        overrides = _read_json(path.with_name(OVERRIDES_FILENAME), problems) or {}

        return cls(
            downloads_path=downloads,
            categories=rules.get("categories", {}),
            patterns=rules.get("patterns", {}),
            category_hints=rules.get("category_hints", {}),
            type_map=rules.get("type_map", {}),
            managed_folders=rules.get("managed_folders", []),
            ignore=rules.get("ignore", []),
            overrides=overrides,
            external_3d=_clean_3d(data.get("external_3d"), problems),
            fallback_category=rules.get("fallback_category", "Others"),
            fallback_type=rules.get("fallback_type", "Misc"),
            extra={k: v for k, v in data.items() if k not in RULE_KEYS + USER_KEYS},
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
