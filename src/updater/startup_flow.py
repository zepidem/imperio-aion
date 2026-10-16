# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Inert startup policy: check before launch, never silently install an update."""
def startup_action(snapshot, *, launch_mode, check_finished, already_requested=False):
    if not launch_mode or not check_finished or already_requested:return None
    if snapshot.get('phase')=='up_to_date' and snapshot.get('can_launch'):
        return 'launch'
    return None


def apply_startup_view(view,snapshot,*,launch_mode):
    view=dict(view)
    if launch_mode and snapshot.get('phase')=='ready' and snapshot.get('can_launch'):
        view.update(secondary='Abrir versão instalada',secondary_action='launch',
            note='Você escolhe quando instalar. O Windows pode pedir permissão ao abrir.')
    if snapshot.get('phase') in ('offline','error') and snapshot.get('can_launch'):
        view.update(heading='Não foi possível verificar a atualização',
            subtitle='A versão instalada foi conferida e continua disponível.',
            body='Você pode abrir a versão instalada ou tentar verificar novamente.\nNenhuma atualização será instalada sem uma nova conferência.',
            action='launch',primary='Abrir versão instalada',symbol='play',progress=False,
            secondary='Tentar novamente',secondary_action='check',tone='normal',
            note='A abertura usa somente a instalação já autenticada no computador.')
    return view
