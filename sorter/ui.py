"""Окно Tkinter в стиле Mouzi: кнопка очистки, превью плана, применить."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

from .config import Config
from .planner import build_plan, external_3d_warning, Move
from .mover import apply
from .util import (PLAN_EMPTY, PLAN_NOT_BUILT, PLAN_NO_FOLDER, nothing_to_apply,
                   rel_to as _rel_to, report, report_title, settings_message)


class _Tip:
    """Подсказка мышью: длинный список под короткой строкой состояния.

    Окно PyQt кладёт имена непрочитанных папок в `setToolTip`, консоль печатает
    их строками. Здесь такого готового средства нет, а надобность та же:
    строка состояния короткая, в неё влезает только счёт, а чинить идут к
    конкретной папке — и знать надо, к какой именно.

    Всплывающее окно без рамки, живёт, пока курсор над виджетом. Пустой текст
    означает «показывать нечего»: подсказка не появится вовсе.
    """

    def __init__(self, widget):
        self.widget = widget
        self.text = ""
        self.window = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def set(self, text: str) -> None:
        self.text = text
        if not text:
            self._hide()

    def _show(self, _event=None):
        if not self.text or self.window is not None:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        tk.Label(self.window, text=self.text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, wraplength=560).pack()

    def _hide(self, _event=None):
        if self.window is not None:
            self.window.destroy()
            self.window = None


class SorterApp:
    def __init__(self, root: tk.Tk, config_path: Path):
        self.config_path = config_path
        self.config = Config.load(config_path)
        self.moves: list[Move] = []
        # Почему план пуст. Пустой список получается тремя путями, и на
        # «Применить» у них три разных ответа (`util.nothing_to_apply`).
        self.plan_state = PLAN_NOT_BUILT
        # Хвост строки состояния про папки, которые не удалось прочитать.
        # Держим полем, потому что дописывает его и итог применения.
        self.unread_tail = ""
        self.root = root

        root.title("Сортировщик загрузок")
        root.geometry("760x520")
        root.minsize(620, 420)

        self._build_header(root)
        self._build_table(root)
        self._build_footer(root)

        # Крестик окна закрывает его сам, минуя весь наш код: без этого
        # перехвата настройки, которые окно только что меняло, не сохранялись
        # никогда.
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Про испорченные настройки предупреждали окно PyQt и CLI, а этот
        # интерфейс молчал: без правил всё уезжает в Others, и снаружи это
        # выглядит как нормальный план. Сказать надо до «Применить».
        #
        # Поломка и уведомление — разные окна: подчищенное разбором раскладку
        # не портит, и пугать им незачем (`util.settings_message`).
        if self.config.problems or self.config.notices:
            title, text, broken = settings_message(
                self.config.problems, self.config.notices)
            (messagebox.showwarning if broken else messagebox.showinfo)(title, text)

    # --- разметка ---

    def _build_header(self, root):
        top = ttk.Frame(root, padding=12)
        top.pack(fill="x")

        self.path_var = tk.StringVar(value=self.config.downloads_path)
        ttk.Label(top, text="Папка:").pack(side="left")
        ttk.Label(top, textvariable=self.path_var, foreground="#555").pack(side="left", padx=6)

        ttk.Button(top, text="📂 Открыть", command=self.open_downloads).pack(side="right")
        ttk.Button(top, text="🧹 Очистить", command=self.preview).pack(side="right", padx=6)

    def _build_table(self, root):
        frame = ttk.Frame(root, padding=(12, 0))
        frame.pack(fill="both", expand=True)

        cols = ("file", "dest")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.tree.heading("file", text="Файл")
        self.tree.heading("dest", text="Куда поедет")
        self.tree.column("file", width=320)
        self.tree.column("dest", width=380)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_footer(self, root):
        bottom = ttk.Frame(root, padding=12)
        bottom.pack(fill="x")

        self.move_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bottom, text="Перемещать файлы (иначе только показ)",
            variable=self.move_enabled,
        ).pack(side="left")

        self.to_3d = tk.BooleanVar(value=bool(self.config.external_3d.get("enabled", False)))
        ttk.Checkbutton(
            bottom, text="3D-модели → All_3d",
            variable=self.to_3d, command=self.preview,
        ).pack(side="left", padx=12)

        self.resort = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bottom, text="Переразложить старое",
            variable=self.resort, command=self.preview,
        ).pack(side="left")

        self.apply_btn = ttk.Button(bottom, text="Применить", command=self.do_apply)
        self.apply_btn.pack(side="right")

        self.status = tk.StringVar(value="Нажми «Очистить», чтобы построить план.")
        status_label = ttk.Label(root, textvariable=self.status,
                                 padding=(12, 0, 12, 10), foreground="#333")
        status_label.pack(fill="x")
        # Имена непрочитанных папок — сюда: в строку влезает только счёт, а
        # чинить надо конкретную папку (см. `preview`).
        self.unread_tip = _Tip(status_label)

    # --- действия ---

    def _save_settings(self):
        """Пишет в config.json то, что окно меняло. Правила не трогает.

        Меняет это окно одну настройку — галочку выноса 3D, — и она не
        сохранялась никогда. Окно PyQt пишет её в config.json при закрытии, а
        консоль без флагов оттуда же её и читает: на этом держится обещание,
        что окно и консоль на одних настройках показывают один и тот же план.
        Здесь оно не выполнялось — галочку поставили, файлы разложили, окно
        закрыли, и следующий запуск снова раскладывает модели по обычным
        категориям. Ни ошибки, ни слова о том, что настройку забыли.

        Пути к папке 3D у этого окна нет, поэтому в конфиге он остаётся как
        был: стирать то, чего не показывал, окно не вправе.

        Неудачу записи глотаем молча по той же причине, что и в окне PyQt: она
        случается на закрытии, говорить о ней уже некому и некуда.
        """
        if not isinstance(self.config.external_3d, dict):
            self.config.external_3d = {}
        self.config.external_3d["enabled"] = self.to_3d.get()
        try:
            self.config.save(self.config_path)
        except OSError:
            pass

    def _on_close(self):
        self._save_settings()
        self.root.destroy()

    def open_downloads(self):
        """Открывает папку загрузок в проводнике.

        Папку проверяем сами, как это делает окно PyQt. Без проверки
        `os.startfile` показывал `[WinError 2] Не удается найти указанный
        файл` — сообщение системы про путь, который окно написало строкой
        выше своими словами («Папка не найдена: …»). Два разных ответа на
        одну и ту же беду, и второй ещё и не по-русски.
        """
        path = self.config.downloads_path
        if not path or not Path(path).is_dir():
            messagebox.showinfo("Папка не найдена", "Укажи существующую папку.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                os.system(f'xdg-open "{path}"')
        except OSError as exc:
            messagebox.showerror("Ошибка", str(exc))

    def preview(self):
        # Опечатка в пути выглядела ровно как прибранная папка: «План готов:
        # 0 шт.». Окно PyQt и CLI в этом случае говорят «Папка не найдена» —
        # здесь должно быть то же самое, иначе ноль ничего не значит.
        if not Path(self.config.downloads_path).is_dir():
            self.tree.delete(*self.tree.get_children())
            self.moves = []
            self.plan_state = PLAN_NO_FOLDER
            self.unread_tail = ""
            # Список от прошлого плана здесь уже неправда: папки той нет.
            self.unread_tip.set("")
            self.status.set(
                f"Папка не найдена: {self.config.downloads_path}")
            return

        # Галочка «Переразложить старое» включает разбор папок, которые программа
        # создала сама. Чужие папки не трогаются ни в каком режиме.
        #
        # Папки, которые не удалось прочитать, приходят отдельным списком: без
        # них «План готов: 0 шт.» неотличим от прибранных загрузок. Окно PyQt и
        # консоль говорят об этом же — расхождение между интерфейсами тут
        # обесценивает проверку правил через любой из них.
        unread: list[str] = []
        self.moves = build_plan(
            self.config,
            send_3d_external=self.to_3d.get(),
            deep=self.resort.get(),
            problems=unread,
        )
        self.tree.delete(*self.tree.get_children())
        root = Path(self.config.downloads_path)
        for mv in self.moves:
            dest = _rel_to(mv.dst, root)
            if mv.note:
                dest = f"{dest}   ({mv.note})"
            self.tree.insert("", "end", values=(_rel_to(mv.src, root), dest))
        # Галочка 3D без годного пути ничего не выносит, и снаружи это
        # неотличимо от исправной работы. Текст общий с окном PyQt и консолью.
        warning = external_3d_warning(self.config, self.to_3d.get())
        tail = f"   {warning}" if warning else ""
        # Ту же строку дописывает и итог применения: `do_apply` зовёт `preview`
        # и тут же затирает его строку, а вместе с ней уезжала и жалоба. В тот
        # самый момент, когда человек читает отчёт, окно докладывало о
        # безупречном прогоне — «ошибок: 0», — при том что файлы непрочитанной
        # папки лежат неразобранными. В список неудач они не попадают: их не
        # было и в плане.
        self.unread_tail = (f"   Не прочитано папок: {len(unread)} — их файлы "
                            "в план не попали." if unread else "")
        tail += self.unread_tail
        # В строку — счёт, в подсказку мышью — сами имена. Одного числа мало
        # ровно потому, зачем жалоба и заведена: причины у неё временные и
        # чинятся руками (воткнуть флешку, подключить сетевой диск, закрыть
        # программу, держащую каталог), но чинить надо КОНКРЕТНУЮ папку.
        # Консоль печатает их строками, окно PyQt кладёт в подсказку — этот
        # интерфейс называл одно число, то есть говорил о беде и не говорил,
        # где её искать.
        self.unread_tip.set("\n".join(unread))
        self.plan_state = PLAN_EMPTY if not self.moves else ""
        self.status.set(f"План готов: {len(self.moves)} шт. к перемещению.{tail}")

    def do_apply(self):
        if not self.moves:
            # Три разных «двигать нечего» — три разных ответа. Общий с окном
            # PyQt, чтобы совет не расходился между интерфейсами.
            messagebox.showinfo(*nothing_to_apply(self.plan_state))
            return
        if not self.move_enabled.get():
            messagebox.showinfo(
                "Режим показа",
                "Включи галочку «Перемещать файлы», чтобы применить.",
            )
            return
        if not messagebox.askyesno("Подтверждение", f"Переместить {len(self.moves)} файлов?"):
            return

        result = apply(self.moves, self.config, dry_run=False)
        # Сначала новый план, потом итог: `preview` пишет в ту же строку
        # состояния и затирала бы «Перемещено: 7» на «План готов: 0 шт.».
        self.preview()
        # В строку статуса — короткий итог, в окно — полный отчёт с именами:
        # одно лишь число ошибок не говорит, какой файл остался в загрузках и
        # почему. Текст общий с окном PyQt и консолью (`util.report`).
        self.status.set(f"Перемещено: {result.moved}, ошибок: "
                        f"{len(result.errors)}{self.unread_tail}")
        if result.errors or result.notes or result.undo_failed:
            messagebox.showwarning(report_title(result), report(result))
        else:
            messagebox.showinfo(report_title(result), report(result))


def launch(config_path: Path) -> None:
    root = tk.Tk()
    # Тот же расчёт BASE_DIR, что и в main.py.
    base_dir = (Path(sys.executable).parent if getattr(sys, "frozen", False)
                else Path(__file__).parent.parent)
    icon_path = base_dir / "icon.ico"
    if icon_path.exists():
        root.iconbitmap(str(icon_path))
    SorterApp(root, config_path)
    root.mainloop()
