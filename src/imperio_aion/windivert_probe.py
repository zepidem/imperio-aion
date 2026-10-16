# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Self-contained passive capture proof. Only synthetic localhost TCP is read.

The game and ExitLag may remain closed. No user cache, login, protocol payload
or internet request is used. --check does not open a capture/driver handle.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import socket
import time

from native_probe import CASES,_run_case
from native_windivert import NativeCapture,probe


def run(check=False,output=None,on_case=None):
    capability=probe()
    result=dict(product='Império AION',monitor_version='0.8.55',kind='windivert-passive-proof',
        started_utc=datetime.now(timezone.utc).isoformat(),capability=capability,cases=[],
        game_traffic_validated=False,direct_game_traffic_validated=False,exitlag_required=False,
        npcap_required=False,driver_loaded=False,status='UNAVAILABLE',
        scope='Somente TCP localhost criado por esta prova; nenhuma conexão do jogo ou internet.',
        note='Um resultado PASS confirma somente os bytes sintéticos. A API não expõe contadores de descarte na fila do driver.')
    if check:result['status']='READY' if capability.get('ready') else 'UNAVAILABLE'
    elif capability.get('ready'):
        for family,case,size in CASES:
            item=_run_case(family,case,size,backend_factory=NativeCapture,socket_factory=socket.socket,
                           clock=time.monotonic,timeout=12)
            result['cases'].append(item);result['driver_loaded']|=item.get('capture_started',False)
            if on_case:on_case(item)
            if item.get('close_incomplete') or item.get('cleanup_errors'):break
        result['status']='PASS' if len(result['cases'])==len(CASES) and all(c['status']=='PASS' for c in result['cases']) else 'FAIL'
    else:result['detail']=capability.get('error') or 'Execute como administrador para testar o driver incluído. Nenhuma captura foi aberta.'
    destination=Path(output) if output else Path(os.environ.get('LOCALAPPDATA') or Path.home())/'ImperioAION/diagnosticos-captura'
    if destination.suffix.lower()!='.json':destination=destination/('captura-integrada-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    destination=destination.resolve();destination.parent.mkdir(parents=True,exist_ok=True)
    result['report_path']=str(destination);result['finished_utc']=datetime.now(timezone.utc).isoformat()
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description='Império AION · verificação da captura incluída')
    parser.add_argument('--check',action='store_true',help='Somente arquivos/API/permissões; não carrega o driver.')
    parser.add_argument('--output');args=parser.parse_args(argv)
    print('Império AION · teste da captura incluída',flush=True)
    print('O jogo e o ExitLag podem permanecer fechados. Nenhum Npcap é necessário.',flush=True)
    print('O teste de leitura exige administrador e carrega o driver WinDivert incluído.',flush=True)
    result=run(args.check,args.output,lambda c:print(c['case']+': '+c['status']+' · '+str(c['captured_bytes'])+'/'+str(c['expected_bytes'])+' bytes',flush=True))
    print(result['status']+' · '+result.get('detail','Verifique o resultado de cada caso.'),flush=True)
    print('Relatório: '+result['report_path'],flush=True)
    return 0 if result['status'] in ('PASS','READY') else 3


if __name__=='__main__':raise SystemExit(main())
