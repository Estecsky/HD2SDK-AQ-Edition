"""Versioned clothing response compiler, separate from HD2PHY1 spring data."""
from __future__ import annotations
import copy
import hashlib
import math
import struct
import zlib
from . import rig_format
from .unit_rig_profiles import _multiply, _inverse_rigid

CAPABILITY='clothing-pose-driver-v1'
MAGIC=b'HD2POSE1'
GUARD=b'HD2POSE_REQUIRES_V1\0'
HEADER=struct.Struct('<8s6IQ32s12f')
RULE=struct.Struct('<3I2iIf8f')
AXIS=struct.Struct('<4I')
POINT=struct.Struct('<4f')
MODES=('SINGLE','MAX','MIN','ADD')
IDENTITY=[1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.]

class ClothingPoseError(ValueError): pass

def _finite(value):
    try: value=float(value)
    except (ValueError,TypeError,OverflowError) as e: raise ClothingPoseError('衣物驱动必须为有限数值') from e
    if not math.isfinite(value): raise ClothingPoseError('衣物驱动包含非有限数值')
    return value

def enabled_rules(project):
    return [r for r in project.get('clothing_pose',{}).get('rules',[]) if r.get('enabled')]

def validate_author(project):
    doc=project.get('clothing_pose')
    if doc is None:return []
    if doc.get('schema')!=CAPABILITY:raise ClothingPoseError('未知衣物驱动作者协议')
    rules=doc.get('rules')
    if not isinstance(rules,list) or len(rules)>64:raise ClothingPoseError('衣物驱动规则数量无效')
    parents={b['name']:b.get('parent') for b in project['shared_bones']}
    if len(parents)!=len(project['shared_bones']):raise ClothingPoseError('衣物驱动共享骨名重复')
    def ancestors(name):
        seen=set()
        while name:
            if name in seen or name not in parents:raise ClothingPoseError('衣物驱动骨绑定缺失或循环')
            seen.add(name);name=parents[name]
        return seen
    for name in parents:ancestors(name)
    simulated={j['bone'] for c in project.get('chains',[]) for i,j in enumerate(c['joints']) if i or c.get('root_swing')}
    occupied=set()
    for r in rules:
        if type(r.get('enabled')) is not bool:raise ClothingPoseError('驱动启用字段无效')
        target=r.get('target')
        if target not in parents or target in occupied or target in simulated:
            raise ClothingPoseError('衣物目标缺失、重复或与模拟骨冲突（停用项也占用）')
        occupied.add(target)
    active={r['target'] for r in rules if r['enabled']}
    for r in rules:
        if not r['enabled']:continue
        if r.get('combination') not in MODES or not 0<=_finite(r.get('influence'))<=1:
            raise ClothingPoseError('衣物驱动模式或影响系数无效')
        keys=['source','reference','neutral']
        if r['combination']!='SINGLE':keys+=['secondary','secondary_reference','secondary_neutral']
        for key in keys:
            value=r.get(key)
            if 'neutral' in key:
                if not isinstance(value,(list,tuple)) or len(value)!=4 or sum(_finite(x)**2 for x in value)<1.e-16:
                    raise ClothingPoseError('衣物驱动中立四元数无效')
            elif value or 'reference' not in key:
                if not value or ancestors(value)&(active|simulated):raise ClothingPoseError('衣物输入位于驱动/模拟分支或没有绑定')
        axes=r.get('axes')
        if not isinstance(axes,list) or len(axes)!=3 or {a.get('axis') for a in axes}!=set('XYZ'):
            raise ClothingPoseError('衣物驱动必须包含独立XYZ三轴')
        for a in axes:
            if type(a.get('invert')) is not bool or type(a.get('invert_secondary')) is not bool or a.get('secondary_axis') not in ('X','Y','Z'):
                raise ClothingPoseError('衣物驱动输入轴设置无效')
            points=a.get('points')
            if not isinstance(points,list) or not 2<=len(points)<=256:raise ClothingPoseError('衣物曲线点数超限')
            inputs=[]
            for point in points:
                if len(point)!=2 or len(point[1])!=3:raise ClothingPoseError('衣物曲线采样格式无效')
                values=[_finite(point[0]),*(_finite(v) for v in point[1])]
                if any(abs(v)>180 for v in values):raise ClothingPoseError('衣物采样角度超出正负180度')
                inputs.append(values[0])
            if len(set(inputs))!=len(inputs):raise ClothingPoseError('衣物曲线输入角度重复')
    return rules

def dependencies(rule):
    result={rule['target']}
    if rule['enabled']:
        keys=['source','reference']+(['secondary','secondary_reference'] if rule['combination']!='SINGLE' else [])
        result.update(rule.get(k) for k in keys if rule.get(k))
    return result

def consumer_parts(project, weights, target):
    """Weighted descendants plus explicitly retained collider dependencies.

    A static collider may intentionally serve independently saved body/head
    chains. Its driver is copied to those domains, not mistaken for shared
    skinned geometry. Match the existing conservative collider retention rule.
    """
    direct={part for part,bones in weights.items() if target in bones}
    parents={b['name']:b.get('parent') for b in project['shared_bones']}
    colliders=[]
    for collider in project.get('colliders',()):
        found=False
        for key in ('bone','bone_b'):
            name=collider.get(key)
            seen=set()
            while name:
                if name in seen or name not in parents:raise ClothingPoseError('碰撞依赖骨绑定缺失或循环')
                seen.add(name)
                if name==target:found=True
                name=parents[name]
        if found:colliders.append(collider)
    indirect=set()
    if colliders:
        for chain in project.get('chains',()):
            affected={j['bone'] for i,j in enumerate(chain['joints']) if i or chain.get('root_swing')}
            if any(not c.get('dynamic') or c.get('bone') in affected for c in colliders):
                indirect.update(part for part,bones in weights.items() if affected & bones)
    return direct,indirect


def scope_rules(project, selected, weights):
    rules=validate_author(project)
    keep=[]
    for rule in rules:
        direct,indirect=consumer_parts(project,weights,rule['target'])
        consumers=direct|indirect
        current=consumers&selected
        if current:
            if direct and not direct<=selected:raise ClothingPoseError('衣物目标跨身体/头盔或未保存部位')
            if any((p=='Head')==('Head' in selected) for p in indirect-selected):
                raise ClothingPoseError('衣物碰撞驱动依赖本次未保存的同域部位')
            keep.append(copy.deepcopy(rule))
        elif consumers:
            if any((p=='Head')==('Head' in selected) for p in consumers):
                raise ClothingPoseError('衣物目标依赖本次未保存的同域部位')
        elif rule['enabled']:
            raise ClothingPoseError('衣物目标没有可验证的加权后代消费部位：'+rule['target'])
    return {'schema':CAPABILITY,'rules':keep}

def profile_digest(profile):
    bones=profile['target_bones']
    flags=rig_format._flags(profile.get('flags'),rig_format.PROFILE_FLAGS,'clothing profile')
    uid=int(profile['unit_id'],16) if isinstance(profile['unit_id'],str) else int(profile['unit_id'])
    body=struct.pack('<QIII',uid,profile['palette_slots'],flags,len(bones))
    for b in bones:
        body+=rig_format.TARGET.pack(rig_format._name64(b['name'],'clothing bone'),b['parent'],b['source_index'],
            b['palette_slot'],rig_format._flags(b.get('flags'),rig_format.TARGET_FLAGS,'clothing bone'),
            b.get('translation_scale',1.),*rig_format._matrix(b['rest_local'],'clothing Rest'))
    return hashlib.sha256(body).digest()

def compile_resource(project, profile, rules=None):
    validate_author(project)
    rules=enabled_rules(project) if rules is None else [r for r in rules if r['enabled']]
    if not rules:raise ClothingPoseError('不输出没有启用规则的衣物资源')
    bones=profile['target_bones'];index={b['name']:i for i,b in enumerate(bones)}
    author=project['shared_bones'];aindex={b['name']:i for i,b in enumerate(author)}
    # Author list order is not a runtime index. Resolve parent-first explicitly.
    world={}
    def author_world(name):
        if name not in world:
            b=author[aindex[name]];parent=b.get('parent')
            world[name]=_multiply(author_world(parent),b['rest_local']) if parent else b['rest_local']
        return world[name]
    actual=rig_format.rest_globals(bones,'clothing targets')
    basis=_multiply(author_world(bones[0]['name']),_inverse_rigid(actual[0]))
    rig_format._matrix(basis,'clothing basis')
    for b,m in zip(bones,actual):
        if b['name'] not in aindex or max(abs(x-y) for x,y in zip(_multiply(basis,m),author_world(b['name'])))>2.e-4:
            raise ClothingPoseError('衣物坐标基与最终Unit Rest不一致：'+b['name'])
    def resolve(name):
        if not name:return -1
        if name not in index:raise ClothingPoseError('最终运行骨架缺少衣物依赖：'+name)
        return index[name]
    payload=b''
    for r in rules:
        dual=r['combination']!='SINGLE'
        payload+=RULE.pack(resolve(r['source']),resolve(r['target']),resolve(r['secondary']) if dual else 0,
            resolve(r.get('reference')),resolve(r.get('secondary_reference')) if dual else -1,
            MODES.index(r['combination']),r['influence'],*r['neutral'],*(r['secondary_neutral'] if dual else [1.,0.,0.,0.]))
        for axis in 'XYZ':
            a=next(a for a in r['axes'] if a['axis']==axis);points=sorted(a['points'])
            payload+=AXIS.pack(int(a['invert'])|int(a['invert_secondary'])<<1,'XYZ'.index(a['secondary_axis']),len(points),0)
            payload+=b''.join(POINT.pack(x,*v) for x,v in points)
    uid=int(profile['unit_id'],16) if isinstance(profile['unit_id'],str) else int(profile['unit_id'])
    return HEADER.pack(MAGIC,1,HEADER.size,HEADER.size+len(payload),zlib.crc32(payload),len(rules),0,
                       uid,profile_digest(profile),*basis)+payload

def attach_single_unit_resources(compiled, project, rig_document):
    rules=enabled_rules(project)
    if not rules:return compiled
    # Called only for the separately saved helmet domain. Shared BODY gets one
    # canonical program, never one copy for each rendering part.
    files=dict(compiled['files']);manifest=copy.deepcopy(compiled['manifest'])
    for p in rig_document['profiles']:
        uid=int(p['unit_id'],16) if isinstance(p['unit_id'],str) else int(p['unit_id'])
        stem=manifest['rig']['file'][:-7]+f'.{uid:016x}'
        name=stem+'.hd2pose'
        if name in files:raise ClothingPoseError('衣物资源文件重复')
        files[name]=compile_resource(project,p,rules)
        files[stem+'.requires-clothing-v1.rigbin']=GUARD
    manifest.setdefault('runtime_requirements',[]).append(CAPABILITY)
    # Keep diagnostic JSON consistent without putting it into the runtime ZIP.
    import json
    for name in files:
        if name.endswith('.hd2physpack.json'):
            files[name]=(json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')
    result=dict(compiled);result.update(files=files,manifest=manifest);return result
