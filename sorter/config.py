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

        Испорченный вспомогательный файл не мешает запуску: о нём пишем в
        `problems`, а работаем с тем, что есть. `overrides.json` программа пишет
        сама, и оборванная запись (нет места, выключили питание) не должна
        превращать её в кирпич со стеком вместо окна.
        """
        path = Path(path)
        problems: list[str] = []
        data = json.loads(path.read_text(encoding="utf-8"))

        rules_path = path.with_name(RULES_FILENAME)
        rules = _read_json(rules_path, problems) if rules_path.exists() else None
        if rules is None:
            rules = data

        overrides = _read_json(path.with_name(OVERRIDES_FILENAME), problems) or {}

        return cls(
            downloads_path=data["downloads_path"],
            categories=rules.get("categories", {}),
            patterns=rules.get("patterns", {}),
            category_hints=rules.get("category_hints", {}),
            type_map=rules.get("type_map", {}),
            managed_folders=rules.get("managed_folders", []),
            ignore=rules.get("ignore", []),
            overrides=overrides,
            external_3d=data.get("external_3d", {}),
            fallback_category=rules.get("fallback_category", "Others"),
            fallback_type=rules.get("fallback_type", "Misc"),
            extra={k: v for k, v in data.items() if k not in RULE_KEYS + USER_KEYS},
            problems=problems,
        )

    def save(self, path: str | Path) -> None:
        """Пишет только настройки пользователя. Правила не трогает.

        Незнакомые ключи из файла возвращаются на место: если кто-то дописал
        своё в config.json, сохранение из окна не должно это стирать.
        """
        data = {
            **self.extra,
            "downloads_path": self.downloads_path,
            "external_3d": self.external_3d,
        }
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
