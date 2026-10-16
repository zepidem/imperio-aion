# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Offline names and original skill artwork; unknown IDs remain explicit."""
from pathlib import Path
import json
from PIL import Image,ImageTk

ROOT=Path(__file__).resolve().parent
class SkillCatalog:
    def __init__(self,root=ROOT):
        self.root=Path(root);self.photos={};self.errors={}
        try:data=json.loads((self.root/'dados/skills-pt.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):data={}
        self.names=data.get('names',{});self.entries=data.get('entries',{})

    def key(self,code):
        try:code=int(code)
        except (TypeError,ValueError):return None
        if str(code) in self.names:return str(code)
        if 10_000_000<=code<20_000_000:return str(code//10000*10000)
        if 3_000_000<=code<3_100_000:return str(code*10+1)
        return str(code)

    def name(self,code,fallback=''):
        key=self.key(code);name=self.names.get(key)
        if name:return name
        return fallback if fallback and not fallback.startswith(('Codigo ','Código ')) else 'Habilidade não identificada'

    def photo(self,master,code,size=28):
        key=self.key(code)
        # The icon index contains base skill IDs, not every level/variant ID.
        if key not in self.entries:
            try:base=str(int(code)//10000*10000)
            except (TypeError,ValueError):return None
            key=base if base in self.entries else None
        if key is None:return None
        cache=(key,size)
        if cache not in self.photos:
            filename=self.entries[key].get('icon','')
            if Path(filename).name!=filename:return None
            try:
                with Image.open(self.root/'assets/skills'/filename) as im:
                    im=im.convert('RGBA');im.thumbnail((size,size),Image.Resampling.LANCZOS)
                    padded=Image.new('RGBA',(size+8,size),(0,0,0,0));padded.alpha_composite(im,(0,(size-im.height)//2))
                    self.photos[cache]=ImageTk.PhotoImage(padded,master=master)
            except (OSError,ValueError) as error:self.errors[key]=str(error);return None
        return self.photos[cache]
