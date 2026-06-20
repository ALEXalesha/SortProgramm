from sorter.config import Config


def test_save_load_roundtrip(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg = Config(
        downloads_path="C:/Some/Downloads",
        categories={"Учёба": ["класс"]},
        type_map={"Documents": ["pdf"]},
        managed_folders=["Учёба", "Others"],
        ignore=["*.tmp"],
        external_3d={"enabled": True, "path": "D:/Models", "extensions": ["obj"]},
    )
    cfg.save(cfg_path)

    loaded = Config.load(cfg_path)
    assert loaded.downloads_path == "C:/Some/Downloads"
    assert loaded.categories == {"Учёба": ["класс"]}
    assert loaded.type_map == {"Documents": ["pdf"]}
    assert loaded.external_3d["enabled"] is True
    assert loaded.external_3d["path"] == "D:/Models"


def test_save_does_not_write_overrides_into_config(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg = Config(downloads_path="X", overrides={"a.pdf": "Учёба"})
    cfg.save(cfg_path)
    text = cfg_path.read_text(encoding="utf-8")
    assert "overrides" not in text
