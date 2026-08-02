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


def test_external_3d_path_not_a_string_does_not_crash(tmp_path):
    """`"path": 123` вместо строки роняло планирование и окно на запуске.

    Тип самой настройки `external_3d` уже проверяется, а вот путь внутри неё —
    нет: он уходил прямо в `Path()` и в поле ввода. Правка руками, съехавшая
    замена в редакторе — и программа не открывается вовсе.
    """
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    cfg_path = write(tmp_path / "config.json", json.dumps({
        "downloads_path": str(downloads),
        "external_3d": {"enabled": True, "path": 123},
    }))

    cfg = Config.load(cfg_path)

    assert cfg.external_3d["path"] == ""
    assert any("external_3d" in p for p in cfg.problems)
    assert build_plan(cfg, send_3d_external=True) == []


# --- правка руками: не тот тип внутри файла ---


def test_override_category_not_a_string_does_not_crash(tmp_path):
    """`"отчёт.pdf": 3` вместо категории роняло построение плана целиком.

    Правила в `overrides.json` README предлагает писать руками, а значение
    оттуда уходит прямо в `Path()`. Число, список, пропущенная кавычка — и
    вместо плана трассировка, причём и в окне, и в CLI.
    """
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "отчёт.pdf").write_text("x", encoding="utf-8")
    cfg_path = write(tmp_path / "config.json",
                     json.dumps({"downloads_path": str(downloads)}))
    write(tmp_path / "rules.json", json.dumps(RULES))
    write(tmp_path / "overrides.json", json.dumps({"отчёт.pdf": 3}))

    cfg = Config.load(cfg_path)

    assert cfg.overrides == {}, "правило с нестроковой категорией надо выбросить"
    assert any("overrides.json" in p for p in cfg.problems)
    assert build_plan(cfg)


def test_rule_section_of_wrong_type_does_not_crash(tmp_path):
    """`categories` списком вместо словаря — AttributeError на первом же файле."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "клип.mp4").write_text("x", encoding="utf-8")
    cfg_path = write(tmp_path / "config.json",
                     json.dumps({"downloads_path": str(downloads)}))
    write(tmp_path / "rules.json", json.dumps({"categories": ["Медиа"]}))

    cfg = Config.load(cfg_path)

    assert cfg.categories == {}
    assert any("categories" in p for p in cfg.problems)
    assert build_plan(cfg)


def test_keywords_written_as_string_do_not_match_every_letter(tmp_path):
    """`"Медиа": "клип"` вместо списка — это перебор букв, а не слово.

    Программа не падала, было хуже: каждая буква работала как ключевое слово,
    и в «Медиа» уезжало всё подряд. Молчаливая неверная раскладка страшнее
    ошибки — её замечают, когда файлы уже разложены.
    """
    cfg_path = write(tmp_path / "config.json", json.dumps(USER))
    write(tmp_path / "rules.json", json.dumps({"categories": {"Медиа": "клип"}}))

    cfg = Config.load(cfg_path)

    assert cfg.categories == {}
    assert any("Медиа" in p for p in cfg.problems)


def test_fallback_category_not_a_string_falls_back_to_others(tmp_path):
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "файл.xyz").write_text("x", encoding="utf-8")
    cfg_path = write(tmp_path / "config.json",
                     json.dumps({"downloads_path": str(downloads)}))
    write(tmp_path / "rules.json", json.dumps({"fallback_category": 5}))

    cfg = Config.load(cfg_path)

    assert cfg.fallback_category == "Others"
    assert cfg.problems
    assert build_plan(cfg)


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
