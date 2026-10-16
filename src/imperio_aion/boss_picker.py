# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Dark searchable target popup with the panel's rounded box and gold scroll."""
import tkinter as tk
from PIL import ImageTk
from panel_widgets import GoldScrollbar, RoundedField, BG, SURFACE, TEXT, MUTED, GOLD, LINE
from hud_controls import surface_raster, background_rgb


class BossPicker(tk.Canvas):
    def __init__(self, parent, variable, width=250, height=32):
        super().__init__(parent, width=width, height=height, bg=parent.cget('bg'),
            highlightthickness=0, takefocus=True, cursor='hand2')
        self.variable = variable
        self.values = ()
        self.popup = None
        self.outside_binding = None
        self.highlighted = 0
        self.photo = None
        self.bind('<Configure>', self.draw)
        self.bind('<Button-1>', self.toggle)
        self.bind('<Return>', self.toggle)
        self.bind('<space>', self.toggle)
        self.bind('<Down>', self.toggle)
        self.bind('<Escape>', lambda e: self.dismiss(True))
        self.trace = variable.trace_add('write', self.draw)
        self.bind('<Destroy>', self.cleanup, add='+')

    def __setitem__(self, key, value):
        if key == 'values':
            values = tuple(value)
            if values != self.values:
                self.values = values
                if self.popup:
                    self.paint_options()
        else:
            super().__setitem__(key, value)

    def draw(self, *args):
        if not self.winfo_exists():
            return
        w, h = max(2, self.winfo_width()), max(2, self.winfo_height())
        self.photo = ImageTk.PhotoImage(surface_raster(w, h, background_rgb(self), SURFACE,
            GOLD if self.popup else LINE, radius=8), master=self)
        self.delete('all')
        self.create_image(0, 0, anchor='nw', image=self.photo)
        text = self.variable.get()
        import tkinter.font as font
        face = font.Font(self, family='Segoe UI', size=9)
        while len(text) > 1 and face.measure(text) > w - 35:
            text = text[:-2] + '…'
        self.create_text(10, h / 2, text=text, anchor='w', fill=TEXT, font=face)
        self.create_line(w - 19, h / 2 - 2, w - 15, h / 2 + 2, w - 11, h / 2 - 2, fill=GOLD, width=1.5)

    def toggle(self, event=None):
        if self.popup:
            self.dismiss(True)
            return 'break'
        self.popup = tk.Toplevel(self)
        self.popup.withdraw()
        self.popup.overrideredirect(True)
        self.popup.configure(bg=LINE)
        frame = tk.Frame(self.popup, bg=BG, padx=10, pady=10)
        frame.pack(fill='both', expand=True, padx=1, pady=1)
        self.query = tk.StringVar(master=self.popup)
        field = RoundedField(frame, self.query, width=335, height=32)
        field.pack(fill='x', pady=(0, 7))
        self.search = field.inner
        self.search.configure(takefocus=True)
        self.search.insert(0, '')
        self.query.trace_add('write', lambda *_: self.paint_options())
        tk.Label(frame, text='Buscar boss ou alvo', bg=BG, fg=MUTED, font=('Segoe UI', 8)).pack(anchor='w', before=field, pady=(0, 5))
        list_frame = tk.Frame(frame, bg=BG)
        list_frame.pack(fill='both', expand=True)
        self.list = tk.Canvas(list_frame, bg=BG, highlightthickness=0, height=210, takefocus=True)
        scroll = GoldScrollbar(list_frame, command=self.list.yview, autohide=True)
        self.list.configure(yscrollcommand=scroll.set)
        self.list.pack(side='left', fill='both', expand=True)
        self.list.bind('<Button-1>', self.click)
        self.list.bind('<MouseWheel>', lambda e: self.list.yview_scroll(-1 if e.delta > 0 else 1, 'units'))
        self.popup.bind('<Escape>', lambda e: self.dismiss(True))
        self.popup.bind('<Down>', lambda e: self.move(1))
        self.popup.bind('<Up>', lambda e: self.move(-1))
        self.popup.bind('<Return>', self.choose)
        self.popup.bind('<FocusOut>', self.defer_focus)
        self.popup.bind('<Tab>', lambda e: self.dismiss(True))
        self.paint_options()
        self.popup.update_idletasks()
        w, h = 360, self.popup.winfo_reqheight()
        x = max(0, min(self.winfo_rootx() + self.winfo_width() - w, self.winfo_screenwidth() - w))
        y = self.winfo_rooty() + self.winfo_height() + 5
        if y + h > self.winfo_screenheight():
            y = max(0, self.winfo_rooty() - h - 5)
        self.popup.geometry(f'{w}x{h}+{x}+{y}')
        self.popup.deiconify()
        self.popup.lift()
        self.search.focus_set()
        self.outside_binding = self.winfo_toplevel().bind('<Button-1>', self.outside, add='+')
        self.draw()
        return 'break'

    def outside(self, event):
        if self.popup and event.widget is not self and event.widget.winfo_toplevel() is not self.popup:
            self.dismiss()

    def defer_focus(self, event):
        if self.popup:
            self.after_idle(self.check_focus)

    def check_focus(self):
        if self.popup:
            focus = self.focus_get()
            if focus is None or focus.winfo_toplevel() is not self.popup:
                self.dismiss()

    def paint_options(self):
        if not self.popup:
            return
        term = self.query.get().strip().casefold()
        self.matches = [value for value in self.values if term in value.casefold()]
        self.highlighted = self.matches.index(self.variable.get()) if self.variable.get() in self.matches else 0
        self.paint_rows()

    def paint_rows(self):
        self.list.delete('all')
        if not self.matches:
            self.list.create_text(10, 25, anchor='w', text='Nenhum alvo encontrado' if self.values else 'Nenhum boss registrado', fill=TEXT, font=('Segoe UI', 9, 'bold'))
            self.list.create_text(10, 55, anchor='w', text='Tente outro nome.' if self.values else 'Aguardando o primeiro combate identificado.', fill=MUTED, font=('Segoe UI', 8))
        for i, text in enumerate(self.matches):
            y = i * 34
            if i == self.highlighted:
                self.list.create_rectangle(1, y + 1, 326, y + 33, fill='#493225', outline='')
                self.list.create_line(9, y + 32, 320, y + 32, fill=GOLD)
            clipped = text if len(text) <= 49 else text[:46] + '…'
            self.list.create_text(9, y + 17, text=clipped, anchor='w', fill=TEXT if i == self.highlighted else MUTED, font=('Segoe UI', 8))
        self.list.configure(scrollregion=(0, 0, 328, max(85, len(self.matches) * 34)))

    def move(self, delta):
        if self.matches:
            self.highlighted = max(0, min(len(self.matches) - 1, self.highlighted + delta))
            self.paint_rows()
            total = max(1, len(self.matches) * 34)
            first, last = self.list.yview()
            position = self.highlighted * 34
            if position < first * total:
                self.list.yview_moveto(position / total)
            elif position + 34 > last * total:
                self.list.yview_moveto((position + 34 - self.list.winfo_height()) / total)
        return 'break'

    def click(self, event):
        index = int(self.list.canvasy(event.y) // 34)
        if 0 <= index < len(self.matches):
            self.highlighted = index
            self.choose()

    def choose(self, event=None):
        if self.matches:
            self.variable.set(self.matches[self.highlighted])
            self.dismiss(True)
            self.event_generate('<<ComboboxSelected>>')
        return 'break'

    def dismiss(self, focus=False):
        if self.outside_binding:
            self.winfo_toplevel().unbind('<Button-1>', self.outside_binding)
            self.outside_binding = None
        if self.popup:
            popup, self.popup = self.popup, None
            popup.destroy()
        if focus:
            self.focus_set()
        self.draw()
        return 'break'

    def cleanup(self, event):
        if event.widget is self:
            self.dismiss()
            self.variable.trace_remove('write', self.trace)
