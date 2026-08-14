"""Разделение правил и настроек: rules.json против config.json.

Правила поставляет программа и обновляет при установке; настройки принадлежат
пользователю. Раньше всё лежало в одном файле, помеченном «не перезаписывать»,
и обновления правил не доезжали до установленной копии никогда.
"""
import json

from sorter.config import Config

USER = {
    "downloads_path": "X:/загрузки",
    "external_3d": {"enabled": True, "path": "X:/all3d", "extensions": ["stl"]},
}
RULES = {
    "categories": {"Медиа": ["клип"]},
    "patterns": {"Скриншоты": ["^screenshot"]},
    "type_map": {"Videos": ["mp4"]},
    "managed_folders": ["Медиа", "Videos"],
    "ignore": ["*.tmp"],
    "fallback_category": "Разное",
    "fallback_type": "Прочее",
}


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_rules_come_from_rules_json(tmp_path):
    cfg_path = write(tmp_path / "config.json", USER)
    write(tmp_path / "rules.json", RULES)
    cfg = Config.load(cfg_path)
    assert cfg.categories == RULES["categories"]
    assert cfg.patterns == RULES["patterns"]
    assert cfg.fallback_category == "Разное"
    assert cfg.downloads_path == "X:/загрузки"


def test_rules_json_wins_over_stale_copy_in_config(tmp_path):
    """Главный случай: в config.json от старой версии остались прежние правила."""
    stale = {**USER, "categories": {"Старое": ["древность"]}, "patterns": {}}
    cfg_path = write(tmp_path / "config.json", stale)
    write(tmp_path / "rules.json", RULES)
    cfg = Config.load(cfg_path)
    assert cfg.categories == {"Медиа": ["клип"]}


def test_old_config_without_rules_json_still_works(tmp_path):
    """Установка старой версии: правила ещё лежат в config.json."""
    everything = {**USER, **RULES}
    cfg = Config.load(write(tmp_path / "config.json", everything))
    assert cfg.categories == RULES["categories"]
    assert cfg.managed_folders == RULES["managed_folders"]


def test_save_keeps_rules_that_live_in_old_config(tmp_path):
    """Старая установка: `rules.json` рядом нет, правила лежат в `config.json`.

    Сохранение выкидывало ключи правил как «не настройки», а взять их обратно
    неоткуда: файла с правилами ещё не существует. Первое же закрытие окна
    оставляло программу без единой категории — всё в `Others` навсегда.
    """
    cfg_path = write(tmp_path / "config.json", {**USER, **RULES})
    cfg = Config.load(cfg_path)

    cfg.save(cfg_path)

    reloaded = Config.load(cfg_path)
    assert reloaded.categories == RULES["categories"]
    assert reloaded.managed_folders == RULES["managed_folders"]
    assert reloaded.fallback_category == "Разное"


def test_save_writes_only_user_settings(tmp_path):
    cfg_path = write(tmp_path / "config.json", USER)
    write(tmp_path / "rules.json", RULES)
    cfg = Config.load(cfg_path)
    cfg.downloads_path = "Y:/другое"
    cfg.save(cfg_path)

    written = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert written["downloads_path"] == "Y:/другое"
    assert "categories" not in written, "правила не должны попадать в config.json"


def test_save_keeps_unknown_keys(tmp_path):
    """Ручную правку конфига сохранение из окна стирать не должно."""
    cfg_path = write(tmp_path / "config.json", {**USER, "моё_поле": "не терять"})
    write(tmp_path / "rules.json", RULES)
    cfg = Config.load(cfg_path)
    cfg.save(cfg_path)
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["моё_поле"] == "не терять"


def test_save_does_not_touch_rules_file(tmp_path):
    cfg_path = write(tmp_path / "config.json", USER)
    rules_path = write(tmp_path / "rules.json", RULES)
    before = rules_path.read_text(encoding="utf-8")
    Config.load(cfg_path).save(cfg_path)
    assert rules_path.read_text(encoding="utf-8") == before
