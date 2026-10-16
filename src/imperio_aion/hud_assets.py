# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Display the supplied artwork at its final HUD size without rewriting originals.

Each instance belongs to one Tk interpreter. Its cache keeps PhotoImages alive
across Canvas redraws. Pillow's Lanczos filter is used for smooth downsampling;
the built-in Tk fallback is available for diagnostics if Pillow is missing.
"""
from pathlib import Path
import math
import tkinter as tk
from pet_catalog import CATALOG

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None


ROOT = Path(__file__).resolve().parent
CLASS_KEYS = ("gladiator", "templar", "assassin", "ranger", "sorcerer",
              "spiritmaster", "cleric", "chanter")
ASSET_FILES = {key: Path("assets/kit/classes-raster") / (key + ".png")
               for key in CLASS_KEYS}
ASSET_FILES["mascot"] = Path("assets/kit/logo-aion2.png")
# Preserve original filenames while correcting their semantic association.
ASSET_FILES["filter_all"] = Path("assets/kit/targets-raster/bosses.png")
ASSET_FILES["filter_bosses"] = Path("assets/kit/targets-raster/mobs.png")
ASSET_FILES["filter_pets"] = Path("assets/kit/mascote-cavalo.png")
ASSET_FILES["filter_duty"] = Path("assets/kit/dever-ordens.png")
ASSET_FILES.update({f'activity_{key}':Path('assets/kit/activities')/(key+'.png')
                    for key in ('scroll','dungeon','shugo','nightmare','rite')})
ASSET_FILES["od_energy"] = Path("assets/kit/energia-od.png")
ASSET_FILES["kinah_daily"] = Path("assets/kit/kinah.png")
ASSET_FILES["abyss_weekly"] = Path("assets/kit/pontos-abismo.png")
ASSET_FILES["capture_on"] = Path("assets/kit/captura-on.png")
ASSET_FILES["capture_off"] = Path("assets/kit/captura-off.png")
ASSET_FILES.update({key:Path('assets/kit')/filename for key,filename in {
    'action_reload':'acao-reload.png','action_panel':'acao-lupa.png',
    'action_help':'acao-ajuda.png','action_close':'acao-fechar.png'}.items()})
# Original canvases have different transparent margins. These render sizes
# give the supplied glyphs about 14px at rest, with room for hover zoom.
ACTION_SIZES={'action_reload':22,'action_panel':29,'action_help':25,'action_close':33}
ASSET_FILES.update({key:Path('assets/kit/capture-steps')/(key+'.png') for key in
    ('capture_step_1','capture_step_2','capture_step_3','capture_step_check','capture_step_later')})
ASSET_FILES.update(CATALOG.portraits())


class RasterAssets:
    """RasterAssets(master).get('assassin', 26) -> cached Tk PhotoImage or None.

    ``size`` accepts a square integer or (width, height) physical pixel pair.
    Images retain their original aspect ratio and transparent canvas. Unknown
    classes return None so the caller can show a neutral pending-class symbol.
    ``quality`` and ``errors`` make a degraded diagnostic fallback explicit.
    """

    def __init__(self, master, root=None):
        self.master = master
        self.root = Path(root) if root is not None else ROOT
        self.quality = "lanczos" if Image is not None else "tk-nearest-fallback"
        self.errors = {}
        self._originals = {}
        self._photos = {}

    def get(self, key, size):
        if key not in ASSET_FILES:
            return None
        width, height = (size, size) if isinstance(size, int) else size
        if (not isinstance(width, int) or not isinstance(height, int)
                or width < 1 or height < 1 or width > 1024 or height > 1024):
            raise ValueError("HUD artwork size must be 1..1024 physical pixels")
        cache_key = (key, width, height)
        if cache_key in self._photos:
            return self._photos[cache_key]
        path = self.root / ASSET_FILES[key]
        try:
            if Image is not None:
                if key not in self._originals:
                    with Image.open(path) as source:
                        self._originals[key] = source.convert("RGBA")
                source = self._originals[key]
                scale = min(width / source.width, height / source.height)
                scaled_size = (max(1, round(source.width * scale)),
                               max(1, round(source.height * scale)))
                scaled = source.resize(scaled_size, Image.Resampling.LANCZOS)
                if scaled_size == (width, height):
                    raster = scaled
                else:
                    raster = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                    # Copy RGBA without a second alpha multiplication.
                    raster.paste(scaled, ((width - scaled.width) // 2,
                                          (height - scaled.height) // 2))
                photo = ImageTk.PhotoImage(raster, master=self.master)
            else:
                if key not in self._originals:
                    self._originals[key] = tk.PhotoImage(master=self.master,
                                                        file=str(path))
                source = self._originals[key]
                factor = max(1, math.ceil(max(source.width() / width,
                                             source.height() / height)))
                scaled = source.subsample(factor, factor)
                photo = tk.PhotoImage(master=self.master, width=width,
                                      height=height)
                photo.tk.call(str(photo), "copy", str(scaled), "-to",
                              (width - scaled.width()) // 2,
                              (height - scaled.height()) // 2)
            self._photos[cache_key] = photo
            self.errors.pop(key, None)
            return photo
        except (OSError, ValueError, tk.TclError) as error:
            self.errors[key] = str(error)
            return None

    def clear(self):
        """Release this interpreter's images when its HUD is destroyed."""
        self._photos.clear()
        self._originals.clear()

    def action(self,key):
        return self.get(key,ACTION_SIZES[key])
