"""Окно Tkinter: настройки, которые оно меняет, должны переживать закрытие.

`tk.Tk()` требует экрана, поэтому виджеты не поднимаются: проверяется сам
метод сохранения на объекте с теми же полями, что у живого окна.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from sorter.config import Config
from sorter.ui import SorterApp


def make_window(tmp_path, to_3d):
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(tmp_path),
        "external_3d": {"enabled": False, "path": "X:/all3d", "extensions": ["stl"]},
    }, ensure_ascii=False), encoding="utf-8")

    app = SorterApp.__new__(SorterApp)
    app.config_path = tmp_path / "config.json"
    app.config = Config.load(app.config_path)
    app.to_3d = SimpleNamespace(get=lambda: to_3d)
    return app


def test_tk_window_remembers_the_3d_checkbox(tmp_path):
    """Галочка «3D-модели → All_3d» забывалась при закрытии окна.

    Окно PyQt сохраняет её в config.json, консоль без флагов оттуда же её и
    берёт — на том и держится обещание, что окно и консоль на одних настройках
    показывают один и тот же план. Окно Tkinter не сохраняло ничего: галочку
    поставили, файлы разложили, закрыли — и следующий запуск (хоть окна, хоть
    консоли) снова раскладывает модели по обычным категориям. Ни ошибки, ни
    слова о том, что настройку не запомнили.
    """
    app = make_window(tmp_path, to_3d=True)

    app._save_settings()

    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["external_3d"]["enabled"] is True


def test_tk_window_keeps_the_3d_path_it_did_not_touch(tmp_path):
    """Поля пути к папке 3D у этого окна нет — стереть его оно не вправе."""
    app = make_window(tmp_path, to_3d=True)

    app._save_settings()

    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["external_3d"]["path"] == "X:/all3d"


def test_tk_window_names_the_folder_it_could_not_read(tmp_path, monkeypatch):
    """«Не прочитано папок: 1» без имени — жалоба, по которой нечего чинить.

    Про папку, которая на месте, а прочитать её не выходит, README обещает
    рассказать во всех трёх интерфейсах: «План готов: 0 шт.» на такой папке —
    тот самый нечестный ноль. Консоль печатает путь строкой, окно PyQt кладёт
    его в подсказку мышью, а это окно называло одно число.

    Числа мало ровно потому, зачем жалоба и заведена: причины у неё временные и
    чинятся руками — воткнуть флешку, подключить сетевой диск, закрыть
    программу, держащую каталог, — но чинить надо КОНКРЕТНУЮ папку, а какую
    именно, окно не говорило. Загрузки у человека не из трёх папок, и обойти их
    в проводнике, гадая, какая не открылась, — работа на полдня.
    """
    app = make_window(tmp_path, to_3d=False)
    downloads = Path(app.config.downloads_path)
    (downloads / "Медиа" / "Videos").mkdir(parents=True)
    (downloads / "Медиа" / "Videos" / "клип.mp4").write_text("x", encoding="utf-8")

    real = Path.iterdir
    denied = (downloads / "Медиа" / "Videos").resolve()

    def guard(self):
        if self.resolve() == denied:
            raise PermissionError(13, "Отказано в доступе")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", guard)

    app.tree = _FakeTree()
    app.status = _FakeVar()
    app.resort = _FakeVar(True)
    app.moves = []
    app.unread_tip = _FakeTip()

    app.preview()

    assert "Не прочитано папок: 1" in app.status.get()
    assert "Videos" in app.unread_tip.text, (
        "окно сказало, что папку не прочитало, но не сказало какую: "
        f"{app.unread_tip.text!r}")


class _FakeVar:
    """`tk.StringVar`/`BooleanVar` без экрана."""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _FakeTree:
    """`ttk.Treeview` без экрана: помнит только вставленные строки."""

    def __init__(self):
        self.rows = []

    def get_children(self):
        return list(range(len(self.rows)))

    def delete(self, *_items):
        self.rows = []

    def insert(self, _parent, _where, values):
        self.rows.append(values)


class _FakeTip:
    def __init__(self):
        self.text = ""

    def set(self, text):
        self.text = text
