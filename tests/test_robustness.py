"""Устойчивость к испорченным файлам и странным настройкам."""
import json

from sorter.config import Config
from sorter.planner import build_plan

USER = {"downloads_path": "X:/загрузки"}
RULES = {"categories": {"Медиа": ["клип"]}, "managed_folders": ["Медиа"]}


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


# --- испорченные файлы не мешают запуску ---


def test_broken_overrides_does_not_block_startup(tmp_path):
    """overrides.json пишет сама программа: оборванная запись реальна."""
    cfg_path = write(tmp_path / "config.json", json.dumps(USER))
    write(tmp_path / "rules.json", json.dumps(RULES))
    write(tmp_path / "overrides.json", "{это не json")

    cfg = Config.load(cfg_path)

    assert cfg.overrides == {}
    assert cfg.categories == RULES["categories"], "правила должны уцелеть"
    assert any("overrides.json" in p for p in cfg.problems)


def test_broken_rules_does_not_block_startup(tmp_path):
    cfg_path = write(tmp_path / "config.json", json.dumps(USER))
    write(tmp_path / "rules.json", "{сломано")

    cfg = Config.load(cfg_path)

    assert cfg.downloads_path == "X:/загрузки"
    assert any("rules.json" in p for p in cfg.problems)


def test_json_array_instead_of_object_is_reported(tmp_path):
    cfg_path = write(tmp_path / "config.json", json.dumps(USER))
    write(tmp_path / "overrides.json", "[1, 2, 3]")
    cfg = Config.load(cfg_path)
    assert cfg.overrides == {}
    assert cfg.problems


def test_healthy_config_has_no_problems(tmp_path):
    cfg_path = write(tmp_path / "config.json", json.dumps(USER))
    write(tmp_path / "rules.json", json.dumps(RULES))
    write(tmp_path / "overrides.json", json.dumps({"a.mp4": "Медиа"}))
    cfg = Config.load(cfg_path)
    assert cfg.problems == []
    assert cfg.overrides == {"a.mp4": "Медиа"}


# --- внешняя папка 3D указывает на саму папку загрузок ---


def test_external_3d_equal_to_downloads_plans_each_file_once(tmp_path):
    """Иначе первое перемещение пройдёт, а второе упадёт с «нет файла»."""
    cfg = Config(
        downloads_path=str(tmp_path),
        categories={},
        type_map={"3D": ["stl"]},
        managed_folders=["Others", "3D", "Misc"],
        external_3d={"enabled": True, "path": str(tmp_path), "extensions": ["stl"]},
        fallback_category="Others",
        fallback_type="Misc",
    )
    (tmp_path / "деталь.stl").write_text("x", encoding="utf-8")

    moves = build_plan(cfg, send_3d_external=True)

    sources = [m.src for m in moves]
    assert len(sources) == len(set(sources)), f"файл запланирован дважды: {sources}"
