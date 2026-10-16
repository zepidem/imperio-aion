# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Human-facing projection of authenticated updater state; no OS operations."""
from __future__ import annotations

BUSY = frozenset({'detecting', 'checking', 'downloading', 'verifying', 'awaiting_permission', 'installing', 'launching'})


def present(snapshot):
    phase = snapshot.get('phase', 'idle')
    installed = snapshot.get('installed_version')
    first = not bool(installed)
    view = {
        'phase': phase, 'tone': 'normal', 'symbol': 'download',
        'heading': 'Conferindo o aplicativo',
        'subtitle': 'Vamos identificar a versão instalada e a atualização disponível.',
        'body': 'Aguarde a conferência dos arquivos de instalação.',
        'action': None, 'primary': 'Conferindo…', 'secondary': None,
        'secondary_action': None, 'note': 'O jogo pode permanecer aberto.',
        'retained': 'Seus atalhos, posição da HUD e arquivos salvos continuam na mesma pasta de dados.',
        'progress': phase in BUSY, 'progress_label': 'Conferindo a versão disponível',
        'steps': False,
    }
    if phase in ('idle', 'missing'):
        view.update(heading='Instale o Império AION', subtitle='Confira a versão disponível para começar.',
                    body='A captura está incluída no aplicativo. Não exige Npcap, Python ou ExitLag.',
                    action='check', primary='Verificar versão', progress=False,
                    retained='As configurações serão salvas na sua conta do Windows.')
    elif phase in ('detecting', 'checking'):
        pass
    elif phase == 'ready':
        view.update(heading='Instale o Império AION' if first else 'Atualização disponível',
                    subtitle='Tudo pronto para começar a acompanhar seus combates.' if first else 'A nova versão está pronta para ser instalada.',
                    body='A captura está incluída no aplicativo.\nNão exige Npcap, Python ou ExitLag.' if first else 'Atualização da v%s para a v%s.\nAs preferências e os arquivos salvos serão mantidos.' % (installed, snapshot.get('available_version', '—')),
                    action='download', primary='Instalar aplicativo' if first else 'Atualizar agora',
                    secondary='Agora não', secondary_action='close', progress=False)
    elif phase == 'downloading':
        view.update(heading='Baixando o aplicativo' if first else 'Baixando a atualização',
                    subtitle='Vamos conferir os arquivos antes de instalar.',
                    primary='Baixando…', progress_label='Recebendo os arquivos', steps=True,
                    secondary='Cancelar download' if snapshot.get('can_cancel') else None,
                    secondary_action='cancel_download')
    elif phase == 'verifying':
        view.update(heading='Conferindo os arquivos', subtitle='A instalação só continua se o pacote for aprovado.',
                    primary='Conferindo…', symbol='shield', progress_label='Verificando assinatura e arquivos', steps=True)
    elif phase in ('downloaded', 'blocked_running'):
        view.update(heading='Feche a HUD para continuar' if phase == 'blocked_running' else 'Atualização pronta para instalar',
                    subtitle='Falta encerrar a versão em uso.' if phase == 'blocked_running' else 'O pacote foi conferido e aprovado.',
                    body='Use o X dourado da HUD para fechar o Império AION normalmente.\nAssim, ele pode salvar os dados antes da troca de versão.' if phase == 'blocked_running' else 'O Windows vai pedir permissão para instalar a nova versão.',
                    symbol='window' if phase == 'blocked_running' else 'shield',
                    action='install', primary='Verificar novamente' if phase == 'blocked_running' else 'Continuar instalação',
                    secondary='Fazer depois', secondary_action='close', progress=False,
                    note='O AION 2 pode permanecer aberto.')
    elif phase == 'awaiting_permission':
        view.update(heading='Autorize a instalação', subtitle='O Windows vai pedir permissão para instalar a nova versão.',
                    body='Confirme a solicitação na janela do Windows para continuar.',
                    primary='Aguardando permissão…', symbol='shield', progress_label='Aguardando sua autorização',
                    note='A permissão é solicitada pelo Windows.')
    elif phase == 'installing':
        view.update(heading='Instalando o Império AION' if first else 'Instalando a atualização',
                    subtitle='Os arquivos aprovados estão sendo colocados na nova pasta de versão.',
                    primary='Instalando…', progress_label='Finalizando a instalação', steps=True,
                    note='Aguarde a conclusão desta etapa.')
    elif phase in ('installed', 'up_to_date'):
        view.update(heading='Atualização concluída' if phase == 'installed' and snapshot.get('previous_version') else 'Instalação concluída' if phase == 'installed' else 'Você está na versão atual',
                    subtitle='O Império AION v%s está pronto para abrir.' % (installed or snapshot.get('available_version') or '—'),
                    body='Versão instalada conferida.\nInicie a captura para acompanhar o DPS. Os saldos completos são enviados na entrada do personagem.',
                    primary='Abrir Império AION', action='launch', symbol='check', tone='success', progress=False,
                    secondary='Concluir' if phase == 'installed' else None, secondary_action='close',
                    note='Ao abrir, o Windows pode pedir permissão para a captura.')
    elif phase == 'launching':
        view.update(heading='Abrindo o Império AION', subtitle='A versão instalada será conferida antes de abrir.',
                    primary='Abrindo…', symbol='play', progress_label='Conferindo e abrindo o aplicativo',
                    note='O Windows pode pedir permissão para a captura.')
    elif phase == 'launched':
        view.update(heading='Abertura solicitada', subtitle='O pedido de abertura foi enviado ao Windows.',
                    body='Na HUD, inicie a captura para acompanhar o DPS.\nOs saldos de Diárias e Semanais são recebidos na entrada do personagem.',
                    primary='Concluir', action='close', symbol='check', tone='success', progress=False,
                    note='Você pode fechar este atualizador.')
    elif phase == 'cancelled':
        permission = snapshot.get('cancelled_operation') in ('install', 'launch', 'permission') or snapshot.get('download_ready')
        opening=snapshot.get('cancelled_operation')=='launch'
        view.update(heading='Abertura não autorizada' if opening else 'Instalação não autorizada' if permission else 'Download cancelado',
                    subtitle='A solicitação do Windows foi cancelada.' if permission else 'Nenhuma atualização foi instalada.',
                    body='Quando quiser continuar, você poderá autorizar a instalação.\nSua versão atual permanece instalada.' if permission else 'Você pode retomar a atualização quando quiser.',
                    primary='Abrir Império AION' if opening and snapshot.get('can_launch') else 'Continuar instalação' if snapshot.get('can_install') else 'Tentar novamente',
                    action='launch' if opening and snapshot.get('can_launch') else 'install' if snapshot.get('can_install') else 'download' if snapshot.get('can_download') else 'check',
                    secondary='Fazer depois', secondary_action='close', symbol='shield' if permission else 'download', progress=False)
    elif phase == 'corrupted':
        view.update(heading='A instalação precisa ser conferida', subtitle='O aplicativo não será aberto com arquivos diferentes do pacote esperado.',
                    body='Baixe novamente a versão autenticada para reparar a instalação.\nOs dados salvos do aplicativo permanecem na sua conta do Windows.',
                    primary='Verificar versão', action='check', symbol='warning', tone='error', progress=False)
    else:
        error = str(snapshot.get('error') or snapshot.get('message') or '').lower()
        rejected = any(word in error for word in ('assinatura', 'integridade', 'autentic', 'adulter', 'hash', 'arquivo recusado', 'conteúdo não corresponde'))
        view.update(heading='O arquivo não passou na verificação' if rejected else 'Não foi possível concluir',
                    subtitle='A instalação foi interrompida antes de alterar o aplicativo.' if rejected else 'Confira os detalhes e tente novamente.',
                    body='O conteúdo recebido não corresponde ao pacote esperado. Baixe novamente. Se a recusa continuar, entre em contato com o suporte.' if rejected else 'Sua versão atual e os dados salvos foram mantidos. Se o problema continuar, entre em contato com o suporte.',
                    primary='Baixar novamente' if rejected and snapshot.get('can_download') else 'Tentar novamente',
                    action='download' if snapshot.get('can_download') else 'check', symbol='warning', tone='error', progress=False,
                    secondary='Fazer depois', secondary_action='close')
    return view
