# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Desktop entry; --check builds the real panel/HUD with isolated local state.

The check never opens capture or a driver, registers hotkeys, queries a game,
or sends data. Native WinDivert probing only validates the bundled DLL ABI and
compiles exact example filters.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from datetime import datetime, timezone


class CheckGame:
    def viewport(self):return None
    def own_foreground(self):return False


def startup_case(state_root, saved=False):
    """Execute real App initialization and Tk callbacks, never start capture."""
    import tkinter as tk
    from PIL import ImageTk
    import app as app_module
    from collector import Controller
    from character_cache import CharacterCache, cache_directory
    from weekly_orders import next_order_reset
    from pet_catalog import CATALOG

    prior_data=os.environ.get('LOCALAPPDATA')
    os.environ['LOCALAPPDATA']=str(state_root)
    root=None;application=None;errors=[]
    originals=(app_module.PanelHotkey,app_module.game_processes,Controller.start,Controller._spawn)
    def forbidden(*args,**kwargs):raise RuntimeError('A verificação não pode iniciar captura/processos.')
    app_module.PanelHotkey=lambda *args:None
    app_module.game_processes=lambda:set()
    Controller.start=forbidden;Controller._spawn=forbidden
    try:
        if saved:
            prefs=cache_directory().parent/'preferencias-hud.json';prefs.parent.mkdir(parents=True,exist_ok=True)
            prefs.write_text(json.dumps(dict(character='UsuarioTeste',size=[450,230],opacity=70,
                capture_device='native',capture_label='Nativa · Windows · em validação',shortcuts=dict(__import__('shortcuts').DEFAULTS,hud_adjustment='Ctrl+Alt+S'))),encoding='utf-8')
            cache=CharacterCache()
            cache.remember(dict(name='UsuarioTeste',server=2405),{},activity_entries={
                'shugo_party':dict(current=17,bonus=0,limit=21,available=True),
                'ascension_rite':dict(current=2,bonus=0,limit=3,available=True,reset_at=next_order_reset().timestamp())})
            from order_accumulator import reconcile
            import time
            observed=time.time()-20
            captured_identity=dict(name='UsuarioTeste',server=2405,actor=1)
            base_read=dict(available=True,current=20,server_stamp_ms=int((observed-1)*1000),
                observed_at=observed,source='8356-initial-counter',flow='check',identity=captured_identity)
            late_read=dict(available=True,current=0,limit=5,observed_at=observed,flow='check',
                reset_at=next_order_reset().timestamp(),source='8356-initial-counter')
            _,anchor=reconcile(base_read,None,late_read,captured_identity)
            later=dict(base_read,current=21,observed_at=observed+10,
                server_stamp_ms=int((observed+9)*1000),source='8456-counter-and-0f8d-receipt')
            _,tracked=reconcile(later,anchor,late_read,captured_identity)
            cache.remember(captured_identity,{'1162':dict(level=1,souls=None),'1171':dict(level=1,souls=9),
                '1204':dict(level=3,souls=None)},recent=['1162','1171','1204'],order_accumulator=tracked)
            cache.flush(force=True)
        root=tk.Tk();root.withdraw()
        # Keep this background verification from moving focus or showing windows.
        root.deiconify=lambda:None;root.lift=lambda:None;root.focus_force=lambda:None
        root.report_callback_exception=lambda *error:errors.append(''.join(traceback.format_exception(*error)))
        application=app_module.App(root,controller=Controller(),preview=False,game_window=CheckGame())
        root.update()  # Includes after_idle/open_overlay, where the second missing file was used.
        if errors:raise RuntimeError('Erro de callback na abertura: '+errors[0])
        if application.hud_view is None or not application.overlay.winfo_exists():
            raise RuntimeError('A HUD não foi construída na inicialização.')
        if application.hud_view.welcome_original.width<1:
            raise RuntimeError('A imagem de boas-vindas não foi carregada.')
        for key in ('capture_step_1','capture_step_2','capture_step_3','capture_step_check','capture_step_later'):
            if application.hud_view.assets.get(key,28) is None:
                raise RuntimeError('Ícone de preparação ausente: '+key)
        if len(application.hud_view.welcome_card_boxes)!=3:
            raise RuntimeError('As três caixas de preparação não foram criadas.')
        root.after_cancel(application.tick_after)
        application.tick();root.update_idletasks()
        for page in ('combat','pets','duty'):
            application.hud_page.set(page);application.hud_view.render();root.update_idletasks()
        if errors:raise RuntimeError('Erro de callback ao renderizar a HUD: '+errors[0])
        if CATALOG.errors or len(CATALOG.entries)!=207 or not application.catalog.npcs:
            raise RuntimeError('Um catálogo obrigatório está ausente ou incompleto.')
        if application.controller.running or application.controller.owned or application.controller.owned_native:
            raise RuntimeError('A verificação iniciou uma coleta indevida.')
        if application.capture_device!='windivert' or application.character.get()!=('UsuarioTeste' if saved else ''):
            raise RuntimeError('A inicialização não preservou o personagem/modo esperado.')
        if saved:
            snapshot=application.controller.snapshot()
            entries=snapshot.get('activity_entries',{})
            if entries.get('shugo_party',{}).get('current')!=17 or entries.get('ascension_rite',{}).get('current')!=2:
                raise RuntimeError('O histórico de entradas não foi recuperado na abertura.')
            altgard=snapshot.get('weekly_orders',{}).get('altgard',{})
            if altgard.get('current')!=4 or altgard.get('partial') or not altgard.get('cached'):
                raise RuntimeError('A regra semanal de Altgard não foi recuperada.')
            from hud_view import pet_progress
            pets=snapshot.get('pet_collection',{})
            incomplete=next(row for row in pets.get('entries',[]) if row['pet_id']=='1162')
            _,note,complete=pet_progress(incomplete,pets)
            if complete or 'Último saldo salvo' in note:
                raise RuntimeError('Um nível salvo foi apresentado como saldo completo.')
            maximum=next(row for row in pets.get('entries',[]) if row['pet_id']=='1204')
            caption,note,complete=pet_progress(maximum,pets)
            if caption!='Nv. 3 · Máximo' or not complete or note!='Último nível salvo':
                raise RuntimeError('O nível máximo salvo foi apresentado como saldo pendente.')
        from skill_catalog import SkillCatalog
        skill_catalog=SkillCatalog()
        if (len(skill_catalog.entries)!=284 or skill_catalog.photo(root,13050340) is None
                or skill_catalog.photo(root,30000241) is None):
            raise RuntimeError('Catálogo de habilidades ou ícones não empacotado.')
        if application.shortcut('hud_adjustment')!=('Ctrl+Alt+S' if saved else 'Ctrl+Alt+M'):raise RuntimeError('Atalho personalizado não recuperado.')
        if application.hud_view.return_button.text!='Bloquear HUD':raise RuntimeError('Texto de bloqueio incorreto.')
        application.show_page('shortcuts');root.update_idletasks()
        required={'summary','capture','diagnostic','streaming','privacy','terms','shortcuts'}
        if not required.issubset(application.panel.page_views):
            raise RuntimeError('As páginas obrigatórias não foram construídas.')
        if application.pages['skills'] is not application.pages['summary']:
            raise RuntimeError('O histórico e as habilidades não compartilham a página combinada.')
        if application.nav_buttons['shortcuts'].text!='Atalhos':
            raise RuntimeError('O botão Atalhos está ausente ou com texto incorreto.')
        # Verify native pre-login combat access in the exact frozen build.
        state=application.controller.snapshot()
        state.update(mode='live',running=True,error=None,capture_generation=861,
                     capture_ready=False,metadata=dict(capture_source_ready=False),login_session=dict(confirmed=False))
        application.show_combat_hud();application.hud_view.update(state,None,[])
        assert not application.login_ready and not application.hud_view.welcome_shown
        assert 'Preparando captura' in application.hud_view.empty.cget('text')
        assert application.hud_view.empty.cget('image')
        from streaming_state import projection
        views,_=projection(application,state,None,[])
        assert views['hud']['ready'] and views['painel']['ready']
        assert not views['hud']['character'] and not views['hud']['rows']
        application.hud_page.set('pets');application.hud_view.render()
        assert application.hud_view.welcome_shown
        # The shipped GUI must keep number 2 until the capture source opens.
        state['capture_ready']=False
        application.hud_view.update(state,None,[])
        assert application.hud_view.welcome_state=='preparing'
        assert application.hud_view.welcome_step_keys[1]=='capture_step_2'
        assert not application.hud_view.capture.running
        state['capture_ready']=True
        state['metadata']['capture_source_ready']=True
        application.hud_view.update(state,None,[])
        assert application.hud_view.welcome_step_keys[1]=='capture_step_2'
        assert application.hud_view.capture.running and not application.login_ready
        hud=application.hud_view
        def card_fill(index):
            image=ImageTk.getimage(hud.welcome_card_photos[index]).convert('RGB')
            return '#%02X%02X%02X'%image.getpixel((image.width//2,image.height//2))
        assert card_fill(0)=='#493421'
        assert card_fill(1)=='#30251D'
        assert card_fill(2)=='#30251D'
        state['character_selection']=dict(confirmed=True,visited=True)
        hud.update(state,None,[])
        assert hud.welcome_step_keys==['capture_step_check','capture_step_check','capture_step_3']
        assert card_fill(0)=='#24432F'
        assert 'Relogue o personagem' in [hud.welcome.itemcget(i,'text') for i in hud.welcome.find_withtag('welcome_step_title')]
        assert not application.login_ready
        assert application.hud_view.welcome_dps.winfo_manager()=='place'
        application.hud_view.welcome_dps.invoke()
        assert application.hud_page.get()=='combat' and not application.hud_view.welcome_shown
        application.onboarding_skipped=False
        state['character_selection']=dict(visited=True,confirmed=False)
        state['login_session']=dict(confirmed=True,entry_confirmed=True)
        hud.update(state,None,[])
        assert hud.welcome_shown and hud.welcome_step_keys==['capture_step_check']*3
        fresh=lambda:dict(available=True,cached=False)
        state.update(od_energy=fresh(),resource_gains=dict(kinah_daily=fresh(),abyss_weekly=fresh()),daily_duty=fresh(),
            weekly_orders=dict(altgard=fresh(),latesran=fresh()),
            activity_entries={key:fresh() for key in ('daily_dungeon','shugo_party','nightmare','ascension_rite')},
            pet_collection=dict(wire_decoder_validated=True,character_scope=dict(name='Example',server=2405)))
        hud.update(state,None,[])
        assert application.onboarding_complete and not hud.welcome_shown
        state['capture_generation']+=1;state['login_session']={}
        hud.update(state,None,[])
        # Viewing history does not mark a login or start an actual capture.
        assert not application.login_ready
        for page in ('summary','streaming','privacy','terms','diagnostic','capture','shortcuts'):
            application.show_page(page);root.update_idletasks()
        if application.streaming.httpd is not None:raise RuntimeError('Streaming iniciou sem ativação do usuário.')
        from native_identity import ICON,APP_ID
        if not ICON.is_file() or APP_ID!='Imperio.AION.Monitor':
            raise RuntimeError('A identidade visual do Windows está incompleta.')
        if errors:raise RuntimeError(str(errors))
        return dict(case='saved-preferences-and-cache' if saved else 'fresh-install',
            app_imported=True,panel_created=True,hud_created=True,welcome_image_loaded=True,
            pages_rendered=['combat','pets','duty'],callback_errors=[],capture_started=False,
            global_hotkeys_registered=False,game_queried=False,pet_catalog_count=len(CATALOG.entries),
            npc_catalog_count=len(application.catalog.npcs),skill_catalog_count=284,
            panel_pages_checked=sorted(required),combined_combat_history=True,prelogin_combat_access=True,preparation_cards=3,original_step_pngs=5,capture_source_readiness_ui=True,selection_step_highlight_and_check=True,
            capture_backend='windivert',old_capture_preference_migrated=saved,
            window_icon_loaded=True,status='PASS')
    finally:
        if application is not None:
            application.close()
        elif root is not None:
            root.destroy()
        app_module.PanelHotkey,app_module.game_processes,Controller.start,Controller._spawn=originals
        if prior_data is None:os.environ.pop('LOCALAPPDATA',None)
        else:os.environ['LOCALAPPDATA']=prior_data


def runtime_check():
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true');parser.add_argument('--output',required=True)
    parser.add_argument('--expected-user-sid',help=argparse.SUPPRESS)
    args=parser.parse_args()
    import tkinter as tk
    import PIL
    from PIL import Image
    from native_windivert import probe
    from hud_assets import ASSET_FILES, ROOT, RasterAssets
    # Import the whole application, including its eager protocol profile.
    import app
    from live_core import PROFILE
    target=Path(args.output).resolve();target.parent.mkdir(parents=True,exist_ok=True)
    report=dict(product='Império AION',version='0.8.71',package_revision=2,kind='packaged-app-startup-check',
        python=sys.version.split()[0],pillow=PIL.__version__,capture_started=False,network_opened=False,
        external_python_required=False,tshark_required=False,npcap_required=False,exitlag_required=False,
        integrated_capture=probe(),assets_checked=0,
        profile_loaded=bool(PROFILE.get('skill_names')),demo_included=False)
    if (not report['integrated_capture'].get('available')
            or report['integrated_capture'].get('driver_started')
            or not report['integrated_capture'].get('filter_compiler_validated')):
        raise RuntimeError('A captura incluída não passou na verificação sem driver.')
    root=tk.Tk();root.withdraw()
    try:
        report['tk']=root.tk.call('info','patchlevel');assets=RasterAssets(root)
        for key,path in ASSET_FILES.items():
            with Image.open(ROOT/path) as image:image.verify()
            report['assets_checked']+=1
        with Image.open(ROOT/'assets/kit/mascote-pensativa.png') as image:image.verify()
        report['assets_checked']+=1
        for folder in ('assets/panel','assets/skills'):
            for path in (ROOT/folder).rglob('*'):
                if path.suffix.lower() in ('.png','.webp','.ico'):
                    with Image.open(path) as image:image.verify()
                    report['assets_checked']+=1
        for key in ('mascot','filter_pets','filter_duty','kinah_daily','abyss_weekly','action_reload'):
            if assets.get(key,24) is None:raise RuntimeError('Imagem da HUD indisponível: '+key)
        if assets.errors:raise RuntimeError('Falha na carga de imagens: '+str(assets.errors))
    finally:root.destroy()
    legal=json.loads((ROOT/'dados/legal-pt.json').read_text(encoding='utf-8'))
    if legal.get('responsible')!='Zepidem' or legal.get('contact')!='support@imperiobdo.com':
        raise RuntimeError('O responsável ou contato das páginas legais está incorreto.')
    for key in ('privacy','terms'):
        if not legal.get(key,{}).get('sections'):
            raise RuntimeError('Página legal vazia: '+key)
    report['legal_pages_loaded']=True
    with tempfile.TemporaryDirectory(prefix='startup-check-',dir=target.parent) as temporary:
        report['startup_cases']=[startup_case(Path(temporary)/name,saved)
            for name,saved in (('fresh',False),('saved',True))]
    report['status']='PASS'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    from startup_identity import StartupIdentityError, enforce_expected_user_sid
    try:
        enforce_expected_user_sid()
    except StartupIdentityError as identity_error:
        # No app imports, profile lookup or log writes under another account.
        if '--check' in sys.argv and '--output' in sys.argv:
            target=Path(sys.argv[sys.argv.index('--output')+1]).resolve()
            target.write_text(json.dumps(dict(status='IDENTITY_REFUSED',version='0.8.71',error=str(identity_error),capture_started=False)),encoding='utf8')
        else:
            import ctypes
            box=ctypes.windll.user32.MessageBoxW
            box.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint]
            box(None,str(identity_error),'Império AION',0x10)
        raise SystemExit(2)
    try:
        from installation_guard import enforce_elevated_installation
        enforce_elevated_installation()
        if '--check' in sys.argv:runtime_check()
        else:
            if '--demo' in sys.argv:
                raise ValueError('A demonstração está disponível somente no pacote fonte.')
            from app import main
            main()
    except Exception:
        failure=traceback.format_exc()
        if '--check' in sys.argv:
            # An explicit output path is the only write outside temporary state.
            try:
                output=Path(sys.argv[sys.argv.index('--output')+1]).resolve()
                output.parent.mkdir(parents=True,exist_ok=True)
                output.write_text(json.dumps(dict(version='0.8.71',status='FAIL',
                    capture_started=False,error=failure),ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception:pass
            raise SystemExit(1)
        from app_paths import data_directory
        folder=data_directory()/'logs';folder.mkdir(parents=True,exist_ok=True)
        path=folder/('erro-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')+'.log')
        path.write_text(failure,encoding='utf-8')
        if '--check' not in sys.argv:
            from tkinter import messagebox
            messagebox.showerror('Império AION','Não foi possível abrir o aplicativo.\nDiagnóstico: '+str(path))
        raise SystemExit(1)
