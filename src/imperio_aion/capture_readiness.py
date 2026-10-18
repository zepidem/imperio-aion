# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Capture acquisition readiness is independent of requested ON and login data."""

def source_ready(snapshot):
    # Older diagnostics and synthetic UI fixtures predate this field. Actual
    # live sessions always initialize it false before starting their worker.
    return bool(snapshot.get('running') and not snapshot.get('error')
                and snapshot.get('capture_ready',True) is True)


def preparing(snapshot):
    return bool(snapshot.get('running') and not snapshot.get('error')
                and not source_ready(snapshot))


def onboarding_progress(snapshot,login_ready=False):
    capture=source_ready(snapshot)
    selection=snapshot.get('character_selection') or {}
    visited=bool(login_ready or capture and selection.get('visited'))
    done=(visited, bool(visited and capture),bool(login_ready))
    active=next((i for i,value in enumerate(done) if not value),None)
    return done,active


def initial_reading_details(snapshot):
    """Keep confirmed login baselines separate from the on-demand duty list."""
    live=bool(source_ready(snapshot) and snapshot.get('mode')=='live'
              and not (snapshot.get('character_selection') or {}).get('confirmed'))
    def fresh(value):
        return bool(live and isinstance(value,dict) and value.get('available') is True
                    and not any(value.get(k) for k in
                        ('cached','manual','partial','reset_pending','reading_error','invalidated')))
    gains=snapshot.get('resource_gains') or {}
    weekly=snapshot.get('weekly_orders') or {}
    entries=snapshot.get('activity_entries') or {}
    pets=snapshot.get('pet_collection') or {}
    return {
        'Personagem':bool(live and (snapshot.get('login_session') or {}).get('confirmed')),
        'Energia Od':fresh(snapshot.get('od_energy')),
        'Kinah diário':fresh(gains.get('kinah_daily')),
        'Abismo semanal':fresh(gains.get('abyss_weekly')),
        'Dever diário':fresh(snapshot.get('daily_duty')),
        'Ordem Altgard':fresh(weekly.get('altgard')),
        'Ordem Latesran':fresh(weekly.get('latesran')),
        **{label:fresh(entries.get(key)) for key,label in (
            ('daily_dungeon','Masmorra diária'),('shugo_party','Shugo Festa'),
            ('nightmare','Pesadelo'),('ascension_rite','Rito de Ascensão'))},
        'Mascotes':bool(live and pets.get('wire_decoder_validated')
                        and pets.get('character_scope') and not pets.get('cache_display')),
    }


def initial_readings(snapshot):
    """Actual confirmation status, including an honestly pending daily duty."""
    fields=initial_reading_details(snapshot)
    return {'Personagem':fields['Personagem'],
        'Recursos':all(fields[key] for key in ('Energia Od','Kinah diário','Abismo semanal')),
        'Diárias e Semanais':all(fields[key] for key in ('Dever diário','Ordem Altgard',
            'Ordem Latesran','Masmorra diária','Shugo Festa','Pesadelo','Rito de Ascensão')),
        'Mascotes':fields['Mascotes']}


def initial_loading_complete(snapshot):
    # 1c8d can contain an expired duty list until the user opens Dever in-game.
    # Relogging does not renew that list. It must not hold the whole app hostage.
    # This does not confirm its counter: cached/reset/invalid duty stays pending.
    # Every other baseline still requires fresh data from this identified login.
    fields=initial_reading_details(snapshot)
    return all(value for name,value in fields.items() if name!='Dever diário')


def initial_loading_status(snapshot):
    fields=initial_reading_details(snapshot);ready=initial_loading_complete(snapshot)
    return dict(ready=ready,groups=initial_readings(snapshot),
        pending_fields=[name for name,value in fields.items() if not value],
        deferred_fields=['Dever diário'] if ready and not fields['Dever diário'] else [])
