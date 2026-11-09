"""Snapshot relative shape keys on disposable independent-export objects.

Positions use ``P = P_export + sum((weight - baseline) * delta)``. Normals
use normalized linear deltas, sampled one channel at a time with the others
at their export baseline. This is an approximation between samples and for
combined channels, not a runtime geometry-normal recalculation.

POINT/CORNER attributes deliberately survive the SDK's UV/sharp-edge splits,
normal transfer, triangulation and joining. No evaluated dependency graph or
driver expression is executed here, and authoring objects are never changed.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct

import bpy
from mathutils import Vector

from . import morph_format


METADATA_PROP = "HD2SDK_MorphChannelsV1"
NORMAL_STRATEGY = "normalized_linear_delta"
POSITION_PREFIX = "hd2mp_"
NORMAL_PREFIX = "hd2mn_"
ORIGIN_ATTRIBUTES = tuple("hd2mo_identity_" + str(index) for index in range(4))
ORIGIN_VERTEX = "hd2mo_vertex"
ORIGIN_CORNER = "hd2mo_corner"


class MorphGeometryError(ValueError):
    pass


def require_export_enabled(objects, scene):
    """Keep the public release gate independent of saved Batch preferences."""
    settings = getattr(scene, "hd2bt_settings", None)
    if (morph_format.EXPORT_AVAILABLE
            and getattr(settings, "shape_key_export_mode", "DISABLED") == "ENABLED"):
        return
    names = sorted({obj.name for obj in objects if obj.type == 'MESH'
                    and obj.data.shape_keys is not None})
    if names:
        raise MorphGeometryError(
            morph_format.EXPORT_UNAVAILABLE + '，已取消保存/导出：' + '、'.join(names)
            + '。请先应用或移除形态键（包括 Basis）；源模型未修改'
        )


def _finite(value, label):
    value = float(value)
    if not math.isfinite(value):
        raise MorphGeometryError(f"{label} 包含非有限数值")
    return value


def _keys(obj):
    data = getattr(getattr(obj, "data", None), "shape_keys", None)
    return data.key_blocks if data else ()


def _has_driver(obj, key):
    animation = obj.data.shape_keys.animation_data
    path = key.path_from_id("value")
    return bool(animation and any(curve.data_path == path for curve in animation.drivers))


def _mask(obj, key):
    if not key.vertex_group:
        return None
    group = obj.vertex_groups.get(key.vertex_group)
    if group is None:
        raise MorphGeometryError(f"{obj.name} / {key.name} 的遮罩顶点组不存在：{key.vertex_group}")
    weights = [0.0] * len(obj.data.vertices)
    for vertex in obj.data.vertices:
        for assignment in vertex.groups:
            if assignment.group == group.index:
                weight = _finite(assignment.weight, f"{obj.name} / {key.name} 遮罩")
                if not 0.0 <= weight <= 1.0:
                    raise MorphGeometryError(f"{obj.name} / {key.name} 遮罩权重必须在 0 到 1 之间")
                weights[vertex.index] = weight
                break
    return weights


def validate_source(obj):
    """Read-only preflight; leave ordinary meshes on their existing SDK path."""
    keys = _keys(obj)
    if len(keys) < 2:
        return False
    if not obj.data.shape_keys.use_relative:
        raise MorphGeometryError(f"{obj.name} 使用绝对形态键；独立封包暂只支持相对形态键")
    unsupported = [modifier.name for modifier in obj.modifiers if modifier.type != 'ARMATURE']
    if unsupported:
        raise MorphGeometryError(f"{obj.name} 的形态键导出不支持非骨架修改器：" + "、".join(unsupported))
    if obj.show_only_shape_key:
        raise MorphGeometryError(f"{obj.name} 已启用仅显示当前形态键，请关闭后再保存以明确默认混合")
    count = len(obj.data.vertices)
    pointers = {key.as_pointer() for key in keys}
    for key in keys:
        if len(key.data) != count or key.relative_key.as_pointer() not in pointers:
            raise MorphGeometryError(f"{obj.name} / {key.name} 的形态键拓扑或相对键无效")
        for point in key.data:
            for component in point.co:
                _finite(component, f"{obj.name} / {key.name} 坐标")
        low = _finite(key.slider_min, f"{obj.name} / {key.name} 最小值")
        high = _finite(key.slider_max, f"{obj.name} / {key.name} 最大值")
        _finite(key.value, f"{obj.name} / {key.name} 默认值")
        if low > high:
            raise MorphGeometryError(f"{obj.name} / {key.name} 的形态键范围无效")
        _mask(obj, key)
    return True


def _channel_id(obj, key):
    return hashlib.sha256((obj.name + "\0" + key.name).encode("utf-8")).hexdigest()[:32]


def _source_deltas(source, geometry_keys):
    for source_key, key in zip(_keys(source)[1:], geometry_keys[1:]):
        mask = _mask(source, source_key)
        delta = []
        for index, (point, relative) in enumerate(zip(key.data, key.relative_key.data)):
            value = point.co - relative.co
            if mask is not None:
                value *= mask[index]
            delta.append(value)
        yield source_key, delta


def default_positions(source_obj):
    """Current relative-key mix in source local coordinates, before skinning."""
    keys = _keys(source_obj)
    if not keys:
        return [vertex.co.copy() for vertex in source_obj.data.vertices]
    if len(keys) == 1:
        return [point.co.copy() for point in keys[0].data]
    validate_source(source_obj)
    positions = [point.co.copy() for point in keys[0].data]
    for key, delta in _source_deltas(source_obj, keys):
        weight = 0.0 if key.mute else float(key.value)
        if weight:
            for position, displacement in zip(positions, delta):
                position += weight * displacement
    return positions


def _set_positions(mesh, positions):
    values = [_finite(component, '形态键混合坐标') for point in positions for component in point]
    mesh.vertices.foreach_set("co", values)
    mesh.update()


def _corner_normals(mesh):
    if hasattr(mesh, "calc_normals_split"):
        mesh.calc_normals_split()
    if hasattr(mesh, "corner_normals"):
        normals = [item.vector.copy() for item in mesh.corner_normals]
    else:
        normals = [loop.normal.copy() for loop in mesh.loops]
    for normal in normals:
        if not all(math.isfinite(component) for component in normal) or normal.length_squared < 1e-20:
            raise MorphGeometryError("形态键网格存在无效角点法线，请修复退化面后保存")
        normal.normalize()
    return normals


def _attribute(mesh, name, domain, values):
    if mesh.attributes.get(name) is not None:
        raise MorphGeometryError(f"网格存在与临时形态数据冲突的属性：{name}")
    attribute = mesh.attributes.new(name=name, type='FLOAT_VECTOR', domain=domain)
    attribute.data.foreach_set("vector", [component for vector in values for component in vector])


def _origin_attributes(source_obj, mesh):
    identity = struct.unpack('<4i', hashlib.sha256(source_obj.name.encode('utf-8')).digest()[:16])
    specifications = [(name, 'POINT', [value] * len(mesh.vertices))
                      for name, value in zip(ORIGIN_ATTRIBUTES, identity)]
    # One-based markers reserve zero for a non-morph object joined by Blender.
    specifications.extend(((ORIGIN_VERTEX, 'POINT', range(1, len(mesh.vertices) + 1)),
                           (ORIGIN_CORNER, 'CORNER', range(1, len(mesh.loops) + 1))))
    for name, domain, values in specifications:
        if mesh.attributes.get(name) is not None:
            raise MorphGeometryError(f"网格存在与临时形态来源冲突的属性：{name}")
        attribute = mesh.attributes.new(name=name, type='INT', domain=domain)
        attribute.data.foreach_set('value', values)


def _clear_temporary_shape_keys(obj):
    """Clear and free exactly the Key copied for this temporary mesh.

    Blender 4.5 mesh.copy() can leave a detached Key with users == 1 after
    shape_key_clear(). Its `user`/from pointer then dangles when the temporary
    mesh is deleted, preventing a subsequent .blend save. The ID user map is
    authoritative here; the numeric users count includes this stale count.
    Never purge other orphan keys owned by the user or another operation.
    """
    temporary_key = obj.data.shape_keys
    pointer = temporary_key.as_pointer()
    temporary_key.animation_data_clear()
    obj.shape_key_clear()
    # Some Blender versions free the Key while clearing it. Find the exact
    # surviving ID by pointer instead of dereferencing a possibly freed RNA.
    detached = next((key for key in bpy.data.shape_keys if key.as_pointer() == pointer), None)
    if detached is None:
        return
    if obj.data.shape_keys is not None or bpy.data.user_map(subset={detached}).get(detached):
        raise MorphGeometryError("临时形态键仍被使用，无法安全清理导出副本")
    bpy.data.batch_remove(ids=(detached,))


def prepare_export_copy(source_obj, transformed_copy):
    """Bake a copy already transformed with ``shape_keys=True``; return channels.

    The caller owns/removes the copy. Every mesh/object/Key write here is to
    that copy; temporary normal-sampling meshes are always released.
    """
    keys = _keys(source_obj)
    if not keys:
        return None
    if (source_obj == transformed_copy or source_obj.data == transformed_copy.data
            or source_obj.data.shape_keys == transformed_copy.data.shape_keys):
        raise MorphGeometryError("形态键导出必须使用完全独立的临时网格副本")
    enabled = validate_source(source_obj)
    copy_keys = _keys(transformed_copy)
    if len(keys) != len(copy_keys) or any(a.name != b.name for a, b in zip(keys, copy_keys)):
        raise MorphGeometryError("临时副本的形态键与作者网格不一致")
    positions = [point.co.copy() for point in copy_keys[0].data]
    channels = []
    displacements = []
    if enabled:
        for key, delta in _source_deltas(source_obj, copy_keys):
            baseline = 0.0 if key.mute else float(key.value)
            sample = 1.0 if baseline <= 0.5 else 0.0
            channels.append(dict(
                id=_channel_id(source_obj, key), name=key.name,
                source_object=source_obj.name, relative_key=key.relative_key.name,
                min=float(key.slider_min), max=float(key.slider_max),
                default=baseline, baseline=baseline, normal_sample_weight=sample,
                has_driver=_has_driver(source_obj, key), muted=bool(key.mute),
            ))
            displacements.append(delta)
            if baseline:
                for position, displacement in zip(positions, delta):
                    position += baseline * displacement
    # Clearing copy animation prevents modifiers/normal transfer from evaluating
    # a copied driver. The source Key datablock and its animation remain intact.
    transformed_copy.animation_data_clear()
    _clear_temporary_shape_keys(transformed_copy)
    _set_positions(transformed_copy.data, positions)
    if not channels:
        return None
    _origin_attributes(source_obj, transformed_copy.data)
    base_normals = _corner_normals(transformed_copy.data)
    scratch = transformed_copy.data.copy()
    try:
        # Zero custom normals request Blender's geometric automatic normals.
        # Work on original corners, before UV seams become disconnected edges.
        scratch.normals_split_custom_set([(0.0, 0.0, 0.0)] * len(scratch.loops))
        scratch.update()
        automatic_base = _corner_normals(scratch)
        for channel, delta in zip(channels, displacements):
            span = channel['normal_sample_weight'] - channel['baseline']
            _set_positions(scratch, [point + span * displacement for point, displacement in zip(positions, delta)])
            automatic_sample = _corner_normals(scratch)
            normal_delta = []
            for base, auto_base, auto_sample in zip(base_normals, automatic_base, automatic_sample):
                sample_normal = base + auto_sample - auto_base
                if sample_normal.length_squared < 1e-20:
                    raise MorphGeometryError(f"{source_obj.name} / {channel['name']} 的法线采样退化，不能安全导出")
                sample_normal.normalize()
                normal_delta.append((sample_normal - base) / span)
            _attribute(transformed_copy.data, POSITION_PREFIX + channel['id'], 'POINT', delta)
            _attribute(transformed_copy.data, NORMAL_PREFIX + channel['id'], 'CORNER', normal_delta)
    finally:
        bpy.data.meshes.remove(scratch)
    if hasattr(transformed_copy.data, 'use_auto_smooth'):
        # Blender 4.0's DATA_TRANSFER refuses loop normals when Auto Smooth is
        # disabled. Freeze the previously sampled baseline before enabling it,
        # so UV splitting cannot silently turn a smooth surface into hard faces.
        transformed_copy.data.use_auto_smooth = True
        transformed_copy.data.normals_split_custom_set(base_normals)
    transformed_copy[METADATA_PROP] = json.dumps(channels, ensure_ascii=False, separators=(',', ':'))
    return channels


def _metadata(obj):
    value = obj.get(METADATA_PROP)
    if value is None:
        return []
    try:
        channels = json.loads(value)
    except (TypeError, ValueError) as error:
        raise MorphGeometryError("临时形态键元数据损坏") from error
    if not isinstance(channels, list):
        raise MorphGeometryError("临时形态键元数据必须为通道列表")
    return channels


def merge_metadata(objects):
    """Merge onto the first disposable object immediately before Blender join."""
    objects = list(objects)
    channels = {}
    for obj in objects:
        for channel in _metadata(obj):
            old = channels.get(channel['id'])
            if old is not None and old != channel:
                raise MorphGeometryError("合并网格的同一形态通道定义冲突")
            channels[channel['id']] = channel
    result = sorted(channels.values(), key=lambda item: item['id'])
    if objects and result:
        objects[0][METADATA_PROP] = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
    return result


def capture_prepared(prepared_obj):
    """Capture final split vertex indices, matching GetMeshData's last loop rule."""
    channels = _metadata(prepared_obj)
    if not channels:
        return None
    mesh = prepared_obj.data
    digest = hashlib.sha256(b'HD2SDK morph prepared mapping v1\0')
    for name in (*ORIGIN_ATTRIBUTES, ORIGIN_VERTEX, ORIGIN_CORNER):
        attribute = mesh.attributes.get(name)
        expected_domain = 'CORNER' if name == ORIGIN_CORNER else 'POINT'
        if attribute is None or attribute.domain != expected_domain or attribute.data_type != 'INT':
            raise MorphGeometryError("形态键来源标记在网格准备过程中丢失")
        digest.update(struct.pack('<I', len(attribute.data)))
        for item in attribute.data:
            digest.update(struct.pack('<i', item.value))
    for polygon in mesh.polygons:
        digest.update(struct.pack('<I', len(polygon.loop_indices)))
        for loop_index in polygon.loop_indices:
            digest.update(struct.pack('<I', mesh.loops[loop_index].vertex_index))
    deltas = []
    for channel in channels:
        position = mesh.attributes.get(POSITION_PREFIX + channel['id'])
        normal = mesh.attributes.get(NORMAL_PREFIX + channel['id'])
        if (position is None or normal is None or position.domain != 'POINT'
                or normal.domain != 'CORNER' or position.data_type != 'FLOAT_VECTOR'
                or normal.data_type != 'FLOAT_VECTOR'):
            raise MorphGeometryError(f"{channel['name']} 的形态增量在网格准备过程中丢失")
        vertex_normals = [Vector((0.0, 0.0, 0.0)) for _ in mesh.vertices]
        for loop in mesh.loops:
            vertex_normals[loop.vertex_index] = normal.data[loop.index].vector.copy()
        records = []
        for vertex in mesh.vertices:
            record = [*position.data[vertex.index].vector, *vertex_normals[vertex.index]]
            if not all(math.isfinite(value) for value in record):
                raise MorphGeometryError(f"{channel['name']} 的最终形态增量包含非有限数值")
            if any(value != 0.0 for value in record):
                records.append([vertex.index, *record])
        deltas.append(dict(channel_id=channel['id'], records=records))
    baseline_normals = [list(vertex.normal.normalized()) for vertex in mesh.vertices]
    corner_normals = _corner_normals(mesh)
    for loop, normal in zip(mesh.loops, corner_normals):
        baseline_normals[loop.vertex_index] = list(normal)
    # Match GetMeshData: polygons retain their order inside material-index
    # buckets, then the buckets are flattened in material-slot order.
    buckets = [[] for _ in range(max(len(prepared_obj.material_slots),
                                    1 + max((p.material_index for p in mesh.polygons), default=0)))]
    for polygon in mesh.polygons:
        buckets[polygon.material_index].append(list(polygon.vertices[:3]))
    return dict(channels=channels, deltas=deltas, mapping_sha256=digest.hexdigest(),
                baseline_positions=[list(vertex.co) for vertex in mesh.vertices],
                baseline_normals=baseline_normals,
                indices=[triangle for bucket in buckets for triangle in bucket])


def source_revision(objects):
    """Hash morph semantics and source geometry for safe physics-only updates."""
    digest = hashlib.sha256(b"HD2SDK morph source revision v1\0")

    def text(value):
        raw = str(value).encode('utf-8')
        digest.update(struct.pack('<I', len(raw)))
        digest.update(raw)

    def numbers(values):
        values = tuple(values)
        digest.update(struct.pack('<I', len(values)))
        digest.update(struct.pack('<' + 'd' * len(values), *values))

    for obj in sorted(objects, key=lambda item: item.name):
        keys = _keys(obj)
        if not keys:
            continue
        validate_source(obj)
        text(obj.name)
        numbers((len(keys), bool(obj.data.shape_keys.use_relative), bool(obj.show_only_shape_key)))
        numbers((bool(getattr(obj.data, 'use_auto_smooth', False)),
                 float(getattr(obj.data, 'auto_smooth_angle', 0.0))))
        for key in keys:
            text(key.name)
            text(key.relative_key.name)
            text(key.vertex_group)
            numbers((key.slider_min, key.slider_max, key.value, bool(key.mute), _has_driver(obj, key)))
            for point in key.data:
                numbers(point.co)
            mask = _mask(obj, key)
            if mask is not None:
                numbers(mask)
        # Connectivity, smoothing and authored normals affect the exported
        # normal deltas even if key coordinates have not changed.
        for polygon in obj.data.polygons:
            numbers((*polygon.vertices, bool(polygon.use_smooth), polygon.material_index))
        for edge in obj.data.edges:
            numbers((*edge.vertices, bool(edge.use_edge_sharp), bool(edge.use_seam)))
        numbers((len(obj.data.uv_layers), obj.data.uv_layers.active_index))
        for layer in obj.data.uv_layers:
            text(layer.name)
            numbers((bool(layer.active_render),))
            for item in layer.data:
                numbers(item.uv)
        numbers((len(obj.material_slots),))
        for slot in obj.material_slots:
            text(slot.name)
            text(slot.link)
        for normal in _corner_normals(obj.data):
            numbers(normal)
    return digest.hexdigest()
