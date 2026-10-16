# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Local ID -> Portuguese name/original portrait catalog. No runtime HTTP."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent


class PetCatalog:
    def __init__(self,path=None):
        self.entries={};self.errors=[]
        try:
            file=Path(path) if path is not None else ROOT/'dados/mascotes-pt.json'
            if file.stat().st_size>2*1024*1024:raise ValueError('Catálogo excede o limite')
            data=json.loads(file.read_text(encoding='utf-8-sig'))
            if data.get('schema_version')!=1 or data.get('language')!='pt-BR':
                raise ValueError('Perfil do catálogo de mascotes inválido')
            entries=data.get('entries')
            if not isinstance(entries,dict) or len(entries)>500:raise ValueError('Lista de mascotes inválida')
            for identifier,entry in entries.items():
                try:
                    if not identifier.isdecimal() or str(int(identifier))!=identifier or not 0<int(identifier)<1000000:
                        raise ValueError('ID inválido')
                    if not isinstance(entry,dict):raise ValueError('Registro inválido')
                    name=entry.get('name')
                    if not isinstance(name,str) or not 0<len(name)<=100 or any(ord(c)<32 for c in name):
                        raise ValueError('Nome inválido')
                    if entry.get('icon_key')!='pet_'+identifier or entry.get('icon_path')!='assets/pets/'+identifier+'.webp':
                        raise ValueError('Caminho de retrato inválido')
                    self.entries[identifier]=dict(name=name,icon_key=entry['icon_key'],icon_path=entry['icon_path'])
                    catalog_name=entry.get('catalog_name')
                    if isinstance(catalog_name,str) and 0<len(catalog_name)<=100 and not any(ord(c)<32 for c in catalog_name):
                        self.entries[identifier]['catalog_name']=catalog_name
                except (ValueError,TypeError):self.errors.append('Mascote '+identifier+' ignorada')
        except (OSError,ValueError,TypeError,AttributeError) as exc:
            self.errors.append('Catálogo indisponível: '+str(exc))

    def get(self,identifier):
        entry=self.entries.get(str(identifier))
        return dict(entry) if entry else None

    def portraits(self):
        return {e['icon_key']:Path(e['icon_path']) for e in self.entries.values()}


CATALOG=PetCatalog()
