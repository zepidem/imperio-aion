# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Explicit presentation-only projection; raw collector state is never served."""
from pathlib import Path
from combat_charts import chart_series, points_for, row_key, skill_colors
from presentation import CLASSES
from hud_assets import ASSET_FILES
from activity_view import activity_rows
from hud_view import pet_progress
from od_energy import energy_parts
from resource_gains import gain_presentation
from display_identity import LOCAL_PLAYER_LABEL,player_label

ROOT = Path(__file__).resolve().parent


def projection(app, snapshot, fight, rows, names=True, resources=False):
    # Fail closed if an older caller still passes the rejected v61 nearby view.
    if fight and fight.get('observation_only'):fight=None;rows=[]
    assets = {}

    def asset(key, path=None):
        path = path or ASSET_FILES.get(key)
        if path is None:
            return ''
        resolved = (ROOT / path).resolve()
        if not resolved.is_relative_to((ROOT / 'assets').resolve()) or not resolved.is_file():
            return ''
        assets[key] = resolved
        return 'asset/' + key

    catalog = app.panel.skill_catalog
    selected = next((r for r in rows if r['name'] == app.skill_member.get()), None)
    players, skills, partial = chart_series(snapshot, fight, rows, app.character.get().strip(), catalog)
    player_keys = {row_key(row): 'j' + str(i) for i, row in enumerate(rows)}
    output_rows = []
    for i, row in enumerate(rows):
        cls = row.get('class_key', 'unknown')
        output_rows.append(dict(key=player_keys[row_key(row)], name=player_label(row) if names else 'Jogador ' + str(i + 1),
            icon=asset(cls), className=CLASSES.get(cls, CLASSES['unknown'])[0],
            color=CLASSES.get(cls, CLASSES['unknown'])[1], damage=row.get('damage'),
            dps=row.get('dps_average'), seconds=(row.get('duration_us') or 0) / 1e6,
            share=row.get('damage_share_percent')))
    selected_key = player_keys.get(row_key(selected)) if selected else None
    items = selected.get('skills', []) if selected else []
    colors = skill_colors(items)
    output_skills = []
    for item in sorted(items, key=lambda s: -(s.get('damage') or 0)):
        code = str(item['code'])
        key = catalog.key(code)
        entry = catalog.entries.get(key) or catalog.entries.get(str(int(code) // 10000 * 10000)) or {}
        filename = entry.get('icon', '')
        icon = asset('skill_' + code, Path('assets/skills') / filename) if filename and Path(filename).name == filename else ''
        output_skills.append(dict(key=code, name=catalog.name(code, item.get('name', '')), icon=icon,
            records=item.get('records'), damage=item.get('damage'), color=colors[code],
            share=100 * (item.get('damage') or 0) / selected['damage'] if selected and selected.get('damage') else None))

    def curves(series, kind):
        output = []
        for line in series:
            if kind == 'skills' and (not selected or line['member'] != row_key(selected)):
                continue
            code = line.get('code', '')
            key = player_keys.get(line['key']) if kind == 'group' else code
            if key is None:
                continue
            entry = dict(key=key, color=line.get('color', colors.get(code, '#D6BEA2')),
                partial=line.get('partial', False))
            for mode in ('total', 'second'):
                points = points_for(line, mode)
                # Bounded presentation samples, exact final cumulative damage.
                stride = max(1, (len(points) + 1199) // 1200)
                values = points[::stride]
                if points and (not values or values[-1] != points[-1]):
                    values.append(points[-1])
                entry[mode] = values
            output.append(entry)
        return output

    target = app.catalog.target(snapshot, fight) if fight else 'Aguardando combate'
    waiting_title='Confira a captura' if snapshot.get('error') else 'Inicie a captura'
    waiting_note='Abra o diagnóstico no aplicativo.' if snapshot.get('error') else 'O DPS pode ser iniciado durante a dungeon.'
    panel = dict(kind='painel', title='Histórico de combate', target=target,
        ready=app.combat_ready(snapshot),
        waitingTitle=waiting_title, waitingNote=waiting_note,
        rows=output_rows, skills=output_skills,
        selected=selected_key, selectedSkill=app.panel.skill_chart.selected,
        groupFocus=player_keys.get(app.panel.group_chart.selected),
        selectedName=next((r['name'] for r in output_rows if r['key'] == selected_key), ''),
        groupMode=app.panel.group_chart.mode, skillMode=app.panel.skill_chart.mode,
        total=fight.get('damage') if fight else None, dps=fight.get('dps_average') if fight else None,
        seconds=(fight.get('duration_us') or 0) / 1e6 if fight else 0,
        partial=partial or bool(fight and fight.get('partial')), groupCurves=curves(players, 'group'), skillCurves=curves(skills, 'skills'))
    view = app.hud_page.get()
    hud = dict(kind='hud', view=view, ready=app.combat_ready(snapshot) if view=='combat' else bool(app.login_ready), target=target, logo=asset('mascot'),
        character=LOCAL_PLAYER_LABEL if names and app.login_ready else '', rows=output_rows,
        waitingTitle=waiting_title if view=='combat' else 'Dados do personagem pendentes',
        waitingNote=waiting_note if view=='combat' else 'Relogue quando puder, com a captura ativa. O DPS já pode ser usado.',
        total=panel['total'], dps=panel['dps'], seconds=panel['seconds'], partial=panel['partial'], resources=[])
    if view == 'duty':
        hud['activities'] = [dict(name=r['name'], count=r['count'], note=r['note']) for r in activity_rows(snapshot)]
    elif view == 'pets':
        state = snapshot.get('pet_collection', {})
        entries = app.hud_view.pet_table.visible_entries() if app.hud_view else state.get('entries', [])[:5]
        hud['pets'] = []
        for pet in entries:
            caption, note, complete = pet_progress(pet, state)
            hud['pets'].append(dict(name=pet['name'], caption=caption, note=note, complete=complete,
                icon=asset('pet_' + str(pet['pet_id'])), souls=pet.get('session_souls', 0)))
    if app.hud_view and view == 'combat':
        offset = app.hud_view.table.offset
        hud['rows'] = output_rows[offset:offset + app.hud_view.table.visible_capacity()]
    if resources:
        od = energy_parts(snapshot.get('od_energy', {}))
        hud['resources'].append(dict(text=''.join(od), icon=asset('od_energy')))
        for key in ('kinah_daily', 'abyss_weekly'):
            item = gain_presentation(key, snapshot.get('resource_gains', {}).get(key))
            hud['resources'].append(dict(text=item['text'], icon=asset(key)))
    return dict(hud=hud, painel=panel), assets
