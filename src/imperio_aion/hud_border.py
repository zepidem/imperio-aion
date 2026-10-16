# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""One continuous border shared by the frame and its eight resize hit areas."""
from functools import lru_cache
from PIL import Image, ImageDraw, ImageOps


@lru_cache(maxsize=8)
def _corner(background, outline, radius, inset):
    # Supersample only the small curves, so resizing large HUDs stays cheap.
    scale=4;extent=radius+inset+4;side=extent*2
    high=Image.new('RGB',(side*scale,side*scale),background)
    ImageDraw.Draw(high).rounded_rectangle(
        (inset*scale,inset*scale,(side-inset-1)*scale,(side-inset-1)*scale),
        radius=radius*scale,outline=outline,width=scale)
    return high.resize((side,side),Image.Resampling.LANCZOS).crop((0,0,extent,extent))


def border_raster(width,height,background='#241B15',outline='#A47D49'):
    width,height=max(1,int(width)),max(1,int(height))
    radius=max(0,min(14,(min(width,height)-9)//2));inset=2
    corner=_corner(background,outline,radius,inset)
    extent=corner.width
    result=Image.new('RGB',(width,height),background)
    if width<extent*2 or height<extent*2:return result
    top=corner.crop((extent-1,0,extent,extent)).resize((width-2*extent,extent),Image.Resampling.NEAREST) if width>extent*2 else None
    left=corner.crop((0,extent-1,extent,extent)).resize((extent,height-2*extent),Image.Resampling.NEAREST) if height>extent*2 else None
    if top:
        result.paste(top,(extent,0));result.paste(ImageOps.flip(top),(extent,height-extent))
    if left:
        result.paste(left,(0,extent));result.paste(ImageOps.mirror(left),(width-extent,extent))
    result.paste(corner,(0,0));result.paste(ImageOps.mirror(corner),(width-extent,0))
    result.paste(ImageOps.flip(corner),(0,height-extent))
    result.paste(ImageOps.flip(ImageOps.mirror(corner)),(width-extent,height-extent))
    return result
