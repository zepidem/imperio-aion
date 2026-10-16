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


def initial_readings(snapshot):
    """Only fresh, validated initial data can dismiss preparation automatically.

    Pet per-species metadata may legitimately be sparse; the decoded initial
    collection is required, rather than inventing missing levels or balances.
    The caller latches completion within a login so quiet traffic stays usable.
    """
    live=bool(source_ready(snapshot) and snapshot.get('mode')=='live'
              and not (snapshot.get('character_selection') or {}).get('confirmed'))
    def fresh(value):
        return bool(live and value.get('available') is True
                    and not any(value.get(k) for k in
                        ('cached','manual','partial','reset_pending','reading_error','invalidated')))
    gains=snapshot.get('resource_gains') or {}
    weekly=snapshot.get('weekly_orders') or {}
    entries=snapshot.get('activity_entries') or {}
    pets=snapshot.get('pet_collection') or {}
    return {
        'Personagem':bool(live and (snapshot.get('login_session') or {}).get('confirmed')),
        'Recursos':all(fresh(value) for value in (snapshot.get('od_energy') or {},
                   gains.get('kinah_daily') or {},gains.get('abyss_weekly') or {})),
        'Diárias e Semanais':all(fresh(value) for value in (
            snapshot.get('daily_duty') or {},weekly.get('altgard') or {},
            weekly.get('latesran') or {},*(entries.get(key) or {} for key in
            ('daily_dungeon','shugo_party','nightmare','ascension_rite')))),
        'Mascotes':bool(live and pets.get('wire_decoder_validated')
                        and pets.get('character_scope') and not pets.get('cache_display')),
    }
