# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Game metadata for presentation. Combat math stays in the existing reader."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
CLASS_RANGES=[(5,8,'gladiator'),(9,12,'templar'),(13,16,'ranger'),(17,20,'assassin'),
              (21,24,'spiritmaster'),(25,28,'sorcerer'),(29,32,'cleric'),(33,36,'chanter')]
# Palette is a provisional UI reference, not a verified NCSOFT colour standard.
CLASSES={
    'gladiator':('Gladiador','#86DDF3'), 'templar':('Templário','#7DA0F9'),
    'assassin':('Assassino','#A4E79B'), 'ranger':('Arqueiro','#62B18F'),
    'sorcerer':('Feiticeiro','#B78CF2'), 'spiritmaster':('Espiritualista','#CF6BD0'),
    'cleric':('Clérigo','#E7CF7D'), 'chanter':('Cantor','#E4A55B'),
    'unknown':('Classe pendente','#D6BEA2')}


def class_key(class_id):
    if type(class_id) is int:
        return next((name for low,high,name in CLASS_RANGES if low<=class_id<=high),'unknown')
    return 'unknown'


def alpha_for(percent):
    return max(.225,min(.75,float(percent)*.0075))


def resize_box(box, edge, delta, bounds=None, minimum=(390,160), maximum=(1000,900)):
    x,y,w,h=box;dx,dy=delta;right=x+w;bottom=y+h
    if 'w' in edge:x+=dx
    if 'e' in edge:right+=dx
    if 'n' in edge:y+=dy
    if 's' in edge:bottom+=dy
    if bounds:
        bx,by,bw,bh=bounds
        x=max(bx,x);y=max(by,y);right=min(bx+bw,right);bottom=min(by+bh,bottom)
        minw,minh=min(minimum[0],bw),min(minimum[1],bh)
    else:minw,minh=minimum
    nw=max(minw,min(maximum[0],right-x));nh=max(minh,min(maximum[1],bottom-y))
    if 'w' in edge:x=right-nw
    else:right=x+nw
    if 'n' in edge:y=bottom-nh
    else:bottom=y+nh
    if bounds:
        x=max(bx,min(x,bx+bw-nw));y=max(by,min(y,by+bh-nh))
    return int(x),int(y),int(nw),int(nh)


class Catalog:
    def __init__(self):
        try:self.npcs=json.loads((ROOT/'dados/npcs-pt.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):self.npcs={}

    def target(self,snapshot,fight):
        if snapshot.get('mode')=='sample' and snapshot.get('metadata',{}).get('target_name'):
            return snapshot['metadata']['target_name']
        code=self.npc_code(snapshot,fight)
        value=self.npcs.get(str(code),{}) if code is not None else {}
        return value.get('name') or 'Nome do alvo pendente'

    def npc_code(self,snapshot,fight):
        """Resolve species within the encounter's entity lifetime, never by name."""
        floor=-1
        for previous in snapshot.get('encounters',[])+snapshot.get('group_encounters',[])+snapshot.get('observed_encounters',[]):
            if previous['flow']==fight['flow'] and previous['target']==fight['target'] and previous.get('end_us') is not None and previous['end_us']<fight['start_us'] and previous.get('end_reason') in ('abate','novo nascimento'):
                floor=max(floor,previous['end_us']+(1 if previous['end_reason']=='abate' else 0))
        end=fight.get('end_us') if fight.get('end_us') is not None else snapshot.get('clock_us',fight['start_us'])
        candidates=[h for h in snapshot.get('mob_health_history',[]) if h.get('flow')==fight['flow'] and
                    h.get('target')==fight['target'] and h.get('npc_code') and floor<=h['clock_us']<=end]
        before=[h for h in candidates if h['clock_us']<=fight['start_us']]
        latest=max(before,key=lambda h:h['clock_us']) if before else min(candidates,key=lambda h:h['clock_us']) if candidates else None
        if not latest:return None
        names={h['npc_code'] for h in candidates if h['clock_us']>=latest['clock_us']}
        return latest['npc_code'] if len(names)==1 else None

    def is_boss(self,snapshot,fight):
        """Return True/False only for unambiguous catalog metadata; else None."""
        code=self.npc_code(snapshot,fight)
        value=self.npcs.get(str(code),{}) if code is not None else {}
        flag=value.get('isBoss')
        return flag if type(flag) is bool else None

    def player_class(self,snapshot,fight,row):
        known=row.get('class_id')
        matches=[]
        if fight:
            matches=[r['class_id'] for r in snapshot.get('player_classes',[]) if r['flow']==fight['flow'] and
                     r.get('name')==row['name'] and (row.get('actor') is None or row['actor']==r['actor'])]
        sources={class_key(value) for value in matches+([known] if known is not None else [])}
        sources.discard('unknown')
        if len(sources)>1:return 'unknown'
        if known is None and len(sources)==1:return next(iter(sources))
        key=class_key(known)
        if key=='unknown' and not sources and fight:
            remembered=[]
            for hint in snapshot.get('remembered_player_classes',[]):
                if hint.get('flow')!=fight['flow'] or hint.get('name')!=row['name']:continue
                if row.get('server') is not None:
                    if row['server']!=hint.get('server'):continue
                elif not (hint.get('self_identity') and row['name']==snapshot.get('character')):
                    continue
                if row.get('actor') is not None and hint.get('actor') is not None and row['actor']!=hint['actor']:continue
                remembered.append(class_key(hint.get('class_id')))
            cached=set(remembered)-{'unknown'}
            if len(cached)==1:return next(iter(cached))
        return key


def combat_history(snapshot):
    """Only verified personal attribution and explicit party membership belong here."""
    groups=snapshot.get('group_encounters',[])
    fights=[fight for fight in groups if not fight.get('observation_only')]
    now=snapshot.get('clock_us',0)
    for personal in snapshot.get('encounters',[]):
        if personal.get('observation_only'):continue
        end=personal.get('end_us') if personal.get('end_us') is not None else now
        covered=any(group['flow']==personal['flow'] and group['target']==personal['target']
            and group['start_us']<=end and personal['start_us']<=(group.get('end_us') if group.get('end_us') is not None else now)
            for group in groups)
        if not covered:fights.append(personal)
    return sorted(fights,key=lambda fight:(fight['start_us'],fight['key']))


class TargetSelector:
    """Select existing fights for display without combining damage or HP records.

    The selected key is the only persisted state, supplied by the caller. In boss
    mode adds cannot replace the selected boss. A newer identified boss encounter
    starts its own display even if the previous boss's death packet was missed.
    Earlier encounters never pull focus backwards. Unknown species stay excluded.
    """
    def __init__(self,catalog=None):
        self.catalog=catalog if catalog is not None else Catalog()

    def choose(self,snapshot,fights,selected_key,scope,auto_target=True):
        by_key={fight['key']:fight for fight in fights}
        current=by_key.get(selected_key)
        bosses=scope in ('bosses','BOSSES')
        if not bosses:
            if not auto_target and current is not None:return selected_key
            active_keys=(snapshot.get('group_active_key'),snapshot.get('active_key'))
            active=max((by_key[key] for key in active_keys if key in by_key),
                       key=lambda fight:(fight.get('last_damage_us',fight['start_us']),fight['start_us']),default=None)
            active=active['key'] if active else None
            return active if active in by_key else None

        candidates=[fight for fight in fights if self.catalog.is_boss(snapshot,fight) is True]
        valid_keys={fight['key'] for fight in candidates}
        if current is not None and selected_key in valid_keys:
            if not auto_target:return selected_key
            # Encounter start, rather than the last hit, prevents adds or delayed
            # packets on an older boss from stealing focus. Death is not required.
            newer=[fight for fight in candidates if fight['start_us']>current['start_us']]
            return max(newer,key=lambda fight:fight['start_us'])['key'] if newer else selected_key
        return max(candidates,key=lambda fight:fight['start_us'])['key'] if candidates else None
