# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic names need explicit relationship fields AND independent scope."""
import unittest
from combat_names import effect_player,CombatNames
from group_core import GroupBook


def v(n):
    result=bytearray()
    while n>127:result.append((n&127)|128);n>>=7
    result.append(n);return bytes(result)


def entity_state_record(actor=8257,zero=0,flag=7):
    return b'\x42\x36'+v(actor)+v(zero)+v(flag)


def non_removal_records():
    removal=entity_state_record()
    return ([('death',entity_state_record(flag=3),'one'),
             ('other-actor',entity_state_record(actor=202),'one'),
             ('other-flow',removal,'unowned'),
             ('nonzero-field',entity_state_record(zero=1),'one'),
             ('unknown-flag',entity_state_record(flag=9),'one'),
             ('unknown-wide-flag',entity_state_record(flag=128),'one'),
             ('trailing-zero',removal+b'\0','one'),
             ('trailing-byte',removal+b'\x01','one')]+
            [(f'truncated-{end}',removal[:end],'one') for end in range(len(removal))])


def unbound_member_record(name='Example',server=2405):
    """A complete 058a member entry contains no proven current-self relation."""
    text=name.encode('utf8');uid=(server<<48)|12345
    return (b'\x05\x8a\0\0\x01\0'+(777).to_bytes(8,'little')+uid.to_bytes(8,'little')+
            (20).to_bytes(4,'little')+bytes([len(text)])+text+(45).to_bytes(4,'little')+
            b'\0\x01'+(1_800_000_000_000).to_bytes(8,'little')+bytes(10)+b'\x01\0\0\0\0\0')


def caster(actor=8257,name='Example',model=2920648):
    text=name.encode();profile=bytearray(148 if model==2920648 else 149)
    profile[:6]=model.to_bytes(4,'little')+bytes((64 if model==2920648 else 0,2))
    offset=126 if model==2920648 else 127
    profile[offset-3:offset]=b'\x07\x02\x06'
    profile[offset:offset+4]=actor.to_bytes(4,'little')
    return b'\x41\x36'+v(35000)+b'\x1f\0\x01'+bytes([len(text)])+text+profile


def target(actor=8257,name='Example',server=2405,guild='Guild'):
    text=name.encode();profile=bytearray(159)
    profile[:6]=bytes.fromhex('1a902c000002');profile[-1]=6
    if server is None:tail=actor.to_bytes(4,'little')+b'\0\x2d\0\0\0\0';mask=0x14
    else:
        g=guild.encode();tail=(actor.to_bytes(4,'little')+(123).to_bytes(4,'little')+bytes(2)+
            server.to_bytes(2,'little')+bytes([len(g)])+g+b'\x02\0\x01'+bytes(16)+b'\x2d\0\0\0\0');mask=0x54
    return b'\x41\x36'+v(35000)+bytes((mask,0,1,len(text)))+text+profile+tail


def summon(actor=8257,name='Example',server=2405,model=2920647):
    text=name.encode();offset=126 if model==2920647 else 125
    profile=bytearray(offset);profile[:6]=model.to_bytes(4,'little')+bytes((64 if model==2920647 else 0,2))
    profile[-3:]=b'\x07\x02\x06';guild=b'Guild'
    block=actor.to_bytes(4,'little')+(123).to_bytes(4,'little')+bytes(2)+server.to_bytes(2,'little')+bytes([len(guild)])+guild
    footer=(bytes.fromhex('01000200000000000000000000000000000003cd0094070000d0004d010000d600320000002d00000000')
            if model==2920647 else bytes.fromhex('010002000000000000000000000000000000000a00000000'))
    return b'\x41\x36'+v(35000)+b'\x5f\0\x01'+bytes([len(text)])+text+profile+block+footer


class CombatNameTests(unittest.TestCase):
    def test_actor_removal_requires_a_new_explicit_name_for_late_self_promotion(self):
        for fresh_name_when in ('before-self','after-self'):
            with self.subTest(fresh_name_when=fresh_name_when):
                book=GroupBook();names=CombatNames();ctx=dict(flow='one',clock_us=300_000,packet=3)
                book.confirm_party_actor(dict(ctx,clock_us=200_000,packet=2),
                    dict(actor=8257,epoch=0,first_us=100_000,confirmed_us=200_000,
                         basis='owned-party-vitals-sa-1b92'))
                names.observe(ctx,caster(),'one',book)
                self.assertFalse(names.apply(book,'one',300_000)[0]['self_identity'])
                names.observe(dict(ctx,clock_us=1_000_000,packet=10),entity_state_record(),'one',book)
                self.assertEqual(names.apply(book,'one',1_000_000),[])
                if fresh_name_when=='before-self':
                    names.observe(dict(ctx,clock_us=1_100_000,packet=11),caster(),'one',book)
                    refreshed=names.apply(book,'one',1_100_000)
                    self.assertEqual(len(refreshed),1);self.assertFalse(refreshed[0]['self_identity'])
                book.confirm_local_actor('one',dict(actor=8257,first_us=31_000_000,
                    basis='owned-self-stats-4a36'))
                accepted=names.apply(book,'one',31_300_000)
                if fresh_name_when=='after-self':
                    self.assertEqual(accepted,[])
                    self.assertEqual([event['self_identity'] for event in names.events],[False])
                    names.observe(dict(ctx,clock_us=31_400_000,packet=314),caster(),'one',book)
                    accepted=names.apply(book,'one',31_400_000)
                self.assertEqual(len(accepted),1);self.assertTrue(accepted[0]['self_identity'])
                self.assertEqual(accepted[0]['packet'],11 if fresh_name_when=='before-self' else 314)
                self.assertEqual(names.apply(book,'one',31_500_000),[])
                self.assertEqual(names.diagnostic()['explicit_actor_removals_seen'],1)

    def test_non_removal_records_preserve_pending_and_validated_explicit_names(self):
        for validated in (False,True):
            for case,body,flow in non_removal_records():
                with self.subTest(validated=validated,case=case):
                    book=GroupBook();names=CombatNames();ctx=dict(flow='one',clock_us=300_000,packet=3)
                    if validated:
                        book.confirm_party_actor(dict(ctx,clock_us=200_000,packet=2),
                            dict(actor=8257,epoch=0,first_us=100_000,confirmed_us=200_000,
                                 basis='owned-party-vitals-sa-1b92'))
                    names.observe(ctx,caster(),'one',book)
                    initial=names.apply(book,'one',300_000)
                    self.assertEqual([event['self_identity'] for event in initial],[False] if validated else [])
                    names.observe(dict(flow=flow,clock_us=1_000_000,packet=10),body,'one',book)
                    first_self_us=31_000_000 if validated else 1_100_000
                    book.confirm_local_actor('one',dict(actor=8257,first_us=first_self_us,
                        basis='owned-self-stats-4a36'))
                    own=names.apply(book,'one',first_self_us+300_000)
                    self.assertEqual(len(own),1);self.assertTrue(own[0]['self_identity'])
                    self.assertEqual((own[0]['name'],own[0]['packet']),('Example',3))

    def test_actor_removal_discards_an_unapplied_name_before_own_proof(self):
        book=GroupBook();names=CombatNames();ctx=dict(flow='one',clock_us=300_000,packet=3)
        names.observe(ctx,caster(),'one',book)
        self.assertEqual(names.apply(book,'one',300_000),[])
        names.observe(dict(ctx,clock_us=1_000_000,packet=10),entity_state_record(),'one',book)
        book.confirm_local_actor('one',dict(actor=8257,first_us=1_100_000,
            basis='owned-self-stats-4a36'))
        self.assertEqual(names.apply(book,'one',1_300_000),[])
        self.assertEqual(book.names,{});self.assertEqual(names.events,[])
        names.observe(dict(ctx,clock_us=1_400_000,packet=14),caster(),'one',book)
        own=names.apply(book,'one',1_400_000)
        self.assertEqual(len(own),1);self.assertTrue(own[0]['self_identity'])
        self.assertEqual(own[0]['packet'],14)

    def test_unbound_member_record_never_names_self_party_or_nearby_actors(self):
        for proof_first in (False,True):
            with self.subTest(proof_first=proof_first):
                book=GroupBook();names=CombatNames();ctx=dict(flow='one',clock_us=2_000_000,packet=20)
                book.confirm_party_actor(ctx,dict(actor=202,epoch=0,first_us=1_000_000,
                    confirmed_us=2_000_000,basis='owned-party-vitals-sa-1b92'))
                proof=dict(actor=8257,first_us=1_000_000,basis='owned-self-stats-4a36')
                if proof_first:book.confirm_local_actor('one',proof)
                names.observe(ctx,unbound_member_record(name='Müller'),'one',book)
                self.assertEqual(names.apply(book,'one',2_000_000),[])
                if not proof_first:book.confirm_local_actor('one',proof)
                self.assertEqual(names.apply(book,'one',3_000_000),[])
                self.assertEqual(book.names,{});self.assertEqual(names.events,[])

    def test_unbound_member_record_never_replaces_an_explicit_self_name_or_realm(self):
        book=GroupBook();names=CombatNames();ctx=dict(flow='one',clock_us=2_000_000,packet=20)
        book.confirm_local_actor('one',dict(actor=8257,first_us=1_000_000,
            basis='owned-self-stats-4a36'))
        names.observe(ctx,target(name='Müller'),'one',book)
        accepted=names.apply(book,'one',2_000_000)
        self.assertEqual(len(accepted),1);self.assertTrue(accepted[0]['self_identity'])
        for name,server in (('Unrelated',2405),('Müller',1401)):
            with self.subTest(name=name,server=server):
                names.observe(dict(ctx,clock_us=3_000_000,packet=30),
                    unbound_member_record(name=name,server=server),'one',book)
                self.assertEqual(names.apply(book,'one',3_000_000),[])
                self.assertEqual(book.names,{('one',8257):{('Müller',2405)}})
                self.assertEqual(names.events,accepted)

    def test_party_name_can_be_promoted_to_self_without_repeating_the_name_packet(self):
        for first_self_us in (1_000_000,10_000_000,31_000_000):
            with self.subTest(first_self_us=first_self_us):
                book=GroupBook();names=CombatNames();ctx=dict(flow='one',clock_us=300_000,packet=3)
                book.confirm_party_actor(dict(ctx,clock_us=200_000,packet=2),
                    dict(actor=8257,epoch=0,first_us=100_000,confirmed_us=200_000,
                         basis='owned-party-vitals-sa-1b92'))
                names.observe(ctx,caster(),'one',book)
                party=names.apply(book,'one',300_000)
                self.assertEqual(len(party),1);self.assertFalse(party[0]['self_identity'])
                self.assertEqual(names.apply(book,'one',first_self_us-100_000),[])
                book.confirm_local_actor('one',dict(actor=8257,first_us=first_self_us,
                    basis='owned-self-stats-4a36'))
                own=names.apply(book,'one',first_self_us+300_000)
                self.assertEqual(len(own),1);self.assertTrue(own[0]['self_identity'])
                self.assertEqual((own[0]['actor'],own[0]['name'],own[0]['packet'],own[0]['source_clock_us']),
                                 (party[0]['actor'],party[0]['name'],3,300_000))
                self.assertEqual(book.names,{('one',8257):{('Example',None)}})
                self.assertEqual(names.apply(book,'one',first_self_us+400_000),[])
                self.assertEqual([event['self_identity'] for event in names.events],[False,True])

    def test_never_applied_names_still_require_recent_independent_proof(self):
        for case,first_self_us,now in (('preproof',10_000_000,10_300_000),
                                       ('expired',1_000_000,31_000_000)):
            with self.subTest(case=case):
                book=GroupBook();names=CombatNames();ctx=dict(flow='one',clock_us=300_000,packet=3)
                names.observe(ctx,caster(),'one',book)
                self.assertEqual(names.apply(book,'one',300_000),[])
                book.confirm_local_actor('one',dict(actor=8257,first_us=first_self_us,
                    basis='owned-self-stats-4a36'))
                self.assertEqual(names.apply(book,'one',now),[])
                self.assertEqual(names.apply(book,'one',32_000_000),[])
                self.assertEqual(book.names,{});self.assertEqual(names.events,[])

    def test_validated_party_names_cannot_promote_across_epoch_or_flow_changes(self):
        for boundary in ('epoch','flow'):
            with self.subTest(boundary=boundary):
                book=GroupBook();names=CombatNames();ctx=dict(flow='one',clock_us=300_000,packet=3)
                book.confirm_party_actor(dict(ctx,clock_us=200_000,packet=2),
                    dict(actor=8257,epoch=0,first_us=100_000,confirmed_us=200_000,
                         basis='owned-party-vitals-sa-1b92'))
                names.observe(ctx,caster(),'one',book)
                self.assertFalse(names.apply(book,'one',300_000)[0]['self_identity'])
                self.assertEqual(names.apply(book,'one',30_900_000),[])
                flow='two' if boundary=='flow' else 'one'
                if boundary=='epoch':book.roster_epochs['one']=1
                book.confirm_local_actor(flow,dict(actor=8257,first_us=31_000_000,
                    basis='owned-self-stats-4a36'))
                self.assertEqual(names.apply(book,flow,31_300_000),[])
                names.observe(dict(flow=flow,clock_us=31_300_000,packet=313),
                    b'\x00\x36'+bytes(8),flow,book)
                self.assertEqual(names.apply(book,flow,31_300_000),[])
                self.assertEqual([event['self_identity'] for event in names.events],[False])
                if boundary=='flow':self.assertNotIn(('two',8257),book.names)

    def test_explicit_own_world_header_names_combat_without_a_realm_or_login(self):
        book=GroupBook();names=CombatNames();ctx=dict(flow='one',clock_us=2_000_000,packet=20)
        book.confirm_local_actor('one',dict(actor=8257,first_us=1_000_000))
        def header(actor):
            text='Müller'.encode('utf8')
            return b'\x45\x36'+v(actor)+bytes(4)+b'\x01'+bytes([len(text)])+text+(25).to_bytes(4,'little')
        names.observe(ctx,header(9000),'one',book)
        self.assertEqual(names.apply(book,'one',2_000_000),[])
        names.observe(ctx,header(8257),'other-flow',book)
        self.assertEqual(names.apply(book,'one',2_000_000),[])
        names.observe(ctx,header(8257),'one',book)
        accepted=names.apply(book,'one',2_000_000)
        self.assertEqual(accepted[0]['name'],'Müller')
        self.assertIsNone(accepted[0]['server']);self.assertTrue(accepted[0]['self_identity'])
        from combat_names import explicit_local_identity
        self.assertIsNone(explicit_local_identity(book,'one',dict(actor=8257)))

    def test_named_npc_effect_target_is_not_its_named_player_even_with_matching_casts(self):
        # A named boss-mechanic entity can cast on a DIFFERENT proven member.
        # Two agreeing cast messages therefore do not make the name an owner.
        b=GroupBook();names=CombatNames();ctx=dict(flow='one',clock_us=2_000_000,packet=20)
        b.confirm_local_actor('one',dict(actor=9000,first_us=1_000_000))
        b.confirm_party_actor(ctx,dict(actor=8257,epoch=0,first_us=1_000_000,
            confirmed_us=2_000_000,basis='owned-party-vitals-sa-1b92'))
        text=b'Other';profile=bytearray(175)
        profile[:6]=(2920364).to_bytes(4,'little')+b'\0\x02'
        spawn=b'\x41\x36'+v(35000)+b'\x1c\0\x01'+bytes([len(text)])+text+profile
        for packet,body in enumerate((spawn,
                b'\x02\x38'+v(35000)+b'\0'+(1604521).to_bytes(4,'little')+b'\x01\x02'+v(8257)+bytes(16)+b'\x90\x4e\x01\xe8\x07',
                b'\x03\x38'+v(35000)+b'\0'+v(8257)+(1604521).to_bytes(4,'little')+bytes(25)),20):
            names.observe(dict(ctx,packet=packet),body,'one',b)
            self.assertEqual(names.apply(b,'one',2_000_000),[])
        self.assertEqual(b.names,{})

    def test_summon_owner_profile_rejects_unknown_footer_and_all_truncated_prefixes(self):
        for model in (2920647,2920807):
            body=summon(model=model)
            for end in range(len(body)):
                with self.subTest(model=model,end=end):
                    with self.assertRaises((ValueError,UnicodeError,IndexError)):effect_player(body[:end])
            footer_size=42 if model==2920647 else 24
            for offset in (0,1,3,17,18,footer_size-1):
                changed=bytearray(body);changed[len(body)-footer_size+offset]^=0xff
                with self.subTest(model=model,offset=offset):
                    with self.assertRaises(ValueError):effect_player(bytes(changed))

    def test_exact_caster_and_target_profiles_with_utf8_and_no_guild(self):
        for body,realm in ((caster(name='Müller'),None),(caster(model=2920626),None),
                           (target(),2405),(target(server=None),None),(target(guild=''),2405)):
            with self.subTest(body=body[:8]):
                result=effect_player(body)
                self.assertEqual(result['actor'],8257);self.assertEqual(result['server'],realm)

    def test_summon_owner_variants_carry_player_id_and_realm_not_spawn_id(self):
        for model in (2920647,2920807):
            body=summon(name='Müller',server=1401,model=model)
            entry=effect_player(body)
            self.assertEqual((entry['actor'],entry['name'],entry['server']),(8257,'Müller',1401))
            self.assertEqual(entry['basis'],'4136-named-summon-owner')
            for bad in (body[:-1],body+b'\0',summon(server=999,model=model)):
                with self.assertRaises(ValueError):effect_player(bad)
            from inspect_wire import varint
            _,a=varint(body,2);b=bytearray(body);b[a]=0x1c
            with self.assertRaises(ValueError):effect_player(bytes(b))

    def test_unknown_models_target_caster_confusion_and_malformed_fields_reject(self):
        good=caster();_,at=__import__('inspect_wire').varint(good,2)
        invalid=[good[:-1],good+b'\0',caster(actor=35000),caster(actor=0),target(server=999),target()[:-1]]
        for index,value in ((at,0x1c),(at+2,0),(at+3,0),(at+4+len('Example'),0)):
            bad=bytearray(good);bad[index]=value;invalid.append(bytes(bad))
        bad=bytearray(target());bad[-6]=1;invalid.append(bytes(bad))
        for bad in invalid:
            with self.subTest(prefix=bad[:9]):
                with self.assertRaises((ValueError,UnicodeError)):effect_player(bad)

    def test_names_never_prove_party_membership_or_name_nearby_actors(self):
        b=GroupBook();names=CombatNames();ctx=dict(flow='one',clock_us=2_000_000,packet=20)
        names.observe(ctx,caster(),'one',b)
        self.assertEqual(names.apply(b,'one',2_000_000),[]);self.assertEqual(b.names,{})
        b.confirm_local_actor('one',dict(actor=9000,first_us=1_000_000,basis='test-owned-self'))
        self.assertEqual(names.apply(b,'one',2_000_000),[]);self.assertEqual(b.names,{})
        b.confirm_party_actor(ctx,dict(actor=8257,epoch=0,first_us=1_000_000,
            confirmed_us=2_000_000,basis='owned-party-vitals-sa-1b92'))
        accepted=names.apply(b,'one',2_000_000)
        self.assertEqual(accepted[0]['name'],'Example');self.assertFalse(accepted[0]['self_identity'])
        self.assertEqual(b.names[('one',8257)],{('Example',None)})

    def test_unowned_stale_previous_epoch_and_preproof_effects_do_not_apply(self):
        for case in ('unowned','stale','epoch','preproof'):
            with self.subTest(case=case):
                b=GroupBook();n=CombatNames();c=dict(flow='one',clock_us=2_000_000,packet=20)
                b.confirm_local_actor('one',dict(actor=8257,first_us=6_000_000 if case=='preproof' else 1_000_000))
                n.observe(c,target(),'two' if case=='unowned' else 'one',b)
                if case=='epoch':b.roster_epochs['one']=1
                self.assertEqual(n.apply(b,'one',33_000_001 if case=='stale' else 6_000_000),[])
                self.assertEqual(b.names,{})

    def test_explicit_name_just_before_first_vitals_waits_for_independent_membership(self):
        b=GroupBook();n=CombatNames();ctx=dict(flow='one',clock_us=2_000_000,packet=20)
        n.observe(ctx,summon(),'one',b)
        self.assertEqual(n.apply(b,'one',2_100_000),[])
        b.confirm_party_actor(dict(flow='one',clock_us=4_200_000,packet=42),
            dict(actor=8257,epoch=0,first_us=2_100_000,confirmed_us=4_200_000,
                 basis='owned-party-vitals-sa-1b92'))
        accepted=n.apply(b,'one',4_200_000)
        self.assertEqual(accepted[0]['name'],'Example')
        self.assertEqual(b.party_actor_proofs[('one',0,8257)]['first_us'],2_100_000)

    def test_conflicting_names_remain_ambiguous_and_do_not_overwrite_headers(self):
        b=GroupBook();n=CombatNames();c=dict(flow='one',clock_us=2_000_000,packet=20)
        b.confirm_local_actor('one',dict(actor=8257,first_us=1_000_000))
        b.identify('one',dict(actor=8257,name='Different',server=2405))
        n.observe(c,target(),'one',b);n.apply(b,'one',2_000_000)
        self.assertEqual({name for name,realm in b.names[('one',8257)]},{'Example','Different'})
        from combat_names import explicit_local_identity
        self.assertIsNone(explicit_local_identity(b,'one',dict(actor=8257)))


if __name__=='__main__':unittest.main()
