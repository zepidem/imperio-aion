# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Observed SA character-list milestones, isolated from login/DPS attribution.

The bounded 26 36 profile was compared across five local return-to-selection
captures. The initial 15 36 single-avatar response was observed on 2026-10-16.
Unknown layouts remain pending. No token, avatar list or actor ID is
retained, and recognizing this screen never grants character resource access.
"""


def selection_profile(body):
    if not isinstance(body,bytes):return None
    profiles={b'\x26\x36\0\0\x02':(3713,774,3319,'sa-2636-avatar-list'),
              b'\x15\x36\0\0\x20':(3787,790,3335,'sa-1536-initial-avatar-list')}
    profile=profiles.get(body[:5])
    if profile is None:return None
    base,name_size_at,realm_at,key=profile
    if not base+1<=len(body)<=base+36:return None
    # This observed single-avatar layout has one length-prefixed UTF-8 name,
    # followed by class/appearance/level, and a separate realm field.
    size=body[name_size_at];name_at=name_size_at+1
    if not 1<=size<=36 or len(body)!=base+size:
        return None
    try:name=body[name_at:name_at+size].decode('utf8','strict')
    except UnicodeDecodeError:return None
    if not name.strip() or any(ord(c)<32 or ord(c)==127 for c in name):
        return None
    at=name_at+size
    class_id=int.from_bytes(body[at:at+4],'little')
    level=int.from_bytes(body[at+6:at+10],'little')
    realm=int.from_bytes(body[realm_at+size:realm_at+size+2],'little')
    return key if 1<=class_id<=64 and 1<=level<=1000 and 1000<=realm<3000 else None


def selection_response(body):return selection_profile(body) is not None


class CharacterSelection:
    def __init__(self):
        self.flow=None
        self.visited=False
        self.at_selection=False
        self.profile=None

    def observe(self,ctx,body,owned_incoming):
        flow=ctx['flow']
        if not owned_incoming or flow!=owned_incoming:
            return
        if self.flow and self.flow!=flow:
            self.flow=None;self.visited=False;self.at_selection=False;self.profile=None
        profile=selection_profile(body)
        if profile:
            self.flow=flow;self.visited=True;self.at_selection=True
            self.profile=profile
        elif self.flow==flow and body[:2]==b'\x0b\x61':
            from activity_entries import decode_initial_entries
            try:decode_initial_entries(body)
            except ValueError:return
            self.at_selection=False
        elif self.flow==flow and body[:2]==b'\x33\x36':
            from live_core import self_identity
            try:self_identity(body)
            except (ValueError,UnicodeDecodeError):return
            self.at_selection=False

    def snapshot(self,owned_incoming,source_ready):
        valid=bool(source_ready and owned_incoming and self.flow==owned_incoming)
        if not valid:
            self.flow=None;self.visited=False;self.at_selection=False;self.profile=None
        return dict(confirmed=valid and self.at_selection,
                    visited=valid and self.visited,
                    profile=self.profile if valid else None)
