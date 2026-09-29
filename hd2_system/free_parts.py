"""Free part selection: each slot owns an ordered, exclusive choice list.

Each published choice includes the same-slot base mesh on a disposable save
copy. This keeps the base visible without exponential cross-slot combinations.
"""
from collections import defaultdict
import re

from .independent_packaging import (
    IndependentPackagingError, PART_SLOTS, PLAN_SCHEMA, MANIFEST_SCHEMA,
    _clean_name, _active_targets, _custom_unit_id,
)


def build_free_plan(project_name, archive_name, active_unit_ids, objects, catalog, project_id):
    bases, groups, names, ids, orders = {}, defaultdict(list), {}, set(), set()
    domains, base_names = set(), set()
    for object_name, data in objects:
        slot = str(data.get('HD2BT_PartSlot', ''))
        if slot not in PART_SLOTS:
            raise IndependentPackagingError(f'{object_name} 没有有效语义部位')
        domains.add('HELMET' if slot == 'Head' else 'BODY')
        if data.get('HD2BT_DifferenceLogic', 'GROUP') != 'FREE':
            raise IndependentPackagingError('同一保存域不能混合组差分与自由搭配；请重新标记对应网格')
        kind = data.get('HD2BT_VariantKind', 'BASE')
        if kind == 'BASE':
            if slot in bases:
                raise IndependentPackagingError(f'{slot} 有多个基础对象')
            bases[slot] = str(object_name)
            base_names.add(_clean_name(data.get('HD2BT_BaseGroup') or '基础组', '基础组名称'))
            continue
        if kind != 'FREE_PART':
            raise IndependentPackagingError(f'{object_name} 的自由搭配类型无效')
        group = _clean_name(data.get('HD2BT_DifferenceGroup'), '差分部位名称')
        name = _clean_name(data.get('HD2BT_DifferenceName'), '差分名称')
        identity = str(data.get('HD2BT_DifferenceId', ''))
        order = data.get('HD2BT_DifferenceOrder')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', identity):
            raise IndependentPackagingError(f'{object_name} 缺少有效稳定差分 ID，请重新标记')
        if type(order) is not int or order < 0:
            raise IndependentPackagingError(f'{object_name} 的差分列表顺序无效')
        if slot in names and names[slot] != group:
            raise IndependentPackagingError(f'{slot} 的差分部位名称不一致，请在 Batch 中统一修改')
        if identity in ids or (slot, order) in orders:
            raise IndependentPackagingError(f'{slot} 的差分 ID/顺序重复；每项只能标记一个网格')
        if any(row['name'].casefold() == name.casefold() for row in groups[slot]):
            raise IndependentPackagingError(f'{slot} 的差分名称重复：{name}')
        names[slot] = group
        ids.add(identity)
        orders.add((slot, order))
        groups[slot].append(dict(id=identity, name=name, order=order,
                                 part_slot=slot, object_names=[str(object_name)]))
    if len(domains) != 1:
        raise IndependentPackagingError('请分别存储头和身体')
    domain = next(iter(domains))
    if len({name.casefold() for name in base_names}) > 1:
        raise IndependentPackagingError('一个保存域只能有一个具名基础组')
    generated, native, free_parts = set(), [], []

    def alias(kind, identity, slot, target):
        result = _custom_unit_id(project_id, archive_name, kind, identity, slot, target)
        if result in active_unit_ids or result in generated:
            raise IndependentPackagingError('自由搭配独立 Unit ID 冲突')
        generated.add(result)
        return result

    for slot in PART_SLOTS:
        if slot not in bases and slot not in groups:
            continue
        targets = _active_targets(catalog, active_unit_ids, slot)
        if not targets:
            raise IndependentPackagingError(f'当前 Archive 中找不到 {slot} 对应 Unit')
        if slot in bases:
            for target in targets:
                native.append(dict(part_slot=slot, native_unit_id=target,
                    base_unit_id=alias('BASE', '', slot, target), object_names=[bases[slot]]))
        if slot not in groups:
            continue
        options = sorted(groups[slot], key=lambda row: row['order'])
        if [row['order'] for row in options] != list(range(len(options))):
            raise IndependentPackagingError(f'{slot} 列表首项或中间项尚未标记，请逐项指定网格')
        for option in options:
            option['base_object_name'] = bases.get(slot, '')
            option['routes'] = [dict(native_unit_id=target,
                difference_unit_id=alias('FREE_PART', option['id'], slot, target)) for target in targets]
        free_parts.append(dict(part_slot=slot, name=names[slot], options=options))
    base_name = sorted(base_names)[0] if base_names else '基础部位'
    manifest = dict(schema=MANIFEST_SCHEMA, project_name=project_name,
        project_id=project_id, archive_name=archive_name, difference_logic='FREE',
        base_group=dict(name=base_name, members=[{k:v for k,v in row.items() if k != 'object_names'} for row in native]),
        body_groups=[], helmet_differences=[], free_parts=free_parts,
        body_group_policy='FREE_PART_FIRST_OPTION', helmet_policy='FREE_PART_FIRST_OPTION')
    return dict(schema=PLAN_SCHEMA, project_name=project_name, project_id=project_id,
        archive_name=archive_name, content_domain=domain,
        resource_target_policy='PROJECT_ARCHIVE_ALIASES', native_saves=native,
        difference_manifest=manifest)


def default_routes(plan):
    """Stock descriptor rows resolve to each free list's first composite Unit."""
    routes = {int(row['native_unit_id']): int(row['base_unit_id']) for row in plan['native_saves']}
    for part in plan['difference_manifest'].get('free_parts', ()):
        for route in part['options'][0]['routes']:
            routes[int(route['native_unit_id'])] = int(route['difference_unit_id'])
    return sorted(routes.items())


def validate_scene_registry(plan, groups):
    """Cross-check optional Batch RNA without importing another Blender addon."""
    if plan['difference_manifest'].get('difference_logic') != 'FREE':
        return
    authored = {part['part_slot']: part for part in plan['difference_manifest']['free_parts']}
    for group in groups:
        domain = 'HELMET' if group.part_slot == 'Head' else 'BODY'
        if domain != plan['content_domain'] or not group.options:
            continue
        part = authored.get(group.part_slot)
        if (part is None or part['name'] != group.display_name
                or len(part['options']) != len(group.options)):
            raise IndependentPackagingError(f'{group.display_name} 的列表尚未完整标记，请标记或删除空项后封包')
        for index, option in enumerate(group.options):
            row = part['options'][index]
            if (row['id'], row['name'], row['order']) != (option.identity, option.display_name, index):
                raise IndependentPackagingError(f'{group.display_name} / {option.display_name} 的网格标记与列表不同步')
