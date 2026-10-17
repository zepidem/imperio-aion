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
        try:classes=json.loads((ROOT/'dados/skills-pt.json').read_text(encoding='utf-8')).get('classes',{})
        except (OSError,ValueError):classes={}
        # Images are reused across jobs: Gladiator 11280000 uses ICON_TE.
        # Only the explicit SkillAcquireData class association is evidence.
        self.skill_classes={code:job for code,job in classes.items()
            if isinstance(code,str) and 1<=len(code)<=9 and code.isascii() and code.isdecimal()
            and int(code)>0 and isinstance(job,str) and job in CLASSES and job!='unknown'} if isinstance(classes,dict) else {}

    def target(self,snapshot,fight):
        if snapshot.get('mode')=='sample' and snapshot.get('metadata',{}).get('target_name'):
            return snapshot['metadata']['target_name']
        codes=self.npc_candidates(snapshot,fight)
        names={self.npcs.get(str(code),{}).get('name') for code in codes}
        return next(iter(names)) if len(names)==1 and None not in names else 'Nome do alvo pendente'

    def npc_candidates(self,snapshot,fight):
        """Resolve species within the encounter's entity lifetime, never by name."""
        floor=-1
        for previous in snapshot.get('encounters',[])+snapshot.get('group_encounters',[])+snapshot.get('observed_encounters',[]):
            if previous['flow']==fight['flow'] and previous['target']==fight['target'] and previous.get('end_us') is not None and previous['end_us']<fight['start_us'] and previous.get('end_reason') in ('abate','novo nascimento'):
                floor=max(floor,previous['end_us']+(1 if previous['end_reason']=='abate' else 0))
        for boundary in snapshot.get('npc_identity_floors',[]):
            if boundary['flow']==fight['flow'] and boundary['target'] in (None,fight['target']) and boundary['clock_us']<=fight['start_us']:
                floor=max(floor,boundary['clock_us'])
        end=fight.get('end_us') if fight.get('end_us') is not None else snapshot.get('clock_us',fight['start_us'])
        identity=[h for h in snapshot.get('npc_identity_history',[]) if h.get('valid_until_us') is None or h['valid_until_us']>=fight['start_us']]
        candidates=[h for h in snapshot.get('mob_health_history',[])+identity if h.get('flow')==fight['flow'] and
                    h.get('target')==fight['target'] and h.get('npc_code') and floor<=h['clock_us']<=end]
        before=[h for h in candidates if h['clock_us']<=fight['start_us']]
        latest=max(before,key=lambda h:h['clock_us']) if before else min(candidates,key=lambda h:h['clock_us']) if candidates else None
        if latest:
            codes={h['npc_code'] for h in candidates if h['clock_us']>=latest['clock_us']}
            return codes if len(codes)==1 else set()
        # Exact species metadata above always wins over the static skill table.
        # Keep all possible NPC owners; never select a difficulty by guessing.
        rows=[h for h in snapshot.get('npc_skill_identity_history',[]) if h['flow']==fight['flow'] and h['target']==fight['target']
              and floor<=h['clock_us']<=end and h.get('npc_role_us') is not None and h['npc_role_us']<=end
              and (h.get('valid_until_us') is None or h['valid_until_us']>=fight['start_us'])]
        if not rows:return set()
        owners=set(rows[0]['npc_codes'])
        for row in rows[1:]:owners.intersection_update(row['npc_codes'])
        return owners

    def npc_code(self,snapshot,fight):
        codes=self.npc_candidates(snapshot,fight)
        return next(iter(codes)) if len(codes)==1 else None

    def is_boss(self,snapshot,fight):
        """Return True/False only for unambiguous catalog metadata; else None."""
        codes=self.npc_candidates(snapshot,fight)
        if not codes:return None
        flags=[self.npcs.get(str(code),{}).get('isBoss') for code in codes]
        if all(flag is True for flag in flags):return True
        if all(flag is False for flag in flags):return False
        return None


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
        if key=='unknown' and not sources:
            # Cosmetic class only, from acquired job skills. This never
            # associates an actor with a name, roster slot or the local player.
            # Receiving a name without a class must not remove this appearance.
            observed=set()
            for skill in row.get('skills',[]):
                try:code=int(skill['code'])
                except (KeyError,TypeError,ValueError):continue
                job=self.skill_classes.get(str(code)) or self.skill_classes.get(str(code//10000*10000))
                if job:observed.add(job)
            if len(observed)==1:return next(iter(observed))
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
    proofs={item['flow']:item for item in snapshot.get('local_combat_actors',[])}
    local={flow:item['actor'] for flow,item in proofs.items()}
    groups=[fight for fight in snapshot.get('group_encounters',[]) if
        fight.get('flow') not in local or (fight.get('end_us') is not None and
            fight['end_us']<proofs[fight['flow']].get('first_us',0)) or any(member.get('actor')==local[fight['flow']]
            for member in fight.get('members',[]))]
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

    def includes(self,snapshot,fight,scope):
        return scope not in ('bosses','BOSSES') or self.catalog.is_boss(snapshot,fight) is True

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
