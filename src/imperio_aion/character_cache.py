# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded local last-known facts. Never a source of live protocol state."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from datetime import datetime,timezone
from daily_duty import valid_duty,historical_duty
from weekly_orders import valid_order,historical_order
from activity_entries import valid_entry,historical_entry
from resource_gains import valid_gain_value
from manual_activity import valid_confirmation,presentation_state,confirmation
from altgard_orders import valid_tracking,tracked_presentation
from order_accumulator import valid_accumulator_state,historical_accumulator

PET_SOUL_SOURCES=('0090-explicit','experimental-sparse-zero-level1')


def cache_directory():
    base=os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_DATA_HOME') or str(Path.home()/'.local/share')
    return Path(base)/'ImperioAION'/'cache-personagens'


def identity_key(identity):
    if not isinstance(identity,dict):return None
    name=identity.get('name');server=identity.get('server')
    if not isinstance(name,str) or not 0<len(name)<=64 or any(ord(c)<32 for c in name):return None
    if type(server) is not int or not 1000<=server<3000:return None
    return hashlib.sha256(json.dumps([server,name],ensure_ascii=False).encode()).hexdigest()


def number(value,limit=1000000):return type(value) is int and 0<=value<=limit


class CharacterCache:
    def __init__(self,directory=None):
        self.directory=Path(directory) if directory is not None else cache_directory()
        self.records={};self.dirty=set();self.error=None;self.next_write=0
        try:
            for path in sorted(self.directory.glob('*.json'))[:100]:
                if path.stat().st_size>2*1024*1024:continue
                try:
                    record=json.loads(path.read_text(encoding='utf-8'))
                    key=identity_key(record.get('identity'))
                    if record.get('schema')!=1 or not key or path.stem!=key:continue
                    if not isinstance(record.get('pets',{}),dict) or len(record.get('pets',{}))>500:continue
                    # Copy only whitelisted scalar facts from disk.
                    clean=dict(schema=1,identity={k:record['identity'][k] for k in ('name','server')},pets={},recent=[])
                    for identifier,pet in record.get('pets',{}).items():
                        if isinstance(identifier,str) and identifier.isdigit() and 0<int(identifier)<1000000 and self.valid_pet(pet):
                            clean['pets'][identifier]={k:pet.get(k) for k in ('level','souls','saved_at')}
                            if pet.get('souls') is not None and pet.get('soul_baseline_source') in PET_SOUL_SOURCES:
                                clean['pets'][identifier]['soul_baseline_source']=pet['soul_baseline_source']
                            times=pet.get('field_saved_at',{})
                            if isinstance(times,dict):clean['pets'][identifier]['field_saved_at']={k:v for k,v in times.items() if k in ('level','souls') and isinstance(v,str) and len(v)<=64}
                    od=record.get('od')
                    if isinstance(od,dict) and all(number(od.get(k)) for k in ('current','bonus','capacity')) and od['capacity']>0:
                        clean['od']={k:od.get(k) for k in ('current','bonus','capacity','capacity_source','saved_at')}
                        if isinstance(od.get('confirmed_at'),str) and len(od['confirmed_at'])<=64:clean['od']['confirmed_at']=od['confirmed_at']
                    recent=record.get('recent',[])
                    if isinstance(recent,list):clean['recent']=[k for k in recent[:500] if isinstance(k,str) and k in clean['pets']]
                    if number(record.get('class_id'),64):clean['class_id']=record['class_id']
                    gains=record.get('resource_gains',{})
                    if isinstance(gains,dict):
                        clean['resource_gains']={k:self.gain_fields(v) for k,v in gains.items()
                            if k in ('kinah_daily','abyss_weekly') and self.valid_gain(v,k)}
                    duty=record.get('daily_duty')
                    if valid_duty(duty):clean['daily_duty']={k:duty[k] for k in ('current','limit','reset_at')}
                    order=record.get('latesran')
                    baselines=record.get('activity_baselines',{})
                    if isinstance(baselines,dict):
                        clean['activity_baselines']={key:dict(value) for key,value in baselines.items()
                            if valid_confirmation(key,value)}
                    if valid_order(order):clean['latesran']={k:order[k] for k in ('current','limit','reset_at')}
                    tracking=record.get('altgard_tracking')
                    if valid_tracking(tracking):clean['altgard_tracking']=copy.deepcopy(tracking)
                    accumulator=record.get('order_accumulator')
                    if (valid_accumulator_state(accumulator) and accumulator.get('schema')==2
                            and accumulator.get('calculation_rule')=='weekly-reference-17'
                            and accumulator['identity']==clean['identity']):
                        clean['order_accumulator']=self.accumulator_fields(accumulator)
                    entries=record.get('activity_entries',{})
                    if isinstance(entries,dict) and entries:
                        clean['activity_entries']={k:self.entry_fields(v) for k,v in entries.items() if valid_entry(k,v)}
                    self.records[key]=clean
                except (ValueError,TypeError,AttributeError,KeyError):self.error='Um registro de cache inválido foi ignorado'
        except OSError as exc:self.error=str(exc)

    @staticmethod
    def accumulator_fields(value):
        fields=('schema','identity','week_start','reset_at','base_accumulated','base_latesran',
                'current','last_latesran','server_stamp_ms','observed_at','partial','reason',
                'base_stamp_ms','base_observed_at','base_source','calculation_rule','invalidated','invalid_reason','invalidated_at')
        return {key:copy.deepcopy(value[key]) for key in fields if key in value}

    @staticmethod
    def valid_pet(pet):
        return isinstance(pet,dict) and (pet.get('level') is None or type(pet.get('level')) is int and pet['level'] in (1,2,3)) and (pet.get('souls') is None or number(pet.get('souls')))

    @staticmethod
    def valid_gain(value,key='kinah_daily'):
        return valid_gain_value(key,value)

    @staticmethod
    def gain_fields(value):
        clean={k:value[k] for k in ('current','limit')}
        if isinstance(value.get('saved_at'),str) and len(value['saved_at'])<=64:clean['saved_at']=value['saved_at']
        if isinstance(value.get('confirmed_at'),str) and len(value['confirmed_at'])<=64:clean['confirmed_at']=value['confirmed_at']
        return clean

    @staticmethod
    def entry_fields(value):
        clean={k:value[k] for k in ('current','bonus','limit','reset_at') if k in value}
        for key in ('observed_at','saved_at'):
            if isinstance(value.get(key),str) and len(value[key])<=64:clean[key]=value[key]
        return clean

    def remember(self,identity,pets,od=None,recent=(),class_id=None,resource_gains=None,daily_duty=None,latesran=None,activity_entries=None,altgard_tracking=None,order_accumulator=None):
        key=identity_key(identity)
        if not key:return
        if key not in self.records and len(self.records)>=100:
            self.error='Limite de 100 personagens no cache';return
        old=self.records.get(key)
        record=copy.deepcopy(old) if old else dict(schema=1,identity={k:identity[k] for k in ('name','server')},pets={},recent=[])
        now=datetime.now(timezone.utc).isoformat()
        for identifier,pet in pets.items():
            if not self.valid_pet(pet):continue
            previous=record['pets'].get(identifier,{})
            value={k:pet.get(k) if pet.get(k) is not None else previous.get(k) for k in ('level','souls')}
            # A newly confirmed level has its own soul balance. The balance
            # saved for the previous stage must not become that new balance.
            if (pet.get('level') is not None and pet.get('level')!=previous.get('level')
                    and pet.get('souls') is None):value['souls']=None
            # A newly observed balance without a decoded stage cannot be
            # paired with a historical level, even if the numbers fit it.
            if pet.get('souls') is not None and pet.get('level') is None:value['level']=None
            baseline_source=(pet.get('soul_baseline_source') if pet.get('souls') is not None
                             else previous.get('soul_baseline_source'))
            value['soul_baseline_source']=(baseline_source if value['souls'] is not None
                                          and baseline_source in PET_SOUL_SOURCES else None)
            if any(previous.get(k)!=v for k,v in value.items()):
                times=dict(previous.get('field_saved_at',{}))
                for field in ('level','souls'):
                    if pet.get(field) is not None and pet[field]!=previous.get(field):times[field]=now
                record['pets'][identifier]=dict(value,saved_at=now,field_saved_at=times)
        if od and od.get('available') and not od.get('cached'):
            value={k:od.get(k) for k in ('current','bonus','capacity','capacity_source')}
            previous=record.get('od',{})
            if any(previous.get(k)!=v for k,v in value.items()):record['od']=dict(value,saved_at=now)
            if isinstance(od.get('observed_at'),str) and len(od['observed_at'])<=64:
                record['od']['confirmed_at']=od['observed_at']
        for resource_key,value in (resource_gains or {}).items():
            if resource_key not in ('kinah_daily','abyss_weekly') or not self.valid_gain(value,resource_key) or not value.get('available') or value.get('cached'):continue
            gains=record.setdefault('resource_gains',{});previous=gains.get(resource_key,{})
            fields={k:value[k] for k in ('current','limit')}
            if any(previous.get(k)!=v for k,v in fields.items()):gains[resource_key]=dict(fields,saved_at=now)
            if isinstance(value.get('observed_at'),str) and len(value['observed_at'])<=64:
                gains[resource_key]['confirmed_at']=value['observed_at']
        if (valid_duty(daily_duty) and daily_duty.get('available')
                and not daily_duty.get('cached') and not daily_duty.get('reset_pending') and not daily_duty.get('manual')):
            record['daily_duty']={k:daily_duty[k] for k in ('current','limit','reset_at')}
        if recent:record['recent']=list(dict.fromkeys(list(recent)+record['recent']))[:500]
        if valid_order(latesran) and latesran.get('available') and not latesran.get('cached') and not latesran.get('reset_pending') and not latesran.get('manual'):
            record['latesran']={k:latesran[k] for k in ('current','limit','reset_at')}
        for entry_key,value in (activity_entries or {}).items():
            if valid_entry(entry_key,value) and value.get('available') and not value.get('cached'):
                entries=record.setdefault('activity_entries',{});previous=entries.get(entry_key,{})
                fields=self.entry_fields(value);fields.pop('saved_at',None)
                if any(previous.get(k)!=v for k,v in fields.items()):entries[entry_key]=dict(fields,saved_at=now)
        if number(class_id,64) and class_id>0:record['class_id']=class_id
        if (valid_tracking(altgard_tracking) and altgard_tracking['baseline']==record.get('activity_baselines',{}).get('altgard')):
            record['altgard_tracking']=copy.deepcopy(altgard_tracking)
        if (valid_accumulator_state(order_accumulator) and order_accumulator.get('schema')==2
                and order_accumulator.get('calculation_rule')=='weekly-reference-17'
                and order_accumulator['identity']==record['identity']):
            record['order_accumulator']=self.accumulator_fields(order_accumulator)
        if record!=old:self.records[key]=record;self.dirty.add(key)

    def select(self,name,identity=None):
        key=identity_key(identity)
        if key:return copy.deepcopy(self.records.get(key))
        matches=[v for v in self.records.values() if v['identity']['name']==name]
        return copy.deepcopy(matches[0]) if len(matches)==1 else None

    def confirm_activities(self,identity,counts,now=None):
        key=identity_key(identity)
        if not key:raise ValueError('Identidade do personagem não confirmada')
        values={name:confirmation(name,count,now) for name,count in counts.items()}
        self.remember(identity,{})
        if key not in self.records:raise ValueError(self.error or 'Não foi possível criar o histórico do personagem')
        self.records[key].setdefault('activity_baselines',{}).update(values)
        self.dirty.add(key);self.flush(force=True)
        if self.error:raise OSError(self.error)

    def reload(self):
        """Re-read validated local facts; never discard unsaved in-memory facts."""
        self.flush(force=True)
        disk=CharacterCache(self.directory)
        for key,record in disk.records.items():
            if key not in self.dirty:self.records[key]=record
        if disk.error:self.error=disk.error

    def flush(self,force=False):
        if not self.dirty or not force and time.monotonic()<self.next_write:return
        self.next_write=time.monotonic()+5
        try:
            self.directory.mkdir(parents=True,exist_ok=True)
            for key in list(self.dirty):
                payload=json.dumps(self.records[key],ensure_ascii=False,indent=2)
                temp=None
                try:
                    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=self.directory,prefix=key+'.',suffix='.tmp',delete=False) as f:
                        temp=Path(f.name);f.write(payload);f.flush();os.fsync(f.fileno())
                    os.replace(temp,self.directory/(key+'.json'));self.dirty.discard(key)
                finally:
                    if temp is not None and temp.exists():temp.unlink()
            self.error=None
        except OSError as exc:self.error=str(exc)

    def decorate(self,result,record,confirmed=False,allow_manual=True):
        result['character_cache']=dict(path=str(self.directory),error=self.error,identity_confirmed=confirmed,
            selected=record['identity'] if record else None,account_identifier_decoded=False,
            pets=copy.deepcopy(record.get('pets',{})) if record else {},class_id=record.get('class_id') if record else None)
        if not record:return
        gains=result.setdefault('resource_gains',{})
        for key,value in record.get('resource_gains',{}).items():
            live=gains.get(key,{})
            if not live.get('available') and live.get('allow_cached',True):
                gains[key]=dict(value,available=True,cached=True,experimental=True,
                    reading_error=live.get('reading_error'),
                    detail='Último ganho salvo; ainda não confirmado neste período')
        if record.get('daily_duty') and not result.get('daily_duty',{}).get('available'):
            result['daily_duty']=historical_duty(record['daily_duty'])
        if record.get('latesran') and not result.get('weekly_orders',{}).get('latesran',{}).get('available'):
            result.setdefault('weekly_orders',{})['latesran']=historical_order(record['latesran'])
        if allow_manual and confirmed and result.get('login_session',{}).get('confirmed'):
            for key,value in record.get('activity_baselines',{}).items():
                manual=presentation_state(key,value)
                if not manual:continue
                target=result if key=='daily' else result.setdefault('weekly_orders',{})
                field='daily_duty' if key=='daily' else key
                live=target.get(field,{})
                if not live.get('available') or live.get('cached') or live.get('reset_pending'):
                    target[field]=manual
        tracking=record.get('altgard_tracking')
        if allow_manual and valid_tracking(tracking) and tracking['baseline']==record.get('activity_baselines',{}).get('altgard'):
            historical=tracked_presentation(tracking)
            if historical.get('available'):result.setdefault('weekly_orders',{})['altgard']=historical
        orders=result.setdefault('weekly_orders',{})
        live_altgard=orders.get('altgard',{})
        if (valid_accumulator_state(record.get('order_accumulator')) and not live_altgard.get('available')
                and live_altgard.get('allow_cached',True)):
            previous=historical_accumulator(record['order_accumulator'],record['identity'])
            if live_altgard.get('reading_error'):previous['reading_error']=live_altgard['reading_error']
            orders['altgard']=previous
        entries=result.setdefault('activity_entries',{})
        for key,value in record.get('activity_entries',{}).items():
            live=entries.get(key,{})
            if not live.get('available') and live.get('allow_cached',True):entries[key]=historical_entry(key,value)
        if not result['od_energy'].get('available') and result['od_energy'].get('allow_cached',True) and record.get('od'):
            result['od_energy']=dict(record['od'],available=True,cached=True,experimental=True,
                reading_error=result['od_energy'].get('reading_error'),
                detail='Último saldo conhecido; ainda não confirmado nesta sessão')
        state=result['pet_collection'];live=state.get('entries',[])
        keys=[e['pet_id'] for e in live] or record.get('recent',[])[:500]
        rows=[]
        from pet_catalog import CATALOG
        for key in keys[:500]:
            row=next((dict(e) for e in live if e['pet_id']==key),{})
            pet=record.get('pets',{}).get(key)
            if pet and not row.get('account_count_known') and not row.get('max_level_confirmed'):
                meta=CATALOG.get(key) or dict(name='Mascote '+key,icon_key=None)
                borrowed=[]
                # Fill missing fields only. Cached metadata never replaces a
                # level or balance just received from this character's game.
                if row.get('level') is None and pet.get('level') is not None:
                    row['level']=pet['level'];borrowed.append('level')
                if (row.get('souls') is None and pet.get('souls') is not None
                        and (row.get('level') is None or row['level']==pet.get('level'))):
                    row['souls']=pet['souls'];borrowed.append('souls')
                    if pet.get('soul_baseline_source') in PET_SOUL_SOURCES:
                        row['soul_baseline_source']=pet['soul_baseline_source']
                if borrowed:
                    row.setdefault('pet_id',key);row.setdefault('name',meta['name']);row.setdefault('icon_key',meta['icon_key'])
                    if row.get('next_required') is None:row['next_required']={1:25,2:75,3:0}.get(row.get('level'))
                    row.update(cached=True,cached_fields=borrowed,saved_at=pet.get('saved_at'),account_count_known=False)
                    if 'souls' in borrowed:row['soul_balance_known']=False
            if row:rows.append(row)
        state['entries']=rows
        state['cache_display']=bool(rows and all(e.get('cached') for e in rows))
        state['cache_mixed']=bool(any(e.get('cached') for e in rows) and not state['cache_display'])
