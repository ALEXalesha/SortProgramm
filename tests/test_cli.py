"""Режим командной строки: он должен показывать то же, что и окно.

CLI — рабочий инструмент проверки правил (`python main.py --cli --deep`), и
расхождение с окном обесценивает проверку: смотришь на один план, а программа
сделает другой.
"""
import json
import sys

import main
from sorter.mover import Result


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


# --- вынос 3D, которому некуда выносить ---


def test_cli_says_when_3d_has_nowhere_to_go(tmp_path, monkeypatch, capsys):
    """`--to3d` без пути молча раскладывал модели по обычным категориям.

    Окно про такую настройку говорит строкой под планом, а консоль молчала:
    предупреждал `Config.load` — и только по галочке, сохранённой в файле.
    Флаг включает вынос поверх выключенной галочки, и тогда не предупреждал
    никто. План в консоли от исправного при этом неотличим.
    """
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "деталь.stl").write_text("x", encoding="utf-8")
    cfg_path = write_config(tmp_path, {
        "downloads_path": str(downloads),
        "external_3d": {"enabled": False, "path": "", "extensions": ["stl"]},
    })
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)

    main.run_cli(None, do_apply=False, to_3d=True, deep=False)

    assert "не указан" in capsys.readouterr().out


def test_cli_says_when_3d_path_is_incomplete(tmp_path, monkeypatch, capsys):
    """Путь без диска — это путь от рабочей папки, а не папка внутри загрузок."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "деталь.stl").write_text("x", encoding="utf-8")
    cfg_path = write_config(tmp_path, {
        "downloads_path": str(downloads),
        "external_3d": {"enabled": True, "path": "All_3d", "extensions": ["stl"]},
    })
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)

    main.run_cli(None, do_apply=False, to_3d=None, deep=False)

    out = capsys.readouterr().out
    assert "All_3d" in out
    assert "деталь.stl  ->  Others" in out, "модель должна остаться в загрузках"


def test_cli_stays_quiet_when_3d_is_set_up(tmp_path, monkeypatch, capsys):
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

    assert "не указан" not in capsys.readouterr().out


def _apply_with(tmp_path, monkeypatch, capsys, result):
    """Гоняет `--apply` на готовом итоге: проверяем печать, а не перемещения.

    Оговорка «лёг под другим именем» возникает, только когда цель занимают
    МЕЖДУ планом и применением, — через один вызов такое не подстроить, а
    проверить надо именно слова отчёта.
    """
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "клип.mp4").write_text("x", encoding="utf-8")
    cfg_path = write_config(tmp_path, {"downloads_path": str(downloads)})
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(main, "apply", lambda moves, config, dry_run=True: result)

    main.run_cli(None, do_apply=True, to_3d=False, deep=False)

    return capsys.readouterr().out


def test_cli_prints_the_same_report_as_the_windows(tmp_path, monkeypatch, capsys):
    """Отчёт общий на три интерфейса — это обещание `util.report`, и консоль
    его не выполняла.

    Она печатала итог своими словами, и пропадал ровно тот заголовок, ради
    которого отчёт собрали в одном месте. Оговорка «лёг под другим именем»
    шла сразу за списком ошибок, без единого слова между ними: строка
    «клип.mp4: в цели уже есть …» читалась как ещё одна неудача, хотя файл
    переехал и лежит под соседним именем. Проверять правила через консоль
    README советует именно потому, что консоль показывает то же, что окно.
    """
    out = _apply_with(tmp_path, monkeypatch, capsys, Result(
        planned=2, moved=2,
        notes=[("клип.mp4", "в цели уже есть «клип.mp4», положили как «клип (1).mp4»")],
        errors=[("отчёт.pdf", "нет файла")]))

    assert "Не переехали:" in out
    assert "Легли под другим именем:" in out, (
        f"оговорка ушла без заголовка, вперемешку с ошибками:\n{out}")
    assert "клип (1).mp4" in out


def test_cli_names_the_undo_log_it_could_not_write(tmp_path, monkeypatch, capsys):
    """Файлы разложены, а вернуть их назад нечем — про это тоже общий текст."""
    out = _apply_with(tmp_path, monkeypatch, capsys,
                      Result(planned=1, moved=1, undo_failed="нет места на диске"))

    assert "Отменить эту сортировку не выйдет" in out
    assert "нет места на диске" in out


def test_cli_does_not_cut_the_report_short(tmp_path, monkeypatch, capsys):
    """Хвост сворачивается ради окна, которое не резиновое. Консоль листают.

    Свернуть список в консоли значит спрятать имена, за которыми туда и идут:
    `python main.py --cli --apply` — рабочий инструмент, а не панель с итогом.
    """
    out = _apply_with(tmp_path, monkeypatch, capsys, Result(
        planned=12, moved=0,
        errors=[(f"файл{i:02}.dat", "занят другой программой") for i in range(12)]))

    assert "…и ещё" not in out
    assert "файл11.dat" in out


def test_cli_names_the_folder_it_could_not_read(tmp_path, monkeypatch, capsys):
    """Нечитаемая папка давала «Найдено к перемещению: 0» и ни слова больше.

    Ноль в консоли значит «прибрано», и отличить его от «половину папок не
    открыли» было нечем. Про ненайденную папку CLI говорит давно — про папку,
    которая на месте и не читается, молчал.
    """
    from pathlib import Path

    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "клип.mp4").write_text("x", encoding="utf-8")
    cfg_path = write_config(tmp_path, {"downloads_path": str(downloads)})
    monkeypatch.setattr(main, "CONFIG_PATH", cfg_path)

    real = Path.iterdir

    def guard(self):
        if self == downloads:
            raise PermissionError(13, "Отказано в доступе")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", guard)

    main.run_cli(None, do_apply=False, to_3d=None, deep=False)

    out = capsys.readouterr().out
    assert "Найдено к перемещению: 0" in out
    assert str(downloads) in out
    assert "не удалось прочитать" in out, out
