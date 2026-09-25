"""Размер и место окна Tkinter между запусками.

Один и тот же файл в SortProgramm (старое окно --tk) и IntelegienceDrawer. Правило выбора
места - то же, что в window-state.js калькуляторов, Paint Pro и SyncGlass (restore ниже
переписан с него строка в строку): что бы ни лежало в файле - окно на отключённом мониторе,
размер больше экрана или меньше минимума, мусор, обрезанный файл, - окно открывается там,
где его видно и за заголовок можно взяться. У Qt это делает restoreGeometry(), у Tk такого
нет, поэтому рабочие области мониторов берутся у Windows (EnumDisplayMonitors).
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

# Сколько окна должно остаться на экране, чтобы за него можно было взяться: полоса
# заголовка высотой 38 и хотя бы 80 по ширине.
GRIP_HEIGHT = 38
GRIP_WIDTH = 80


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _round(v) -> int:
    """Как Math.round в JS: половина - вверх. round() Python округлял бы 2.5 до 2."""
    return math.floor(v + 0.5)


def _overlap(a, b):
    w = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
    return (w, h) if w > 0 and h > 0 else None


def restore(saved, areas, opts) -> dict:
    """Где и какого размера открыть окно.

    saved - что лежало в файле (что угодно); areas - рабочие области экранов
    {x, y, width, height}, основной первым; opts - {width, height, minWidth, minHeight}.
    Без x и y в ответе - окно ставится по центру основного экрана."""
    by_default = {"width": opts["width"], "height": opts["height"], "maximized": False}
    screens = [a for a in (areas if isinstance(areas, list) else [])
               if isinstance(a, dict) and all(_finite(a.get(k)) for k in ("x", "y", "width", "height"))
               and a["width"] > 0 and a["height"] > 0]
    if not isinstance(saved, dict) or not _finite(saved.get("width")) or not _finite(saved.get("height")):
        return by_default

    maximized = saved.get("maximized") is True
    big_w = max([opts["width"]] + [a["width"] for a in screens])
    big_h = max([opts["height"]] + [a["height"] for a in screens])
    width = _round(min(max(saved["width"], opts["minWidth"]), max(big_w, opts["minWidth"])))
    height = _round(min(max(saved["height"], opts["minHeight"]), max(big_h, opts["minHeight"])))

    if not _finite(saved.get("x")) or not _finite(saved.get("y")):
        return {"width": width, "height": height, "maximized": maximized}
    x, y = _round(saved["x"]), _round(saved["y"])

    grip = {"x": x, "y": y, "width": width, "height": GRIP_HEIGHT}
    best, best_area = None, 0
    for a in screens:
        o = _overlap(grip, a)
        if o and o[0] * o[1] > best_area:
            best, best_area = a, o[0] * o[1]
    if best is None or best_area < GRIP_WIDTH * GRIP_HEIGHT / 2:
        return {"width": width, "height": height, "maximized": maximized}

    width = min(width, max(best["width"], opts["minWidth"]))
    height = min(height, max(best["height"], opts["minHeight"]))
    nx = max(best["x"], min(x, best["x"] + best["width"] - width))
    ny = max(best["y"], min(y, best["y"] + best["height"] - height))
    return {"x": nx, "y": ny, "width": width, "height": height, "maximized": maximized}


def work_areas(root=None) -> list:
    """Рабочие области мониторов (без панели задач), основной первым. Не Windows или
    вызов не удался - один экран размером с то, что знает Tk."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            found = []
            proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

            def each(hmon, _hdc, _rect, _data):
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                    r = info.rcWork
                    found.append(({"x": r.left, "y": r.top, "width": r.right - r.left,
                                   "height": r.bottom - r.top}, bool(info.dwFlags & 1)))
                return True

            user32.EnumDisplayMonitors(None, None, proc_t(each), 0)
            if found:
                return [a for a, primary in found if primary] + [a for a, primary in found if not primary]
        except (OSError, AttributeError, ValueError):
            pass
    if root is not None:
        return [{"x": 0, "y": 0, "width": root.winfo_screenwidth(), "height": root.winfo_screenheight()}]
    return []


_GEOMETRY = re.compile(r"^(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)$")


def parse_geometry(text):
    """'1200x800+10+-5' -> {x, y, width, height} или None."""
    m = _GEOMETRY.match(text or "")
    if not m:
        return None
    w, h, x, y = m.groups()
    return {"x": int(x.lstrip("+")), "y": int(y.lstrip("+")), "width": int(w), "height": int(h)}


class Remember:
    """Держит обычные границы окна (у развёрнутого - те, к которым оно вернётся) и пишет
    их в файл при закрытии. Tk у развёрнутого окна отдаёт развёрнутую геометрию, поэтому
    обычная запоминается на каждом <Configure>, пока окно не развёрнуто.

    key - если файл общий с другим окном (у SortProgramm окна PyQt и Tkinter), своё
    место лежит под этим ключом, чужие ключи при записи не трогаются.

    opts=None - окно постоянного размера (resizable(False, False)): размер берётся у
    виджетов, запоминается только место. Звать после того, как виджеты разложены."""

    def __init__(self, root, path: Path, opts: dict | None = None, key: str | None = None):
        self.root = root
        self.path = Path(path)
        self.key = key
        self.normal = None
        data = load(self.path)
        saved = (data.get(key) if isinstance(data, dict) else None) if key else data
        areas = work_areas(root)
        fixed = opts is None
        if fixed:
            root.update_idletasks()
            w, h = root.winfo_reqwidth(), root.winfo_reqheight()
            opts = {"width": w, "height": h, "minWidth": w, "minHeight": h}
            if isinstance(saved, dict):
                saved = {**saved, "width": w, "height": h, "maximized": False}
        placed = restore(saved, areas, opts)
        text = geometry_text(placed, areas)
        # Постоянному окну - только место: размер задают виджеты, и чужая цифра (шрифт
        # или масштаб экрана поменялся) обрезала бы их.
        size = f"{placed['width']}x{placed['height']}"
        if not fixed:
            root.geometry(text)
        elif text != size:
            root.geometry(text[len(size):])
        if placed["maximized"]:
            try:
                root.state("zoomed")
            except Exception:  # noqa: BLE001 - не Windows: остаётся обычным
                pass
        root.bind("<Configure>", self._track, add="+")

    def _track(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        if self.root.state() == "normal":
            g = parse_geometry(self.root.wm_geometry())
            if g:
                self.normal = g

    def save(self) -> bool:
        self._track()
        if self.normal is None:
            return False
        state = {**self.normal, "maximized": self.root.state() == "zoomed"}
        if self.key:
            data = load(self.path)
            state = {**(data if isinstance(data, dict) else {}), self.key: state}
        return save(self.path, state)


def geometry_text(placed: dict, areas: list) -> str:
    """Строка для root.geometry(): без x и y - по центру основного экрана."""
    w, h = placed["width"], placed["height"]
    if "x" in placed:
        x, y = placed["x"], placed["y"]
    elif areas:
        a = areas[0]
        x, y = a["x"] + max(0, (a["width"] - w) // 2), a["y"] + max(0, (a["height"] - h) // 2)
    else:
        return f"{w}x{h}"
    return f"{w}x{h}{x:+d}{y:+d}"


def load(path: Path):
    """Прочитать файл; нет файла или в нём мусор - None."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None


def save(path: Path, state: dict) -> bool:
    """Записать через временный файл: убитый посреди записи процесс оставит прежний файл."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False
