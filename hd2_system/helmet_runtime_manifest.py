"""Adapt GROUP helmet replacements to the existing exclusive v6 selector.

The base remains the default published Unit, as declared by the isolation
descriptor. This is only a runtime view: it must never change authoring metadata,
save jobs, or combine a GROUP difference mesh with the base mesh.
"""

from .free_runtime_manifest import build_free_runtime_manifest
from .runtime_manifest import RuntimeManifestError, _hex64, _safe_display_name


def build_helmet_runtime_manifest(plan, rig_profile_unit_ids=(), catalog=None):
    authored = plan['difference_manifest']
    helmets = authored.get('helmet_differences', ())
    if not helmets:
        return None
    if len(helmets) + 1 > 32:
        raise RuntimeManifestError('头盔差分选项包含基础默认项最多 32 项')
    if (plan.get('content_domain') != 'HELMET'
            or authored.get('body_groups') or authored.get('free_parts')
            or authored.get('difference_logic', 'GROUP') != 'GROUP'):
        raise RuntimeManifestError('头盔组差分必须独立导出，不能混合身体或自由差分')
    if (plan.get('resource_target_policy') != 'PROJECT_ARCHIVE_ALIASES'
            or authored.get('helmet_policy') != 'SINGLE_SELECTION_WITH_DEFAULT'):
        raise RuntimeManifestError('头盔组差分缺少独立基础默认策略，请重新保存')

    base = authored.get('base_group')
    if not isinstance(base, dict):
        raise RuntimeManifestError('头盔差分清单缺少基础组')
    base_name = _safe_display_name(base.get('name'), '基础组名称')
    base_routes, native_ids = [], set()
    for row in plan.get('native_saves', ()):
        if row.get('part_slot') != 'Head':
            raise RuntimeManifestError('头盔基础组不能包含身体部位')
        try:
            native = int(row['native_unit_id'])
            published = int(row['base_unit_id'])
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeManifestError('头盔基础组缺少有效独立 Unit ID') from error
        _hex64(native)
        _hex64(published)
        if native in native_ids:
            raise RuntimeManifestError('头盔基础组目标重复')
        native_ids.add(native)
        base_routes.append(dict(native_unit_id=str(native), difference_unit_id=str(published)))
    if not base_routes:
        raise RuntimeManifestError('头盔组差分缺少基础默认路由')

    options = [dict(id='GROUP_BASE', name=base_name, order=0, routes=base_routes)]
    names = set()
    for helmet in sorted(helmets, key=lambda row: str(row.get('name', '')).casefold()):
        if helmet.get('part_slot') != 'Head':
            raise RuntimeManifestError('头盔差分只能引用 Head 部位')
        name = _safe_display_name(helmet.get('name'), '头盔差分名称')
        if name.casefold() in names:
            raise RuntimeManifestError('头盔差分名称重复')
        names.add(name.casefold())
        options.append(dict(id='HELMET_SINGLE:' + name, name=name,
                            order=len(options), routes=helmet.get('routes', ())))

    # Pass a separate view to the shared v6 validator/writer. Its first option
    # anchors every route at the existing base alias, preserving GROUP defaults.
    runtime_plan = dict(plan, difference_manifest=dict(authored, free_parts=[
        dict(part_slot='Head', name='头盔', options=options),
    ]))
    return build_free_runtime_manifest(runtime_plan, rig_profile_unit_ids, catalog)
