"""Capability-gated shared BODY compiler used by independent save/export.

The contract supports complete chains, multiple BODY consumers, and their
colliders. It emits a deliberately incompatible legacy guard so an older
receiver cannot silently treat consumer Rigs as working single-Unit physics.
"""
from __future__ import annotations
import copy
import hashlib
import re
import struct
import zlib

from .physics_packaging import scope_physics_project, published_unit_rows, consumer_bones_by_part, unit_weight_sets
from .physics_compiler import _physics_bones, _runtime_stem, _unit_number
from .rig_format import build as build_rig, rest_globals
from .unit_rig_profiles import _multiply, _inverse_rigid, build_rig_document

CAPABILITY = "shared-body-v1-experimental"
CAPABILITY_V2 = "shared-body-v2-experimental"
CAPABILITY_RUNTIME = "shared-body-v2"
HEADER = struct.Struct("<8s6I64sQQ32sI")
REFERENCE = struct.Struct("<96sQ32s")
CONSUMER = struct.Struct("<QIII12f")
SELECTION = struct.Struct("<Q32s")
MAGIC = b"HD2SHR1\0"
GUARD = b"HD2SHARED_REQUIRES_V1\0"
GUARD_V2 = b"HD2SHARED_REQUIRES_V2\0"
PARTS = ("Hips", "LeftArm", "RightArm", "Torso", "Torso_Armor", "LeftLeg", "RightLeg")
UNMAPPED = 0xffffffff


class SharedPhysicsError(ValueError):
    pass


def _consumer_selections(plan, rows):
    """Bind each published Unit to an authored option and its native route.

    Several routes may be visible for one option; different options of a slot
    may never be active together. Names/Unit hashes alone cannot prove this.
    """
    selections = {}
    routes = {}
    native_parts = {}

    def add(slot, unit, native, kind):
        unit = int(unit); native = int(native)
        if unit in selections or unit not in rows or not 0 < native < 2**64:
            raise SharedPhysicsError('共享选择的 Unit/原生路由无效或重复')
        if rows[unit]['part_slot'] != slot or rows[unit]['kind'] != kind:
            raise SharedPhysicsError('共享选择与保存计划不一致')
        if native in native_parts and native_parts[native] != slot:
            raise SharedPhysicsError('共享原生路由跨语义部位重复')
        native_parts[native] = slot
        choice = hashlib.sha256((slot+'\0'+kind).encode('utf8')).digest()
        selected = routes.setdefault((slot, choice), set())
        if native in selected:
            raise SharedPhysicsError('同一共享选项的原生路由重复')
        selected.add(native)
        selections[unit] = (native, choice)

    for row in plan.get('native_saves', ()):
        unit = row.get('base_unit_id', row.get('native_unit_id'))
        add(row['part_slot'], unit, row.get('native_unit_id', unit), 'native_base')
    manifest = plan.get('difference_manifest', {})
    for group in manifest.get('body_groups', ()):
        for member in group.get('members', ()):
            for route in member.get('routes', ()):
                add(member['part_slot'], route['difference_unit_id'], route['native_unit_id'],
                    'body_difference:'+str(group.get('name', '')))
    for part in manifest.get('free_parts', ()):
        for option in part.get('options', ()):
            identity = str(option.get('id', option.get('name', '')))
            if not identity:
                raise SharedPhysicsError('共享自由选项缺少稳定身份')
            for route in option.get('routes', ()):
                add(part['part_slot'], route['difference_unit_id'], route['native_unit_id'],
                    'free_difference:'+identity)
    if set(selections) != set(rows):
        raise SharedPhysicsError('共享选择未覆盖全部保存 Unit')
    for slot in PARTS:
        sets = [value for (part, _), value in routes.items() if part == slot]
        if sets and any(value != sets[0] for value in sets[1:]):
            raise SharedPhysicsError('同部位共享选项的原生路由集合不一致')
    return selections


def _fixed(text, size):
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", text):
        raise SharedPhysicsError("共享包身份/文件名必须是安全 ASCII 名称")
    data = text.encode("ascii")
    if len(data) >= size:
        raise SharedPhysicsError("共享包身份/文件名过长")
    return data.ljust(size, b"\0")


def _canonical_profile(plan, authoring, rig, canonical_id):
    """Canonical author-space skeleton, including missing middle chain nodes.

    Consumer Rigs come from saved Units; their individual coordinate bases are
    verified later. The complete chain is never reconstructed from one mesh.
    """
    author = {b["name"]: b for b in authoring["shared_bones"]}
    needed = {b['name'] for p in rig['profiles'] for b in p['target_bones']}
    needed.update(j['bone'] for c in authoring['chains'] for j in c['joints'])
    needed.update(c[k] for c in authoring.get('colliders', ()) for k in ('bone','bone_b') if c.get(k))
    from .clothing_pose import enabled_rules, dependencies as pose_dependencies
    needed.update(n for rule in enabled_rules(authoring) for n in pose_dependencies(rule))
    closure = consumer_bones_by_part(authoring, {'canonical': needed})['canonical']
    if not closure.issubset(author):
        raise SharedPhysicsError("保存的 Unit 含共享作者骨架以外的骨骼")
    ordered = []
    pending = set(closure)
    while pending:
        ready = sorted(n for n in pending if not author[n].get('parent') or author[n]['parent'] not in pending)
        if not ready:
            raise SharedPhysicsError("共享骨架父链存在循环")
        ordered.extend(ready); pending.difference_update(ready)
    if len(ordered) > 256:
        raise SharedPhysicsError("共享规范骨架超过本轮 256 骨上限；不能删所需中间骨绕过")
    indices = {n:i for i,n in enumerate(ordered)}
    bones = [{'name': n, 'parent': indices.get(author[n].get('parent'), -1),
              'rest_local': author[n]['rest_local']} for n in ordered]
    world = rest_globals(bones, 'shared_canonical')
    snapshot = {'unit_id': canonical_id, 'palette_slots': len(bones), 'nodes':[
        {'name': b['name'], 'parent': b['parent'], 'world': m} for b,m in zip(bones,world)]}
    # Keep the full ancestry for world Rest calculation, but do not fabricate
    # output targets for unconsumed import wrappers (e.g. an axis-conversion
    # node above the public root). build_rig_document retains available public
    # channels; every chain node/collider/output bone is explicitly in needed.
    required = needed
    result = build_rig_document(authoring, [snapshot],
        required_bones_by_unit={canonical_id:required}, runtime_source_bones=rig['source_bones'],
        weighted_bones_by_unit={canonical_id:required}, rig_gender=plan.get('rig_gender'))
    return result


def compile_shared_physics_pack(plan, authoring_project, weighted_by_part,
        all_weighted_by_part, rig_document, physbone_build, *, runtime_capabilities=(),
        weighted_bones_by_unit=None):
    """Return pure byte payloads; caller stages them in an isolated directory.

    Explicit receiver capability is mandatory and is supplied by the independent
    save pipeline. This compiler does not mutate scenes, Patches or author files.
    """
    version = 2 if {CAPABILITY_V2, CAPABILITY_RUNTIME}.intersection(runtime_capabilities) else 1
    capability = (CAPABILITY_RUNTIME if CAPABILITY_RUNTIME in runtime_capabilities else
                  CAPABILITY_V2 if version == 2 else CAPABILITY)
    if capability not in runtime_capabilities:
        raise SharedPhysicsError("运行时未声明共享身体物理 v1 实验能力，拒绝生成")
    if plan.get('content_domain') != 'BODY':
        raise SharedPhysicsError("共享物理首轮仅支持身体")
    project_id, _ = _runtime_stem(plan)
    project_bytes = _fixed(project_id, 64)
    archive_match = re.search(r'(?i)(?<![0-9a-f])([0-9a-f]{16})(?![0-9a-f])',str(plan['archive_name']))
    archive = int(archive_match.group(1), 16)
    if not archive:
        raise SharedPhysicsError('Archive ID 不能为零')
    scoped = scope_physics_project(plan, authoring_project, weighted_by_part,
        all_weighted_by_part, solver_scope='shared_body_v1_experimental')
    from .clothing_pose import enabled_rules, compile_resource, CAPABILITY as POSE_CAPABILITY
    pose_rules=enabled_rules(scoped)
    if pose_rules:
        if POSE_CAPABILITY not in runtime_capabilities:raise SharedPhysicsError('运行时未声明衣物姿态驱动能力')
        if version!=2:raise SharedPhysicsError('衣物驱动共享封包需要已验证的v2消费身份')
        version=3
    if version == 1 and (len(scoped['chains']) != 1 or scoped.get('chain_links')):
        raise SharedPhysicsError('首轮共享协议仅支持一条完整链；多链/链间约束不能静默丢弃')
    build_rig(rig_document)  # existing index, finite matrix, count and flag gates
    rows = {r['unit_id']:r for r in published_unit_rows(plan)}
    unit_weights = unit_weight_sets(plan, weighted_by_part, weighted_bones_by_unit)
    selections = _consumer_selections(plan, rows) if version >= 2 else {}
    profiles = {}
    for p in rig_document['profiles']:
        uid = _unit_number(p['unit_id'],'shared profile')
        if uid in profiles or uid not in rows:
            raise SharedPhysicsError('共享消费 Unit 重复或不属于本次保存计划')
        missing = unit_weights[uid] - {b['name'] for b in p['target_bones']}
        if missing:
            raise SharedPhysicsError(f'Unit {uid:016x} 缺少实际加权骨：{sorted(missing)}')
        profiles[uid] = p
    if set(rows) != set(profiles):
        raise SharedPhysicsError('保存计划与共享消费 Rig 集合不一致')
    dependencies = consumer_bones_by_part(scoped, weighted_by_part)
    affected = set()
    affected.update(r['target'] for r in pose_rules)
    chain_consumers = {}
    public = {b['name'] for b in rig_document['source_bones']}
    for chain in scoped['chains']:
        if chain.get('root_swing') and chain['joints'][0]['bone'] in public:
            raise SharedPhysicsError('根骨摆动不能覆盖游戏公共骨骼')
        own = {j['bone'] for j in chain['joints'][0 if chain.get('root_swing') else 1:]}
        consumers = {part for part, names in dependencies.items() if names & own}
        if not consumers or not consumers.issubset(PARTS):
            raise SharedPhysicsError('共享链没有合法的身体消费者')
        affected.update(own)
        chain_consumers[chain['name']] = sorted(consumers)
    identity = project_bytes + struct.pack('<Q',archive)
    canonical_id = int.from_bytes(hashlib.sha256(b'HD2_SHARED_CANONICAL_V1\0'+identity).digest()[:8],'little')
    if canonical_id == 0 or canonical_id in rows:
        raise SharedPhysicsError('共享规范身份与实际 Unit 冲突')
    canonical_rig = _canonical_profile(plan, scoped, rig_document, canonical_id)
    canonical = canonical_rig['profiles'][0]
    cbones = canonical['target_bones']
    cindex = {b['name']:i for i,b in enumerate(cbones)}
    cworld = rest_globals(cbones,'canonical')
    ancestry = consumer_bones_by_part(scoped, {name:[name] for name in cindex})
    entries = []
    # Payloads and mappings follow numeric Unit order for stable revisions.
    consumer_rig = copy.deepcopy(rig_document)
    consumer_rig['profiles'] = [copy.deepcopy(profiles[u]) for u in sorted(profiles)]
    for uid in sorted(profiles):
        p = profiles[uid]; target = p['target_bones']
        mapping = [cindex[b['name']] if ancestry[b['name']] & affected else UNMAPPED for b in target]
        valid_mapping = [i for i in mapping if i != UNMAPPED]
        if len(set(valid_mapping)) != len(valid_mapping):
            raise SharedPhysicsError('共享骨映射重复')
        pworld = rest_globals(target,'consumer')
        basis = _multiply(pworld[0], _inverse_rigid(cworld[cindex[target[0]['name']]]))
        for i, ci in enumerate(mapping):
            if ci == UNMAPPED:
                continue  # native FK/IK is supplied per Unit, never overridden
            expected = _multiply(basis, cworld[ci])
            if max(abs(a-b) for a,b in zip(expected,pworld[i])) > 2e-4:
                raise SharedPhysicsError(f'Unit {uid:016x} 的骨 {target[i]["name"]} 与共享 Rest 不一致')
            parent = cbones[ci]['parent']
            while parent >= 0 and parent not in mapping:
                parent = cbones[parent]['parent']
            pindex = target[i]['parent']
            while pindex >= 0 and mapping[pindex] == UNMAPPED:
                pindex = target[pindex]['parent']
            mapped_parent = -1 if pindex < 0 else mapping[pindex]
            if parent != mapped_parent:
                raise SharedPhysicsError(f'Unit {uid:016x} 的共享父链不一致')
        selection = SELECTION.pack(*selections[uid]) if version >= 2 else b''
        entries.append(CONSUMER.pack(uid, PARTS.index(rows[uid]['part_slot']), len(mapping),0,*basis) + selection
                       + struct.pack('<'+'I'*len(mapping), *mapping))
    document = {'unit_id':canonical['unit_id'],'palette_slots':canonical['palette_slots'],
        'fixed_dt':scoped.get('fixed_dt',1/60),'bones':_physics_bones(canonical),
        'chains':copy.deepcopy(scoped['chains']),'colliders':copy.deepcopy(scoped['colliders']),
        'chain_links':copy.deepcopy(scoped.get('chain_links', []))}
    canonical_blob, canonical_summary = build_rig(canonical_rig)
    consumer_blob, consumer_summary = build_rig(consumer_rig)
    if scoped['chains']:
        physics_blob, physics_summary = physbone_build(document)
    elif pose_rules:
        physics_blob, physics_summary = b'', None
    else:
        raise SharedPhysicsError('共享封包既无物理链也无启用的衣物驱动')
    stem = 'shared_' + hashlib.sha256(identity).hexdigest()[:24]
    names = [stem+'.canonical.rigbin', stem+'.canonical.hd2phys', stem+'.consumers.rigbin']
    blobs = [canonical_blob, physics_blob, consumer_blob]
    if version==3:
        names.append(stem+'.canonical.hd2pose');blobs.append(compile_resource(scoped,canonical,pose_rules))
    refs = b''.join(REFERENCE.pack(_fixed(n,96),len(b),hashlib.sha256(b).digest()) if b else bytes(REFERENCE.size)
                    for n,b in zip(names,blobs))
    payload = refs + b''.join(entries)
    revision = hashlib.sha256(identity+struct.pack('<Q',canonical_id)+payload).digest()
    header = HEADER.pack(MAGIC,version,HEADER.size,HEADER.size+len(payload),zlib.crc32(payload),1,
                         (2|int(bool(physics_blob))) if version==3 else 0,
                         project_bytes,archive,canonical_id,revision,len(entries))
    files = {n:b for n,b in zip(names,blobs) if b}
    files[stem+'.hd2shared'] = header+payload
    # Legacy load_rig_file rejects this file's magic before any physics can be
    # activated. A shared-aware loader must deliberately recognize this guard.
    files[stem+f'.requires-shared-v{version}.rigbin'] = (b'HD2SHARED_REQUIRES_V3\0' if version==3 else GUARD_V2 if version==2 else GUARD)
    return {'files':files,'manifest':{'schema':f'HD2SharedBodyPhysics{version}Experimental',
        'solver_scope': 'shared_body_v2' if version >= 2 else 'shared_body_v1',
        # Export/signature/isolation callers must see the actual consumers,
        # never the synthetic canonical solver as a renderable Unit.
        'units':[{'part_slot':rows[u]['part_slot'], 'unit_id':f'{u:016x}',
                  'kind':rows[u]['kind'], 'physics_file':names[1] if physics_blob else None} for u in sorted(rows)],
        'rig':{'file':names[2], 'sha256':hashlib.sha256(consumer_blob).hexdigest(),
               'summary':consumer_summary},
        'runtime_requirements':[capability]+([POSE_CAPABILITY] if pose_rules else []), 'project_id':project_id,'archive':f'{archive:016x}',
        'revision':revision.hex(),'canonical_unit':f'{canonical_id:016x}',
        'chain_consumers':chain_consumers,'canonical_rig':canonical_summary,
        'consumer_rig':consumer_summary,'physics':physics_summary},
        'canonical_rig_document':canonical_rig,'consumer_rig_document':consumer_rig,
        'physics_document':document,'export_scope':scoped['export_scope']}
