"""Классификация целых папок из корня загрузок.

Папка едет одним куском. Мир Minecraft, git-репозиторий или мод BepInEx
бессмысленно разбирать по файлам: без соседей эти файлы мусор, а сама папка —
осмысленная единица. Поэтому здесь нет ни типов, ни расширений — только
категория для всей папки целиком.

Категорию определяем по маркерам внутри (`level.dat` → мир Minecraft), и лишь
если маркеров нет — по имени, теми же правилами, что и для файлов. Маркеры
надёжнее имени: папку можно назвать `fluga` или `latest`, но `level.dat` внутри
врать не станет.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from .classifier import match_category, match_pattern
from .config import Config

# Насколько глубоко заглядываем внутрь папки, чтобы найти маркеры. Двух уровней
# хватает: `games_pygame/games/*.py` прячет питон на втором, глубже маркеры
# формата уже не живут. Рекурсию не запускаем — папка с 3000 файлов
# (ресурс-пак Minecraft) не должна стоить полного обхода.
SURFACE_DEPTH = 2
SURFACE_LIMIT = 600


@dataclass(frozen=True)
class Signature:
    """Признак: какие имена внутри папки выдают её содержимое.

    markers — точные имена (регистр не важен), globs — маски (`*.blend`).
    Срабатывает любое совпадение: маркеры и маски равноправны.
    """

    category: str
    label: str
    markers: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()

    def matches(self, names: set[str]) -> bool:
        if any(marker.lower() in names for marker in self.markers):
            return True
        return any(
            fnmatch.fnmatch(name, glob.lower()) for glob in self.globs for name in names
        )


# Сильные подписи: маркер однозначно задаёт формат. `pack.mcmeta` бывает только
# у пака Minecraft, `.git` — только у репозитория. Такому свидетельству верим
# сразу, даже если имя папки говорит другое.
#
# Порядок внутри задаёт приоритет: первая подошедшая выигрывает.
STRONG_SIGNATURES: tuple[Signature, ...] = (
    Signature("Игры", "мир Minecraft", markers=("level.dat", "level.dat_old")),
    Signature("Игры", "пак Minecraft", markers=("pack.mcmeta", "pack.png")),
    Signature("Игры", "мод BepInEx", markers=("bepinex", "doorstop_config.ini", "winhttp.dll")),
    Signature("Игры", "мод Minecraft Forge", markers=("forge.jar", "options.txt")),
    Signature("Код", "git-репозиторий", markers=(".git",)),
    Signature("Код", "проект Node", markers=("package.json", "node_modules")),
    Signature(
        "Код",
        "проект Python",
        markers=("pyproject.toml", "requirements.txt", "setup.py"),
        globs=("*.py",),
    ),
    Signature(
        "Электроника",
        "прошивка/скетч",
        markers=("platformio.ini",),
        globs=("*.ino", "*.hex", "*.uf2"),
    ),
    Signature("3D", "3D-модели", globs=("*.blend", "*.gcode", "*.3mf", "*.stl", "*.obj")),
    Signature("Нейросети", "модели ИИ", globs=("*.safetensors", "*.gguf", "*.ckpt", "*.onnx")),
    Signature("Дизайн", "исходники дизайна", globs=("*.psd", "*.fig", "*.afdesign")),
)

# Слабые подписи: маркер говорит лишь «здесь какая-то распакованная программа».
# `manifest.json` и `register.cmd` есть у сотни разных приложений, поэтому они
# идут ПОСЛЕ ключевых слов имени — иначе `WinBox_Windows` с папкой `assets`
# внутри объявлялся бы паком Minecraft, а осмысленное имя игнорировалось.
WEAK_SIGNATURES: tuple[Signature, ...] = (
    Signature("Учёба", "учебные документы", globs=("*.docx", "*.pptx", "*.odt")),
    Signature(
        "Программы",
        "распакованная программа",
        markers=("uninstall.exe", "unins000.exe", "register.cmd", "manifest.json"),
        globs=("*.exe", "*.msi"),
    ),
)

DEFAULT_SIGNATURES = STRONG_SIGNATURES + WEAK_SIGNATURES


@dataclass
class FolderVerdict:
    """Куда и почему едет папка. `reason` показываем в предпросмотре."""

    folder: Path
    category: str
    reason: str
    tags: list[str] = field(default_factory=list)


def _surface_names(folder: Path, depth: int = SURFACE_DEPTH, limit: int = SURFACE_LIMIT) -> set[str]:
    """Имена в нижнем регистре с верхних уровней папки.

    Обход в ширину с потолком по числу имён: нам нужны признаки формата, а не
    полная опись. Ошибки доступа глотаем — папка могла уехать или быть занята.
    """
    names: set[str] = set()
    level = [folder]
    for _ in range(depth):
        following: list[Path] = []
        for current in level:
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                names.add(entry.name.lower())
                if entry.is_dir():
                    following.append(entry)
                if len(names) >= limit:
                    return names
        level = following
        if not level:
            break
    return names


def classify_folder(
    folder: Path,
    config: Config,
    strong: tuple[Signature, ...] = STRONG_SIGNATURES,
    weak: tuple[Signature, ...] = WEAK_SIGNATURES,
) -> FolderVerdict:
    """Категория для папки целиком.

    Приоритет: overrides → сильные маркеры → регулярки → ключевые слова →
    слабые маркеры → fallback.

    Overrides наверху, чтобы ручное решение (в том числе от ИИ) всегда било
    автоматику. Сильные маркеры выше имени, потому что `level.dat` честнее
    названия `fluga`. Слабые — ниже имени, потому что «внутри лежит .exe»
    не спорит с осмысленным названием.
    """
    override = config.overrides.get(folder.name)
    if override:
        return FolderVerdict(folder, override, "правило")

    names = _surface_names(folder)
    for signature in strong:
        if signature.matches(names):
            return FolderVerdict(folder, signature.category, signature.label)

    by_pattern = match_pattern(folder.name, config.patterns)
    if by_pattern:
        return FolderVerdict(folder, by_pattern, "имя (шаблон)")

    by_name = match_category(folder.name, "", config.categories)
    if by_name:
        return FolderVerdict(folder, by_name, "имя")

    for signature in weak:
        if signature.matches(names):
            return FolderVerdict(folder, signature.category, signature.label)

    return FolderVerdict(folder, config.fallback_category, "не опознана")


def is_sortable(folder: Path, config: Config) -> bool:
    """Можно ли трогать эту папку.

    Не трогаем: скрытые и служебные (`.git`, `.sorter`), сами папки-категории
    (`Игры`, `Код` — они и есть назначение) и защищённые из конфига. В
    `protected_folders` живут папки с чужим состоянием: `Telegram Desktop` —
    рабочий каталог загрузок Telegram, переезд сломает настройку мессенджера.
    """
    if not folder.is_dir():
        return False
    if folder.name.startswith("."):
        return False
    if folder.name in set(config.managed_folders):
        return False
    return folder.name not in set(config.protected_folders)


def scan_folders(root: str | Path, config: Config) -> list[Path]:
    """Папки в корне загрузок, которые разрешено сортировать."""
    root = Path(root)
    if not root.is_dir():
        return []
    return [entry for entry in sorted(root.iterdir()) if is_sortable(entry, config)]
