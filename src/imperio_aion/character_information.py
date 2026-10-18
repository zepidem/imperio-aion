# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Honest per-field presentation of already decoded facts, never a data source."""
from activity_view import activity_rows
from od_energy import energy_parts
from resource_gains import gain_presentation
from presentation import class_key,CLASSES
from functools import lru_cache
from skill_catalog import SkillCatalog
from display_identity import LOCAL_PLAYER_LABEL

@lru_cache(maxsize=1)
def _effect_names():
    return {str(code):item['name'] for code,item in SkillCatalog().entries.items()
            if item.get('name','').startswith('Pedra Divina:')}

def reading_status(value):
    if not isinstance(value,dict):return 'Pendente'
    if value.get('reading_error'):return 'Leitura parcial'
    if value.get('cached') or value.get('cache_display'):return 'Última leitura'
    if value.get('partial'):return 'Parcial'
    return 'Confirmado' if value.get('available') else 'Pendente'

def character_information(snapshot,character):
    cache=snapshot.get('character_cache',{});identity=cache.get('selected') or {}
    confirmed=bool(snapshot.get('login_session',{}).get('confirmed'))
    cls=class_key(cache.get('class_id'));server=identity.get('server')
    guild=snapshot.get('login_session',{}).get('guild')
    rows=[('Personagem',LOCAL_PLAYER_LABEL,'Confirmado' if confirmed else 'Pendente'),
        ('Guilda',guild if isinstance(guild,str) and guild else 'Ainda não interpretada na captura','Confirmado' if guild and confirmed else 'Pendente'),
        ('Classe',CLASSES[cls][0],('Confirmado' if confirmed else 'Última leitura') if cls!='unknown' else 'Pendente'),
        ('Servidor',str(server) if server is not None else 'Não recebido',('Confirmado' if confirmed else 'Última leitura') if server is not None else 'Pendente'),
        ('Login','Identidade vinculada nesta conexão' if confirmed else 'Aguardando login com a captura ativa','Confirmado' if confirmed else 'Pendente')]
    od=snapshot.get('od_energy',{});rows.append(('Energia Od',''.join(energy_parts(od)),reading_status(od)))
    for key,name in (('kinah_daily','Kinah diário'),('abyss_weekly','Abismo semanal')):
        value=snapshot.get('resource_gains',{}).get(key,{})
        rows.append((name,gain_presentation(key,value)['text'],reading_status(value)))
    for row in activity_rows(snapshot):
        if row['key']=='daily':value=snapshot.get('daily_duty',{})
        elif row['key'] in ('altgard','abyss'):value=snapshot.get('weekly_orders',{}).get('latesran' if row['key']=='abyss' else 'altgard',{})
        else:value=snapshot.get('activity_entries',{}).get(row['key'],{})
        rows.append((row['name'],row['count'],reading_status(value)))
    pets=snapshot.get('pet_collection',{});entries=pets.get('entries',[])
    rows.append(('Mascotes',str(len(entries))+' na coleção' if entries else 'Coleção não recebida',('Última leitura' if pets.get('cache_display') else 'Parcial' if pets.get('relog_required') or any(e.get('souls') is None and e.get('level')!=3 for e in entries) else 'Confirmado') if entries else 'Pendente'))
    rosters=snapshot.get('group_rosters',[]);members=rosters[-1].get('members',[]) if rosters else []
    rows.append(('Grupo',str(len(members))+' integrantes identificados' if members else 'Sem grupo reconhecido','Confirmado' if members else 'Pendente'))
    known=_effect_names();catalog=SkillCatalog()
    effects=sorted({known[catalog.key(item.get('code'))] for fight in snapshot.get('group_encounters',[])+snapshot.get('encounters',[]) for member in fight.get('members',[fight]) for item in member.get('skills',[]) if catalog.key(item.get('code')) in known})
    rows.append(('Pedras Divinas',', '.join(effects) if effects else 'Nenhum efeito observado','Observado' if effects else 'Aguardando combate'))
    rows.extend([('Captura',snapshot.get('status','Não iniciada'),'Ativa' if snapshot.get('running') else 'Inativa'),('Pacotes observados',str(snapshot.get('packets',0)),'Contagem local'),('Registros não interpretados',str(snapshot.get('rejected',0)),'Contagem local')])
    return rows
