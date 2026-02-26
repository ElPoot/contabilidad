import calendar
from datetime import datetime

import customtkinter as ctk

from facturacion_system.core.settings import get_setting


class InlineCalendar(ctk.CTkFrame):
    def __init__(self, master, on_pick):
        today = datetime.today()
        self.y, self.m = today.year, today.month
        self.on_pick = on_pick
        super().__init__(master)
        self._draw()

    def show_month(self, year: int, month: int) -> None:
        self.y, self.m = year, month
        self._draw()

    def _draw(self, **_ignored):
        for w in self.winfo_children():
            w.destroy()
        head = ctk.CTkFrame(self)
        head.pack(padx=8, pady=6, fill="x")
        ctk.CTkButton(head, text="<", width=30, command=self._prev).pack(side="left")
        ctk.CTkLabel(head, text=f"{calendar.month_name[self.m]} {self.y}").pack(
            side="left", expand=True
        )
        ctk.CTkButton(head, text=">", width=30, command=self._next).pack(side="right")

        grid = ctk.CTkFrame(self)
        grid.pack(padx=8, pady=4)
        for i, d in enumerate(["lu", "ma", "mi", "ju", "vi", "sá", "do"]):
            ctk.CTkLabel(grid, text=d, text_color=("#666666", "#AAAAAA")).grid(row=0, column=i, padx=4, pady=2)
        r = 1
        for week in calendar.monthcalendar(self.y, self.m):
            for c, day in enumerate(week):
                if day == 0:
                    ctk.CTkLabel(grid, text="", width=28).grid(row=r, column=c, padx=2, pady=2)
                    continue

                btn = ctk.CTkButton(
                    grid,
                    text=str(day),
                    width=28,
                    height=28,
                    command=lambda dd=day: self._pick(dd),
                )
                btn.grid(row=r, column=c, padx=2, pady=2)
            r += 1

        foot = ctk.CTkFrame(self)
        foot.pack(padx=8, pady=6, fill="x")
        ctk.CTkButton(foot, text="Hoy", command=self._today).pack(side="left")
        ctk.CTkButton(foot, text="Limpiar", command=lambda: self._done("")).pack(side="right")

    def _prev(self):
        self.m -= 1
        if self.m == 0:
            self.m, self.y = 12, self.y - 1
        self._draw()

    def _next(self):
        self.m += 1
        if self.m == 13:
            self.m, self.y = 1, self.y + 1
        self._draw()

    def _today(self):
        t = datetime.today()
        self._done(f"{t.day:02d}/{t.month:02d}/{t.year:04d}")

    def _pick(self, day):
        self._done(f"{day:02d}/{self.m:02d}/{self.y:04d}")

    def _done(self, value):
        if self.on_pick:
            self.on_pick(value)


class MultiSelectDropdown(ctk.CTkFrame):
    def __init__(self, master, options=None, on_change=None):
        super().__init__(master)
        if options is None:
            options = get_setting("default_extensions", ["pdf", "xml", "xlsx", "zip", "jpg", "png"])
        self.options = [str(opt).lower().lstrip(".") for opt in options]
        self.on_change = on_change
        self.sel = {opt: ctk.BooleanVar(value=(opt in ("pdf", "xml"))) for opt in self.options}
        self.btn = ctk.CTkButton(self, text=self._label(), command=self._toggle_panel)
        self.btn.pack(fill="x")
        self.panel = ctk.CTkFrame(self)
        self.panel_visible = False
        self.panel_body = ctk.CTkScrollableFrame(self.panel, width=220, height=140)
        self.panel_body.pack(fill="both", expand=True, padx=5, pady=5)
        for opt in self.options:
            ctk.CTkCheckBox(
                self.panel_body, text=opt, variable=self.sel[opt], command=self._changed
            ).pack(anchor="w", pady=2)

    def _toggle_panel(self):
        if self.panel_visible:
            self.panel.pack_forget()
        else:
            self.panel.pack(fill="x", pady=4)
        self.panel_visible = not self.panel_visible

    def _label(self):
        chosen = [k for k, v in self.sel.items() if v.get()]
        return "Ext: " + ",".join(chosen) if chosen else "Ext: (ninguna)"

    def _changed(self):
        self.btn.configure(text=self._label())
        if self.on_change:
            self.on_change(self.selected())

    def selected(self):
        return [k for k, v in self.sel.items() if v.get()]

    def set_enabled(self, enabled: bool):
        self.btn.configure(state="normal" if enabled else "disabled")

    def set_selected(self, values):
        selected = {str(v).lower().lstrip(".") for v in (values or [])}
        for opt, var in self.sel.items():
            var.set(opt in selected)
        self.btn.configure(text=self._label())

    def set_options(self, options):
        current = set(self.selected())
        self.options = [str(opt).lower().lstrip(".") for opt in (options or [])]
        for w in self.panel_body.winfo_children():
            w.destroy()
        self.sel = {opt: ctk.BooleanVar(value=(opt in current)) for opt in self.options}
        for opt in self.options:
            ctk.CTkCheckBox(self.panel_body, text=opt, variable=self.sel[opt], command=self._changed).pack(anchor="w", pady=2)
        self.btn.configure(text=self._label())
