"""Режим командной строки: он должен показывать то же, что и окно.

CLI — рабочий инструмент проверки правил (`python main.py --cli --deep`), и
расхождение с окном обесценивает проверку: смотришь на один план, а программа
сделает другой.
"""
import json

import main


def write_config(tmp_path, data, rules=None):
    (tmp_path / "config.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "rules.json").write_text(
        json.dumps(rules or {
            "categories": {},
            "type_map": {"3D": ["stl"]},
            "managed_folders": ["Others", "Misc", "3D"],
            "fallback_category": "Others",
            "fallback_type": "Misc",
        }, ensure_ascii=False),
        encoding="utf-8")
    return tmp_path / "config.json"


def test_cli_reports_broken_settings(tmp_path, monkeypatch, capsys):
    """Окно предупреждает о непрочитанных настройках, а CLI молчал.

    Без правил всё уезжает в Others. Увидеть это надо до `--apply`, а не после.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"downloads_path": "X:/z", "extern', encoding="utf-8")
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)

    main.run_cli(str(tmp_path), do_apply=False, to_3d=None, deep=False)

    out = capsys.readouterr().out
    assert "config.json" in out
    assert "не читается" in out


def test_cli_says_when_folder_is_missing(tmp_path, monkeypatch, capsys):
    """Опечатка в `--path` выглядела как «загрузки уже разобраны».

    Окно на несуществующей папке пишет «Папка не найдена», а CLI печатал
    «Найдено к перемещению: 0» — тот же самый ответ, что и на прибранной
    папке. Отличить одно от другого было нельзя.
    """
    cfg_path = write_config(tmp_path, {"downloads_path": str(tmp_path)})
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)

    main.run_cli(str(tmp_path / "нет такой"), do_apply=False, to_3d=None, deep=False)

    out = capsys.readouterr().out
    assert "не найдена" in out
    assert "Найдено к перемещению" not in out


def test_cli_uses_saved_3d_setting(tmp_path, monkeypatch, capsys):
    """Галочку «3D → отдельная папка» окно хранит в config.json.

    CLI её не читал и всегда раскладывал 3D внутри загрузок: план в консоли
    расходился с тем, что сделает окно с теми же настройками.
    """
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "деталь.stl").write_text("x", encoding="utf-8")
    cfg_path = write_config(tmp_path, {
        "downloads_path": str(downloads),
        "external_3d": {"enabled": True, "path": str(tmp_path / "All_3d"),
                        "extensions": ["stl"]},
    })
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)

    main.run_cli(None, do_apply=False, to_3d=None, deep=False)

    assert "All_3d" in capsys.readouterr().out


def test_cli_can_turn_saved_3d_setting_off(tmp_path, monkeypatch, capsys):
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "деталь.stl").write_text("x", encoding="utf-8")
    cfg_path = write_config(tmp_path, {
        "downloads_path": str(downloads),
        "external_3d": {"enabled": True, "path": str(tmp_path / "All_3d"),
                        "extensions": ["stl"]},
    })
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)

    main.run_cli(None, do_apply=False, to_3d=False, deep=False)

    assert "All_3d" not in capsys.readouterr().out
