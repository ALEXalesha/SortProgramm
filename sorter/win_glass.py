"""Настоящее стекло Windows через DWM (Acrylic, скруглённые углы, тёмный режим).

Изолировано и только для Windows. Любая ошибка не должна ронять приложение —
вызывающий код просто получит False и откатится на полупрозрачный фон.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

# Атрибуты DwmSetWindowAttribute
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38

# Значения
DWMWCP_ROUND = 2            # скруглённые углы
DWMSBT_TRANSIENTWINDOW = 3  # Acrylic (размытое стекло)


class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def _set_attr(dwm, hwnd: int, attr: int, value: int) -> None:
    val = ctypes.c_int(value)
    dwm.DwmSetWindowAttribute(
        wintypes.HWND(hwnd), attr, ctypes.byref(val), ctypes.sizeof(val)
    )


def apply_acrylic(hwnd: int, dark: bool = True) -> bool:
    """Включает Acrylic-фон, скруглённые углы и тёмный режим для окна hwnd.

    Возвращает True при успехе, False если система не поддержала (старый Windows).
    """
    try:
        dwm = ctypes.windll.dwmapi
        _set_attr(dwm, hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
        _set_attr(dwm, hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
        _set_attr(dwm, hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_TRANSIENTWINDOW)
        # Растягиваем стекло на весь клиент, чтобы материал просвечивал.
        margins = _MARGINS(-1, -1, -1, -1)
        dwm.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(margins))
        return True
    except Exception:
        return False
