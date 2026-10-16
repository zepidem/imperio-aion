# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""One startup hint attached to our HUD, dismissed without intercepting game input."""
import tkinter as tk


class LogoHint:
    def __init__(self,hud):
        self.hud=hud;self.pending=True;self.popup=None;self.timer=None
        self.visible=False;self.armed=False
        hud.window.bind_all('<ButtonPress>',self.dismiss,add='+')

    def dismiss(self,event=None):
        self.pending=False;self.visible=False
        if self.timer:
            self.hud.window.after_cancel(self.timer);self.timer=None
        if self.popup:self.popup.destroy();self.popup=None

    def sync(self,visible):
        if not self.pending:return
        if not visible or self.hud.app.hud_collapsed:
            if self.popup:self.popup.withdraw()
            self.visible=False;return
        if self.popup is None:
            self.popup=tk.Toplevel(self.hud.window);self.popup.withdraw()
            self.popup.overrideredirect(True);self.popup.transient(self.hud.window)
            self.popup.configure(bg='#D5A65F');self.popup.attributes('-topmost',True)
            self.label=tk.Label(self.popup,text=self.hint_text(),
                bg='#30251D',fg='#FFF5E8',font=('Segoe UI',9),justify='left',padx=10,pady=7);self.label.pack(padx=1,pady=1)
            self.popup.update_idletasks()
        anchor=self.hud.welcome_logo if self.hud.welcome_shown else self.hud.mascot
        w,h=self.popup.winfo_reqwidth(),self.popup.winfo_reqheight()
        x=anchor.winfo_rootx();y=anchor.winfo_rooty()-h-6
        # Clamp to the current game's monitor/viewport, including negative x.
        view=self.hud.app.viewport
        left,top,right,bottom=(view.x,view.y,view.x+view.width,view.y+view.height) if view else (0,0,self.popup.winfo_screenwidth(),self.popup.winfo_screenheight())
        if y<top:y=anchor.winfo_rooty()+anchor.winfo_height()+6
        x=max(left,min(x,right-w));y=max(top,min(y,bottom-h))
        self.popup.geometry(f'{w}x{h}{x:+d}{y:+d}')
        if not self.visible or not self.popup.winfo_viewable():
            if hasattr(self.hud.app.game,'overlay_interaction'):
                self.hud.app.game.overlay_interaction(self.popup.winfo_id(),False)
            self.popup.deiconify();self.popup.lift();self.visible=True;self.armed=False
        if self.timer is None:self.poll()

    def hint_text(self):
        return ('Aperte aqui para minimizar.\n'+self.hud.app.shortcut('hud_collapse')+' para minimizar.\nPara clicar, use '+self.hud.app.shortcut('hud_adjustment')+'.\nEsc ou clique na tela para fechar.')

    def refresh_text(self):
        if self.popup:self.label.configure(text=self.hint_text());self.popup.update_idletasks()

    def poll(self):
        if self.timer:self.hud.window.after_cancel(self.timer)
        self.timer=None
        if not self.pending or self.hud.app.closing:return
        if self.visible and hasattr(self.hud.app.game,'hint_dismiss_pressed'):
            pressed=self.hud.app.game.hint_dismiss_pressed()
            if self.armed and pressed:
                self.dismiss();return
            if not pressed:self.armed=True
        self.timer=self.hud.window.after(50,self.poll)
