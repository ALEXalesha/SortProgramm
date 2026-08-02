"""Режим командной строки: он должен показывать то же, что и окно.

CLI — рабочий инструмент проверки правил (`python main.py --cli --deep`), и
расхождение с окном обесценивает проверку: смотришь на один план, а программа
сделает другой.
"""
import json
import sys

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


# --- флаги CLI без --cli и --path ---


def no_windows(monkeypatch, seen):
    """Ни один интерфейс в тестах открываться не должен."""
    import sorter.ui_qt
    import sorter.ui
    monkeypatch.setattr(sorter.ui_qt, "launch", lambda p: seen.setdefault("окно", "PyQt"))
    monkeypatch.setattr(sorter.ui, "launch", lambda p: seen.setdefault("окно", "Tk"))


def test_apply_without_cli_flag_still_applies(monkeypatch):
    """README учит `python main.py --apply --to3d`, а это открывало окно.

    В CLI main() уходил только по `--cli` или `--path`, поэтому `--apply`
    молча терялся: человек ждал, что файлы разъедутся по папкам, а получал
    окно и нетронутые загрузки. Флаг, который меняет файлы на диске, — это
    и есть заявка на CLI, гадать тут не о чем.
    """
    seen = {}
    no_windows(monkeypatch, seen)
    monkeypatch.setattr(main, "run_cli", lambda *a: seen.setdefault("cli", a))
    monkeypatch.setattr(sys, "argv", ["main.py", "--apply", "--to3d"])

    main.main()

    assert "окно" not in seen, f"вместо CLI открылось окно {seen.get('окно')}"
    assert seen["cli"] == (None, True, True, False)


def test_deep_without_cli_flag_still_runs_cli(monkeypatch):
    """`--deep` — тоже про консоль: в окне для этого есть галочка."""
    seen = {}
    no_windows(monkeypatch, seen)
    monkeypatch.setattr(main, "run_cli", lambda *a: seen.setdefault("cli", a))
    monkeypatch.setattr(sys, "argv", ["main.py", "--deep"])

    main.main()

    assert "окно" not in seen
    assert seen["cli"] == (None, False, None, True)


def test_no_to3d_without_cli_flag_still_runs_cli(monkeypatch):
    """`--no-to3d` выключает вынос 3D разово — тоже разовый прогон в консоли."""
    seen = {}
    no_windows(monkeypatch, seen)
    monkeypatch.setattr(main, "run_cli", lambda *a: seen.setdefault("cli", a))
    monkeypatch.setattr(sys, "argv", ["main.py", "--no-to3d"])

    main.main()

    assert seen["cli"] == (None, False, False, False)


def test_bare_run_still_opens_the_window(monkeypatch):
    """Без флагов — по-прежнему окно. Это основной способ запуска."""
    seen = {}
    no_windows(monkeypatch, seen)
    monkeypatch.setattr(main, "run_cli", lambda *a: seen.setdefault("cli", a))
    monkeypatch.setattr(sys, "argv", ["main.py"])

    main.main()

    assert seen == {"окно": "PyQt"}


def test_tk_flag_still_opens_old_window(monkeypatch):
    """`--tk` — просьба открыть старое окно, а не молчаливый CLI."""
    seen = {}
    no_windows(monkeypatch, seen)
    monkeypatch.setattr(main, "run_cli", lambda *a: seen.setdefault("cli", a))
    monkeypatch.setattr(sys, "argv", ["main.py", "--tk"])

    main.main()

    assert seen == {"окно": "Tk"}
