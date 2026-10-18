# SPDX-License-Identifier: GPL-3.0-only
"""Exercise real widgets with isolated state, no game, capture or web server."""
import copy, os, tempfile, tkinter as tk, unittest
from pathlib import Path
from unittest.mock import patch
from app import App
from collector import Controller
from combat_charts import row_key
from panel_widgets import RoundedTable
from streaming_state import projection


class _NoGame:
    def viewport(self): return None
    def own_foreground(self): return False


class DisplayIdentityUITests(unittest.TestCase):
    def setUp(self):
        folder=Path(__file__).resolve().parents[2]/'work';folder.mkdir(exist_ok=True)
        temp=tempfile.TemporaryDirectory(dir=folder);self.addCleanup(temp.cleanup)
        def forbidden(*args,**kwargs): raise AssertionError('No capture in label tests')
        original=tk.Toplevel
        class HiddenTop(original):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs);self.withdraw()
            def deiconify(self): pass
        for p in (patch.dict(os.environ,LOCALAPPDATA=temp.name),
                  patch('app.PanelHotkey',return_value=None),patch('app.game_processes',return_value=set()),
                  patch.object(Controller,'start',side_effect=forbidden),patch.object(Controller,'_spawn',side_effect=forbidden),
                  patch.object(App,'follow_game',return_value=None),patch('tkinter.Toplevel',HiddenTop)):
            p.start();self.addCleanup(p.stop)
        self.root=tk.Tk();self.root.withdraw();self.root.state=lambda:'normal'
        self.app=App(self.root,controller=Controller(),preview=True,game_window=_NoGame())
        self.addCleanup(self.app.close);self.root.update()
        self.app.hud_scope.set('all');self.app.show_page('summary')
        self.app.controller.reader.bindings={'one':dict(actor=101,character='Zepidem',server=2405)}
        self.rows=[dict(name='Você · nome pendente',actor=101,dbid=9001,class_id=36,
                       damage=120,dps_average=60,dps_current=60,duration_us=2_000_000,
                       hp_percent=None,damage_share_percent=60,skills=[dict(code=18010140,name='One',damage=120,records=1)]),
                   dict(name='Outra pessoa',actor=202,dbid=9002,class_id=27,
                       damage=80,dps_average=40,dps_current=40,duration_us=2_000_000,
                       hp_percent=None,damage_share_percent=40,skills=[dict(code=14010140,name='Two',damage=80,records=1)])]
        self.fight=dict(key='grupo/one/1/77/1',flow='one',target=77,revision=1,attempt=1,
                        start_us=0,end_us=None,end_reason=None,duration_us=2_000_000,partial=False,
                        damage=200,dps_average=100,dps_current=100,records=2,members=self.rows,max_hp=None,skills=[])
        self.state=self.app.controller.snapshot()
        self.state.update(mode='sample',identified=True,named_identity_confirmed=False,character='Zepidem',
                          clock_us=2_000_000,group_encounters=[self.fight],group_active_key=self.fight['key'],encounters=[],group_rosters=[],
                          local_combat_actors=[dict(flow='one',actor=101,first_us=0)],warnings=[],error=None,stale=False)
        self.app.controller.snapshot=lambda:copy.deepcopy(self.state)

    def refresh(self):
        self.app.tick();self.root.update_idletasks()

    def pending_peers(self):
        self.rows[0]['identity_pending']=True
        self.rows[1].update(name='Integrante 2 · nome pendente',identity_pending=True,
                            dbid=None,observation_key='actor/202')
        third=copy.deepcopy(self.rows[1])
        third.update(name='Integrante 3 · nome pendente',actor=303,
                     observation_key='actor/303',class_id=36,damage=50,
                     dps_average=25,dps_current=25,
                     skills=[dict(code=18010141,name='Three',damage=50,records=1)])
        self.rows.append(third)
        self.fight.update(damage=250,dps_average=125,dps_current=125,records=3)
        for row in self.rows:row['damage_share_percent']=row['damage']/2.5

    def test_own_label_stays_stable_when_name_arrives_and_selection_uses_real_identity(self):
        self.refresh()
        a=self.app
        self.assertEqual(a.group_table.item('actor/101','values')[0],'Você')
        self.assertEqual(a.hud_view.table.item('actor/101','values')[0],'Você')
        self.assertEqual(a.panel.character.get(),'Você')
        self.assertEqual(a.panel.character_label.get(),'Você')
        self.assertEqual(a.panel.skill_member_label.get(),'Você')
        self.assertEqual(a.skill_total,120)
        self.rows[0]['name']='Zepidem';self.state['named_identity_confirmed']=True
        self.refresh()
        self.assertEqual(a.group_table.item('actor/101','values')[0],'Você')
        self.assertEqual(a.hud_view.table.item('actor/101','values')[0],'Você')
        self.assertEqual(a.character.get(),'Zepidem')
        a.group_table.selection_set('actor/202');a.panel.select_player()
        self.assertEqual(a.skill_member.get(),'Outra pessoa')
        self.assertEqual(a.panel.skill_member_label.get(),'Outra pessoa')
        self.assertEqual(a.skill_total,80)
        a.panel.player_curve_selected('actor/101')
        self.assertEqual(a.skill_member.get(),'Zepidem')
        self.assertEqual(a.panel.skill_member_label.get(),'Você')
        self.assertEqual(a.skill_total,120)
        self.assertEqual(self.rows[0]['damage'],120)

    def test_overlay_projection_uses_alias_without_changing_selection_or_privacy(self):
        self.rows[0]['name']='Zepidem';self.refresh();a=self.app;a.login_ready=True
        rows=list(a.group_member_rows.values());original=copy.deepcopy(rows)
        view,_=projection(a,self.state,self.fight,rows,names=True)
        self.assertEqual([r['name'] for r in view['painel']['rows']],['Você','Outra pessoa'])
        self.assertEqual(view['painel']['selectedName'],'Você')
        self.assertEqual(view['hud']['character'],'Você')
        private,_=projection(a,self.state,self.fight,rows,names=False)
        self.assertEqual([r['name'] for r in private['painel']['rows']],['Jogador 1','Jogador 2'])
        self.assertEqual(private['hud']['character'],'')
        self.assertEqual(rows,original)
        self.assertEqual(a.skill_member.get(),'Zepidem')

    def test_pending_peers_keep_separate_table_rows_and_accumulated_damage(self):
        self.pending_peers();original=copy.deepcopy((self.rows,self.fight))
        self.refresh();a=self.app

        self.assertEqual(set(a.group_table.get_children()),{'actor/101','actor/202','actor/303'})
        self.assertEqual(set(a.hud_view.table.values),{'actor/101','actor/202','actor/303'})
        for key,label in (('actor/101','Você'),('actor/202','Sincronizando'),
                          ('actor/303','Sincronizando')):
            with self.subTest(key=key):
                self.assertEqual(a.group_table.item(key,'values')[0],label)
                self.assertEqual(a.hud_view.table.item(key,'values')[0],label)
        self.assertEqual([a.group_member_rows[key]['damage'] for key in
                          ('actor/101','actor/202','actor/303')],[120,80,50])
        self.assertEqual(sum(row['damage'] for row in a.group_member_rows.values()),250)
        self.assertEqual((self.rows,self.fight),original)

    def test_pending_indicators_clear_individually_when_each_name_arrives(self):
        self.pending_peers();self.refresh();a=self.app
        table=a.hud_view.table
        self.assertEqual(len(table.find_withtag('name_loading')),2)
        self.assertFalse(a.group_table.item('actor/101','sync_pending'))
        self.assertTrue(a.group_table.item('actor/202','sync_pending'))
        self.assertTrue(a.group_table.item('actor/303','sync_pending'))
        original_damage={key:row['damage'] for key,row in a.group_member_rows.items()}
        original_classes={key:row.get('class_key') for key,row in a.group_member_rows.items()}

        self.rows[1].update(name='Primeiro nome recebido',identity_pending=False,dbid=9002)
        self.refresh()

        self.assertEqual(a.group_table.item('actor/202','values')[0],'Primeiro nome recebido')
        self.assertEqual(table.item('actor/202','values')[0],'Primeiro nome recebido')
        self.assertEqual(table.item('actor/303','values')[0],'Sincronizando')
        self.assertEqual(len(table.find_withtag('name_loading')),1)
        self.assertFalse(a.group_table.item('actor/202','sync_pending'))
        self.assertTrue(a.group_table.item('actor/303','sync_pending'))

        self.rows[2].update(name='Segundo nome recebido',identity_pending=False,dbid=9003)
        self.refresh()

        self.assertEqual(len(table.find_withtag('name_loading')),0)
        self.assertEqual(a.group_table.item('actor/303','values')[0],'Segundo nome recebido')
        self.assertEqual(table.item('actor/303','values')[0],'Segundo nome recebido')
        self.assertEqual({key:row['damage'] for key,row in a.group_member_rows.items()},original_damage)
        self.assertEqual({key:row.get('class_key') for key,row in a.group_member_rows.items()},original_classes)

    def test_selected_pending_peer_keeps_own_skills_when_real_name_arrives(self):
        self.pending_peers();self.refresh();a=self.app
        a.group_table.selection_set('actor/202');a.panel.select_player()
        self.assertEqual(a.skill_member.get(),'Integrante 2 · nome pendente')
        self.assertEqual(a.panel.skill_member_label.get(),'Sincronizando')
        self.assertEqual(a.skill_total,80)

        # A player click invalidates the skill table; a concurrently arriving
        # nickname must preserve the selected actor even before its next render.
        a.last_table=None
        self.rows[1].update(name='Nome recebido',identity_pending=False,dbid=9002)
        self.refresh()

        self.assertEqual(a.group_table.selection(),('actor/202',))
        self.assertEqual(a.skill_member.get(),'Nome recebido')
        self.assertEqual(a.panel.skill_member_label.get(),'Nome recebido')
        self.assertEqual(a.skill_total,80)
        self.assertEqual([row['code'] for row in a.skill_rows],[14010140])
        a.group_table.selection_set('actor/303');a.panel.select_player()
        self.assertEqual(a.panel.skill_member_label.get(),'Sincronizando')
        self.assertEqual(a.skill_total,50)

    def test_hud_tooltip_finds_received_class_using_stable_actor_key(self):
        self.pending_peers();self.refresh();hud=self.app.hud_view
        hud.interactive=True;hud.menu_open=False

        with patch.object(hud,'show_inline_hint') as hint:
            hud.show_tip('actor/202',100,100)

        self.assertEqual(hint.call_count,1)
        self.assertIn('Sincronizando',hint.call_args.args[0])
        self.assertIn('Feiticeiro',hint.call_args.args[0])


class PendingTableLifecycleTests(unittest.TestCase):
    def test_opt_in_spinner_releases_animation_on_hidden_resolved_or_destroyed_table(self):
        root=tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        table=RoundedTable(root,columns=('name','damage'))
        table.configure(width=400,height=120)
        table.insert('','end',iid='unrelated',values=('Sincronizando','12'))
        table.draw()
        self.assertEqual(len(table.find_withtag('name_loading')),0)
        self.assertIsNone(table.loading_timer)
        table.item('unrelated',sync_pending=True)
        table.draw()
        self.assertEqual(len(table.find_withtag('name_loading')),1)
        self.assertIsNone(table.loading_timer)  # Hidden tables retain a static icon.

        with patch.object(table,'winfo_ismapped',return_value=True):
            table.loading_motion=True
            table.schedule_loading()
            self.assertIsNotNone(table.loading_timer)
            table.loading_motion=False
            table.schedule_loading()
            self.assertIsNone(table.loading_timer)
            self.assertEqual(len(table.find_withtag('name_loading')),1)
            table.loading_motion=True
            table.schedule_loading()
            self.assertIsNotNone(table.loading_timer)
            with patch.object(table,'winfo_ismapped',return_value=False):
                table.schedule_loading()
            self.assertIsNone(table.loading_timer)
            table.schedule_loading()
            self.assertIsNotNone(table.loading_timer)
            table.item('unrelated',values=('Nome recebido','12'),sync_pending=False)
            table.draw()
            self.assertEqual(len(table.find_withtag('name_loading')),0)
            self.assertIsNone(table.loading_timer)
            table.item('unrelated',sync_pending=True)
            table.draw()
            timer=table.loading_timer
            self.assertIsNotNone(timer)
            table.destroy()
            with self.assertRaises(tk.TclError):root.tk.call('after','info',timer)


if __name__=='__main__': unittest.main()
