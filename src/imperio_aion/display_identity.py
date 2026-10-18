# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""User-facing labels only. Names, actor IDs and selection keys remain intact."""
LOCAL_PLAYER_LABEL = 'Você'
PENDING_PLAYER_LABEL = 'Sincronizando'


def name_pending(row):
    """Only explicit peer-name state drives the presentation placeholder."""
    return row.get('self_identity') is not True and row.get('identity_pending') is True


def player_label(row):
    if row.get('self_identity') is True:return LOCAL_PLAYER_LABEL
    return PENDING_PLAYER_LABEL if name_pending(row) else str(row.get('name') or '')


def local_player_rows(rows, local_actor=None):
    """Copy UI rows and mark only an already independently proven local actor."""
    return [dict(row, self_identity=(row.get('self_identity') is True or
                 type(local_actor) is int and type(row.get('actor')) is int and
                 row['actor'] == local_actor)) for row in rows]
