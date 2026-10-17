# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Inert startup policy: check before launch, never silently install an update."""
def startup_action(snapshot, *, launch_mode, check_finished, already_requested=False):
    if not launch_mode or not check_finished or already_requested:return None
    if snapshot.get('phase') in ('up_to_date','offline_ready') and snapshot.get('can_launch'):
        return 'launch'
    return None


def apply_startup_view(view,snapshot,*,launch_mode):
    view=dict(view)
    if snapshot.get('phase')=='offline_ready':
        installed=bool(snapshot.get('can_launch'))
        view.update(heading='Aplicativo disponível offline' if installed else 'Instale o Império AION',
            subtitle='Nenhuma consulta de atualização foi enviada.',
            body='Para consultar e baixar uma versão, escolha Verificar versão.\nO servidor receberá seu IP e informações da requisição.',
            action='launch' if installed else 'check',primary='Abrir aplicativo' if installed else 'Verificar versão',
            secondary='Verificar versão' if installed else None,secondary_action='check' if installed else None,
            symbol='play' if installed else 'download',progress=False,tone='normal',
            note='A consulta automática é opcional e pode ser alterada abaixo.')
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
