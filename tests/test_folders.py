"""Классификация и раскладка целых папок из корня загрузок."""
from pathlib import Path

from sorter.config import Config
from sorter.folders import classify_folder, is_sortable, scan_folders
from sorter.planner import plan_folders


def make_config(root: Path) -> Config:
    return Config(
        downloads_path=str(root),
        # Порядок как в config.json: Электроника раньше Программы, иначе слово
        # "driver" забрало бы драйвер платы CH340 себе.
        categories={
            "Игры": ["minecraft", "pvp"],
            "Электроника": ["ch340", "espy"],
            "Программы": ["setup", "driver"],
        },
        patterns={"Скриншоты": [r"^screenshot"]},
        managed_folders=["Игры", "Программы", "Электроника", "Others"],
        protected_folders=["Telegram Desktop"],
        fallback_category="Others",
        folder_bucket="_Папки",
    )


def make_folder(root: Path, name: str, children: list[str]) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    for child in children:
        target = folder / child
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    return folder


# --- определение по маркерам внутри ---


def test_minecraft_world_detected_by_level_dat(tmp_path):
    """Папка называется `fluga` — по имени не понять. Внутри level.dat."""
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "fluga", ["level.dat", "region/r.0.0.mca"])
    verdict = classify_folder(folder, cfg)
    assert verdict.category == "Игры"
    assert verdict.reason == "мир Minecraft"


def test_git_repo_goes_to_code(tmp_path):
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "claude-usage", [".git/HEAD", "README.md"])
    assert classify_folder(folder, cfg).category == "Код"


def test_bepinex_mod_goes_to_games(tmp_path):
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "MalumMenu_v3.0.2_Full", ["BepInEx/core.dll", "winhttp.dll"])
    assert classify_folder(folder, cfg).category == "Игры"


def test_python_project_found_two_levels_deep(tmp_path):
    """`games_pygame/games/*.py` — маркер на втором уровне, не в корне папки."""
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "games_pygame", ["games/snake.py", "games/tetris.py"])
    assert classify_folder(folder, cfg).category == "Код"


def test_firmware_folder_goes_to_electronics(tmp_path):
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "blink", ["platformio.ini", "src/main.ino"])
    assert classify_folder(folder, cfg).category == "Электроника"


def test_marker_beats_misleading_name(tmp_path):
    """Имя говорит «setup» (Программы), но внутри git-репозиторий."""
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "setup-tools", [".git/HEAD"])
    assert classify_folder(folder, cfg).category == "Код"


# --- запасные пути: имя, шаблон, override ---


def test_falls_back_to_name_keywords(tmp_path):
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "driver_ch340_341", ["readme.txt"])
    verdict = classify_folder(folder, cfg)
    assert verdict.category == "Электроника"  # ch340 раньше driver в порядке категорий
    assert verdict.reason == "имя"


def test_pattern_matches_folder_name(tmp_path):
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "screenshot-dump", ["a.png"])
    assert classify_folder(folder, cfg).category == "Скриншоты"


def test_override_wins_over_markers(tmp_path):
    cfg = make_config(tmp_path)
    cfg.overrides = {"fluga": "Учёба"}
    folder = make_folder(tmp_path, "fluga", ["level.dat"])
    verdict = classify_folder(folder, cfg)
    assert verdict.category == "Учёба"
    assert verdict.reason == "правило"


def test_generic_assets_folder_is_not_a_minecraft_pack(tmp_path):
    """WinBox — утилита MikroTik. Папка `assets` внутри есть у сотни программ."""
    cfg = make_config(tmp_path)
    cfg.categories = {"Электроника": ["winbox"], "Игры": ["minecraft"]}
    folder = make_folder(tmp_path, "WinBox_Windows", ["assets/x.dat", "WinBox.exe"])
    assert classify_folder(folder, cfg).category == "Электроника"


def test_name_keyword_beats_weak_signature(tmp_path):
    """Слабый маркер «внутри .exe» не должен спорить с осмысленным именем."""
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "minecraft-tools", ["run.exe", "manifest.json"])
    verdict = classify_folder(folder, cfg)
    assert verdict.category == "Игры"
    assert verdict.reason == "имя"


def test_weak_signature_used_when_name_says_nothing(tmp_path):
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "Release_x64", ["register.cmd", "lib.dll"])
    verdict = classify_folder(folder, cfg)
    assert verdict.category == "Программы"
    assert verdict.reason == "распакованная программа"


def test_unknown_folder_goes_to_fallback(tmp_path):
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "7C96v1L1", ["7C96v1x.txt"])
    verdict = classify_folder(folder, cfg)
    assert verdict.category == "Others"
    assert verdict.reason == "не опознана"


# --- что трогать нельзя ---


def test_hidden_folders_are_skipped(tmp_path):
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, ".sorter", ["undo_1.json"])
    assert not is_sortable(folder, cfg)


def test_category_folders_are_skipped(tmp_path):
    """`Игры` — это назначение, а не груз. Иначе папка уехала бы в саму себя."""
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "Игры", ["Installers/a.exe"])
    assert not is_sortable(folder, cfg)


def test_protected_folders_are_skipped(tmp_path):
    """Telegram пишет в свою папку — переезд сломает настройку мессенджера."""
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "Telegram Desktop", ["photo.jpg"])
    assert not is_sortable(folder, cfg)


def test_scan_folders_returns_only_sortable(tmp_path):
    cfg = make_config(tmp_path)
    make_folder(tmp_path, "fluga", ["level.dat"])
    make_folder(tmp_path, ".sorter", ["undo_1.json"])
    make_folder(tmp_path, "Игры", ["a.exe"])
    make_folder(tmp_path, "Telegram Desktop", ["b.jpg"])
    (tmp_path / "loose.exe").write_text("x", encoding="utf-8")
    assert [f.name for f in scan_folders(tmp_path, cfg)] == ["fluga"]


# --- план ---


def test_plan_folders_targets_bucket(tmp_path):
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "fluga", ["level.dat"])
    moves = plan_folders([folder], cfg)
    assert len(moves) == 1
    assert moves[0].dst == tmp_path / "Игры" / "_Папки" / "fluga"
    assert moves[0].note == "мир Minecraft"


def test_plan_folders_dedup_keeps_dotted_name_intact(tmp_path):
    """У папки нет расширения: номер должен уйти в конец, а не перед `.2`."""
    cfg = make_config(tmp_path)
    folder = make_folder(tmp_path, "zapret-1.9.2", ["readme.txt"])
    occupied = tmp_path / "Others" / "_Папки" / "zapret-1.9.2"
    occupied.mkdir(parents=True)
    moves = plan_folders([folder], cfg)
    assert moves[0].dst.name == "zapret-1.9.2 (1)"


def test_plan_folders_skips_folder_already_in_place(tmp_path):
    cfg = make_config(tmp_path)
    settled = tmp_path / "Игры" / "_Папки" / "fluga"
    settled.mkdir(parents=True)
    (settled / "level.dat").write_text("x", encoding="utf-8")
    assert plan_folders([settled], cfg) == []
