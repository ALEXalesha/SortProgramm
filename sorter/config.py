"""Конфигурация сортировщика: категории, карта типов, защищённые папки."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    downloads_path: str
    categories: dict[str, list[str]] = field(default_factory=dict)
    patterns: dict[str, list[str]] = field(default_factory=dict)
    category_hints: dict[str, str] = field(default_factory=dict)
    type_map: dict[str, list[str]] = field(default_factory=dict)
    managed_folders: list[str] = field(default_factory=list)
    protected_folders: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    overrides: dict[str, str] = field(default_factory=dict)
    external_3d: dict = field(default_factory=dict)
    fallback_category: str = "Others"
    fallback_type: str = "Misc"
    folder_bucket: str = "_Папки"

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        overrides_path = path.with_name("overrides.json")
        overrides = {}
        if overrides_path.exists():
            overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
        return cls(
            downloads_path=data["downloads_path"],
            categories=data.get("categories", {}),
            patterns=data.get("patterns", {}),
            category_hints=data.get("category_hints", {}),
            type_map=data.get("type_map", {}),
            managed_folders=data.get("managed_folders", []),
            protected_folders=data.get("protected_folders", []),
            ignore=data.get("ignore", []),
            overrides=overrides,
            external_3d=data.get("external_3d", {}),
            fallback_category=data.get("fallback_category", "Others"),
            fallback_type=data.get("fallback_type", "Misc"),
            folder_bucket=data.get("folder_bucket", "_Папки"),
        )

    def save(self, path: str | Path) -> None:
        """Пишет config.json (без overrides — они в отдельном файле)."""
        data = {
            "downloads_path": self.downloads_path,
            "categories": self.categories,
            "patterns": self.patterns,
            "category_hints": self.category_hints,
            "type_map": self.type_map,
            "managed_folders": self.managed_folders,
            "protected_folders": self.protected_folders,
            "ignore": self.ignore,
            "external_3d": self.external_3d,
            "fallback_category": self.fallback_category,
            "fallback_type": self.fallback_type,
            "folder_bucket": self.folder_bucket,
        }
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
