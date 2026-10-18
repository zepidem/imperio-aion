# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Small native gold progress indicator; no data or identity interpretation."""
from PIL import Image, ImageDraw, ImageTk


def loading_motion_enabled():
    """Explicit indeterminate status feedback, independent of decorative motion.

    The user requested animated synchronization feedback. Do not use the
    Windows client-area transition preference to freeze this small status
    indicator, and never change that Windows setting. Widgets still stop
    their timer when hidden, resolved, or destroyed.
    """
    return True


def loading_photo(master,frame,cache):
    frame=frame%12
    if frame not in cache:
        raster=Image.new('RGBA',(56,56));draw=ImageDraw.Draw(raster)
        draw.ellipse((8,8,48,48),outline='#6A5036',width=5)
        draw.arc((8,8,48,48),start=frame*30,end=frame*30+110,fill='#D5A65F',width=6)
        cache[frame]=ImageTk.PhotoImage(raster.resize((14,14),Image.Resampling.LANCZOS),master=master)
    return cache[frame]
