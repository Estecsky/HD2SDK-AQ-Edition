"""Free-part v6 protocol. Requires a matching v6-capable game runtime.

One logical choice atomically controls all native appearance/body-type routes.
The isolation descriptor starts at each part's first (composite) option alias.
"""
from .runtime_manifest import (
    RuntimeManifestError, _archive_package, _stable_label, _safe_display_name, _hex64,
)


def build_free_runtime_manifest(plan, rig_profile_unit_ids=(), catalog=None):
    from .resource_isolation import load_binding_catalog
    parts = plan['difference_manifest'].get('free_parts', ())
    if not parts:
        return None
    catalog = catalog or load_binding_catalog()
    package = _archive_package(plan['archive_name'])
    target = catalog['targets'].get(package)
    if not target or target.get('domain') != plan['content_domain'] or not target.get('equipment'):
        raise RuntimeManifestError('自由差分缺少对应身体/头盔装备引用边界')
    project = plan['project_id']
    manifest_id = _stable_label('hd2free', project, package, digits=16)
    domain = 'helmet' if plan['content_domain'] == 'HELMET' else 'body'
    lines = ['manifest_version=6', f'manifest_id={manifest_id}',
             'difference_logic=free_parts', f'domain={domain}',
             f'archive=0x{package.upper()}']
    owned_sets = []
    for equipment in sorted(target['equipment'], key=lambda row: row['key']):
        lines.append(f"equipment=0x{int(equipment['key'], 16):08X}")
        owned_sets.append({int(value, 16) for value in equipment['unit_ids']})
    profiles = set(map(int, rig_profile_unit_ids))
    route_count, occupied, published_ids = 0, set(), set()
    for part in parts:
        slot = part['part_slot']
        part_id = _stable_label('fp', project, package, slot)
        options = part['options']
        if not options:
            raise RuntimeManifestError(f'{slot} 没有自由差分选项')
        default = {int(r['native_unit_id']): int(r['difference_unit_id']) for r in options[0]['routes']}
        if not default or occupied.intersection(default):
            raise RuntimeManifestError('自由差分部位路由为空或重复')
        if any(not set(default) <= owned for owned in owned_sets):
            raise RuntimeManifestError('自由差分路由超出装备引用边界')
        occupied.update(default)
        route_count += len(default)
        labels = [_stable_label('fo', project, package, slot, option['id']) for option in options]
        if len(set(labels)) != len(labels):
            raise RuntimeManifestError('自由差分选项稳定 ID 重复')
        lines.extend(['', f'free_part={part_id},{slot},{labels[0]}',
                      f"free_part_display_name={part_id},{_safe_display_name(part['name'], '差分部位名称')}"])
        for index, (option, label) in enumerate(zip(options, labels)):
            routes = option['routes']
            if option['order'] != index or len(routes) != len(default) or {int(r['native_unit_id']) for r in routes} != set(default):
                raise RuntimeManifestError('自由差分顺序或粗壮/纤细路由集合不一致')
            lines.extend([f'free_option={part_id},{label},{index}',
                          f"free_option_display_name={part_id},{label},{_safe_display_name(option['name'], '差分名称')}"])
            for route in sorted(routes, key=lambda row: int(row['native_unit_id'])):
                native, published = int(route['native_unit_id']), int(route['difference_unit_id'])
                if published in published_ids or published in occupied:
                    raise RuntimeManifestError('自由差分发布 Unit ID 冲突')
                published_ids.add(published)
                lines.append(f'free_route={part_id},{label},{_hex64(native)},{_hex64(default[native])},{_hex64(published)}')
                if published in profiles:
                    lines.append(f'free_rig_profile={part_id},{label},{_hex64(published)}')
    if route_count > 32:
        raise RuntimeManifestError('自由差分物理路由超过 32 个槽位')
    return dict(schema='HD2RuntimeDifferenceManifest6', manifest_id=manifest_id,
                archive_package=package, file_name=f'{manifest_id}.difference.ini',
                text='\n'.join(lines) + '\n', body_parts=route_count if domain == 'body' else 0,
                body_groups=0, free_parts=len(parts), requires_runtime='free-parts-v6')
