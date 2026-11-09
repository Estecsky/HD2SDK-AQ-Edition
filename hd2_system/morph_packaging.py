"""Bind prepared morph captures to the exact serialized Unit GPU buffers.

No Blender imports or game I/O occur here. ``build_document`` accepts tuples
``(unit_id, loaded_unit, main_bytes, stream_bytes, gpu_bytes, captures)``.
``captures`` maps FINAL MeshInfoArray indices to capture_prepared results.
The Unit may be a cold TOC-only decode; RawMeshes are optional cross-checks.
Offsets in the resulting document are relative to that Unit's GPU blob, never
the containing patch GPU file. All source bytes must be final serialized bytes.
"""

from __future__ import annotations

import copy
import hashlib
import math
import re
import struct

from . import morph_format


class MorphPackagingError(ValueError):
    """An exported Unit and its prepared capture cannot be safely associated."""


_F32 = struct.Struct("<f")
_POSITION = struct.Struct("<3f")
_OLD_UNIT_VERSIONS = {1, 10800436, 10800437}
_FORMAT_SIZES = {0: 4, 1: 8, 2: 12, 3: 16, 4: 4,
                 21: 4, 22: 8, 23: 12, 24: 16,
                 25: 1, 26: 2, 27: 3, 28: 4, 29: 4, 30: 4,
                 32: 2, 33: 4, 34: 6, 35: 8}
# SDK encode_packed_oct_norm floors each 10-bit coordinate. In octahedral
# space each coordinate therefore moves by < 2/1023. On every unfolded/folded
# triangle the decode Jacobian has operator norm sqrt(3), and its unnormalized
# vector has length >= 1/sqrt(3). Normalized decode is consequently globally
# 3-Lipschitz, including paths crossing triangle boundaries. Its directional
# distance error is bounded by 3 * sqrt(2) * 2/1023, not 0.008 (which rejects
# valid samples near normalize(1, 1, 1)). Reserve eight float32 epsilons for
# Blender's float32 normalization before the SDK's Python oct encoding.
OCT10_NORMAL_ERROR_BOUND = 6.0 * math.sqrt(2.0) / 1023.0
NORMAL_FLOAT32_ALLOWANCE = 8.0 * (2.0 ** -23)
NORMAL_TOLERANCE = OCT10_NORMAL_ERROR_BOUND + NORMAL_FLOAT32_ALLOWANCE


def _fail(message):
    raise MorphPackagingError(message)


def _integer(value, label, minimum=0, maximum=0xFFFFFFFF):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(f"{label} 必须是 [{minimum}, {maximum}] 内的整数")
    return value


def _field(obj, name):
    try:
        return getattr(obj, name)
    except AttributeError as error:
        raise MorphPackagingError(f"最终 Unit 缺少 {name}") from error


def _bytes(value, label):
    if not isinstance(value, (bytes, bytearray, memoryview)):
        _fail(f"{label} 必须是实际序列化字节")
    return bytes(value)


def _finite_vector(value, label):
    try:
        result = tuple(value)
    except TypeError as error:
        raise MorphPackagingError(f"{label} 必须包含三个有限坐标") from error
    if len(result) != 3 or any(type(v) not in (float, int) or not math.isfinite(v) for v in result):
        _fail(f"{label} 必须包含三个有限坐标")
    return result


def _position_bytes(value, label):
    try:
        return _POSITION.pack(*_finite_vector(value, label))
    except (OverflowError, struct.error) as error:
        raise MorphPackagingError(f"{label} 超出 float32 范围") from error


def _normal(value, label):
    vector = _finite_vector(value, label)
    length = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(length) or length < 1e-20:
        _fail(f"{label} 法线退化")
    return tuple(v / length for v in vector)


def _decode_normal(packed):
    x = (packed & 1023) * (2.0 / 1023.0) - 1.0
    y = ((packed >> 10) & 1023) * (2.0 / 1023.0) - 1.0
    z = 1.0 - abs(x) - abs(y)
    if z < 0:
        x, y = ((1.0 - abs(y)) * (1 if x >= 0 else -1),
                (1.0 - abs(x)) * (1 if y >= 0 else -1))
    return _normal((x, y, z), "GPU packed normal")


def _slice(blob, offset, size, label):
    _integer(offset, label + " offset")
    _integer(size, label + " size")
    if offset + size > len(blob):
        _fail(f"{label} 超出最终 GPU 字节范围")
    return blob[offset:offset + size]


def _unit_fingerprint(main, stream, gpu):
    # Exact resource_isolation.payload_fingerprint/materials.entry_payload order.
    digest = hashlib.sha256(struct.pack("<3Q", len(main), len(gpu), len(stream)))
    for part in (main, gpu, stream):
        digest.update(part)
    return digest.hexdigest()


def _layout(unit, stream):
    version = _integer(_field(unit, "Version"), "Unit.Version")
    if version in _OLD_UNIT_VERSIONS:
        _fail("形态数据暂不支持使用旧顶点格式编号的 Unit")
    stride = _integer(_field(stream, "VertexStride"), "VertexStride", 1, 4096)
    components, cursor = [], 0
    for component in _field(stream, "Components"):
        kind = _integer(_field(component, "Type"), "Component.Type", 0, 7)
        form = _integer(_field(component, "Format"), "Component.Format")
        index = _integer(_field(component, "Index"), "Component.Index")
        if form not in _FORMAT_SIZES:
            _fail(f"不支持的顶点组件格式：Type={kind}, Format={form}")
        size = _FORMAT_SIZES[form]
        components.append(dict(type=kind, format=form, index=index, offset=cursor, size=size))
        cursor += size
    if cursor > stride:
        _fail("顶点组件总长度超过最终 VertexStride")
    positions = [item for item in components if item["type"] == 0]
    normals = [item for item in components if item["type"] == 1]
    if (len(positions) != 1 or positions[0]["format"] != 2 or positions[0]["index"] != 0
            or len(normals) != 1 or normals[0]["format"] != 30 or normals[0]["index"] != 0):
        _fail("形态数据仅支持 Type0/Format2 float3 位置和 Type1/Format30 packed_oct_10_10 法线")
    return components, positions[0], normals[0]


def _raw_meshes(unit, captured_indices):
    result = {}
    mapping = getattr(unit, "DEV_MeshInfoMap", None)
    for raw in getattr(unit, "RawMeshes", ()):
        index = _integer(_field(raw, "MeshInfoIndex"), "RawMesh.MeshInfoIndex")
        if mapping:
            if index >= len(mapping):
                _fail("RawMesh 的 DEV_MeshInfoMap 索引越界")
            index = _integer(mapping[index], "DEV_MeshInfoMap")
        if index in captured_indices:
            if index in result:
                _fail("多个 RawMesh 指向同一形态网格")
            result[index] = raw
    return result


def _flat_indices(faces, vertex_count, label):
    if not isinstance(faces, (list, tuple)):
        _fail(f"{label} 必须是三角形列表")
    result = []
    for face in faces:
        if not isinstance(face, (list, tuple)) or len(face) != 3:
            _fail(f"{label} 包含非三角形")
        result.extend(_integer(v, label, 0, vertex_count - 1) for v in face)
    return result


def _transform_metadata(unit, info):
    index = _integer(_field(info, "TransformIndex"), "TransformIndex")
    result = dict(transform_index=index)
    transform_info = getattr(unit, "TransformInfo", None)
    matrices = getattr(transform_info, "TransformMatrices", None)
    if matrices is None:
        return result
    if index >= len(matrices):
        _fail("形态网格 TransformIndex 超出最终 Unit 矩阵表")
    values = list(_field(matrices[index], "v"))
    if len(values) != 16 or any(type(v) not in (float, int) or not math.isfinite(v) for v in values):
        _fail("形态网格的 mesh_to_unit 矩阵无效")
    result.update(mesh_to_unit=[values[column * 4 + row] for row in range(4) for column in range(4)],
                  matrix_layout="row_major_4x4")
    return result


def _bounds(positions, deltas, channels):
    lower = [list(position) for position in positions]
    upper = [list(position) for position in positions]
    for delta in deltas:
        channel = channels[delta["channel_id"]]
        baseline = channel["baseline"]
        # Include the initial baked mix, even for a muted zero outside sliders.
        lo = min(channel["min"], baseline) - baseline
        hi = max(channel["max"], baseline) - baseline
        for record in delta["records"]:
            vertex = record[0]
            for axis, displacement in enumerate(record[1:4]):
                a, b = lo * displacement, hi * displacement
                lower[vertex][axis] = math.nextafter(lower[vertex][axis] + min(a, b), -math.inf)
                upper[vertex][axis] = math.nextafter(upper[vertex][axis] + max(a, b), math.inf)
    return ([min(point[axis] for point in lower) for axis in range(3)],
            [max(point[axis] for point in upper) for axis in range(3)])


def _mesh_document(unit_id, unit, gpu, mesh_index, capture, raw, unit_hash, channels):
    infos = _field(unit, "MeshInfoArray")
    streams = _field(unit, "StreamInfoArray")
    if mesh_index >= len(infos):
        _fail(f"Unit {unit_id:016x} 的形态 MeshInfoIndex 越界")
    info = infos[mesh_index]
    stream_index = _integer(_field(info, "StreamIndex"), "StreamIndex")
    if stream_index >= len(streams):
        _fail("形态网格 StreamIndex 超出最终 Unit")
    stream = streams[stream_index]
    components, position_component, normal_component = _layout(unit, stream)
    stride = stream.VertexStride
    stream_vertices = _integer(_field(stream, "NumVertices"), "NumVertices", 1)
    vb_offset = _integer(_field(stream, "VertexBufferOffset"), "VertexBufferOffset")
    vb_size = _integer(_field(stream, "VertexBufferSize"), "VertexBufferSize")
    vertex_bytes = _slice(gpu, vb_offset, vb_size, "VertexBuffer")
    if stream_vertices * stride > vb_size:
        _fail("VertexBufferSize 不足以容纳最终顶点流")
    index_type = _integer(_field(stream, "IndexBuffer_Type"), "IndexBuffer_Type", 0, 1)
    index_stride = 4 if index_type == 1 else 2
    ib_offset = _integer(_field(stream, "IndexBufferOffset"), "IndexBufferOffset")
    ib_size = _integer(_field(stream, "IndexBufferSize"), "IndexBufferSize")
    stream_indices = _integer(_field(stream, "NumIndices"), "NumIndices")
    _slice(gpu, ib_offset, ib_size, "IndexBuffer")
    if stream_indices * index_stride > ib_size:
        _fail("IndexBufferSize 不足以容纳最终索引流")
    if vb_offset < ib_offset + ib_size and ib_offset < vb_offset + vb_size:
        _fail("最终顶点缓冲与索引缓冲重叠")
    sections = _field(info, "Sections")
    if not sections:
        _fail("形态网格没有最终 Section")
    vertex_offset = _integer(_field(sections[0], "VertexOffset"), "Section.VertexOffset")
    vertex_count = _integer(_field(sections[0], "NumVertices"), "Section.NumVertices", 1)
    if vertex_offset + vertex_count > stream_vertices:
        _fail("形态 Section 的顶点范围超过最终顶点流")
    positions = capture.get("baseline_positions")
    normals = capture.get("baseline_normals")
    if (not isinstance(positions, list) or not isinstance(normals, list)
            or len(positions) != vertex_count or len(normals) != vertex_count):
        _fail("形态捕获的基准位置/法线数量与最终 Section 顶点数不一致")
    actual_positions = []
    for vertex in range(vertex_count):
        offset = vb_offset + (vertex_offset + vertex) * stride
        position_offset = offset + position_component["offset"]
        actual_position = gpu[position_offset:position_offset + 12]
        if actual_position != _position_bytes(positions[vertex], "捕获基准位置"):
            _fail(f"Unit {unit_id:016x} Mesh {mesh_index} 顶点 {vertex} 的形态基准与最终 GPU 不一致")
        actual_positions.append(_POSITION.unpack(actual_position))
        packed = struct.unpack_from("<I", gpu, offset + normal_component["offset"])[0]
        actual_normal = _decode_normal(packed)
        expected_normal = _normal(normals[vertex], "捕获基准法线")
        normal_distance = math.dist(actual_normal, expected_normal)
        if normal_distance > NORMAL_TOLERANCE:
            _fail(f"Unit {unit_id:016x} Mesh {mesh_index} 顶点 {vertex} 的基准法线与最终 GPU 不一致："
                  f"actual={actual_normal!r}, expected={expected_normal!r}, "
                  f"distance={normal_distance:.12g}, limit={NORMAL_TOLERANCE:.12g}, packed=0x{packed:08x}")
        if raw is not None:
            if len(raw.VertexPositions) != vertex_count or len(raw.VertexNormals) != vertex_count:
                _fail("RawMesh 的顶点数量与最终 Section 不一致")
            if _position_bytes(raw.VertexPositions[vertex], "RawMesh位置") != actual_position:
                _fail("RawMesh 位置与最终 GPU 不一致")
            raw_normal = _normal(raw.VertexNormals[vertex], "RawMesh法线")
            raw_distance = math.dist(raw_normal, actual_normal)
            if raw_distance > NORMAL_TOLERANCE:
                _fail(f"RawMesh 法线与最终 GPU 不一致：Unit {unit_id:016x} Mesh {mesh_index} 顶点 {vertex}, "
                      f"actual={actual_normal!r}, expected={raw_normal!r}, "
                      f"distance={raw_distance:.12g}, limit={NORMAL_TOLERANCE:.12g}, packed=0x{packed:08x}")
    captured_indices = _flat_indices(capture.get("indices"), vertex_count, "形态捕获索引")
    actual_indices, section_metadata, index_parts, occupied = [], [], [], []
    for section_index, section in enumerate(sections):
        if section.VertexOffset != vertex_offset or section.NumVertices != vertex_count:
            _fail("同一形态网格的 Section 顶点布局不一致")
        index_offset = _integer(_field(section, "IndexOffset"), "Section.IndexOffset")
        count = _integer(_field(section, "NumIndices"), "Section.NumIndices")
        if count % 3 or index_offset + count > stream_indices:
            _fail("Section 索引不是完整三角形或超过最终索引流")
        end = index_offset + count
        if count and any(index_offset < b and end > a for a, b in occupied):
            _fail("形态网格的 Section 索引范围重叠")
        occupied.append((index_offset, end))
        byte_offset = ib_offset + index_offset * index_stride
        part = _slice(gpu, byte_offset, count * index_stride, "Section indices")
        index_parts.append(part)
        actual_indices.extend(value[0] for value in struct.iter_unpack("<I" if index_stride == 4 else "<H", part))
        section_metadata.append(dict(section_index=section_index,
                                     material_index=_integer(_field(section, "MaterialIndex"), "MaterialIndex"),
                                     section_id=_integer(_field(section, "ID"), "Section.ID"),
                                     group_index=_integer(_field(section, "GroupIndex"), "GroupIndex"),
                                     vertex_offset=vertex_offset, vertex_count=vertex_count,
                                     index_offset=index_offset, index_count=count, index_byte_offset=byte_offset))
    if actual_indices != captured_indices:
        _fail("形态捕获的材质排序索引与最终 GPU Section 索引不一致")
    if raw is not None and _flat_indices(raw.Indices, vertex_count, "RawMesh索引") != actual_indices:
        _fail("RawMesh 索引与最终 GPU 不一致")
    deltas = copy.deepcopy(capture.get("deltas"))
    if not isinstance(deltas, list):
        _fail("形态捕获缺少增量数组")
    # Bounds must enclose the float32 values the runtime will actually read.
    for delta in deltas:
        if not isinstance(delta, dict) or delta.get("channel_id") not in channels:
            _fail("形态增量引用了不存在的通道")
        records = delta.get("records")
        if not isinstance(records, list):
            _fail("形态增量 records 无效")
        quantized = []
        for record in records:
            if not isinstance(record, (list, tuple)) or len(record) != 7:
                _fail("形态增量记录长度无效")
            vertex = _integer(record[0], "形态增量顶点", 0, vertex_count - 1)
            try:
                values = [_F32.unpack(_F32.pack(number))[0] for number in record[1:]]
            except (TypeError, struct.error, OverflowError) as error:
                raise MorphPackagingError("形态增量超出 float32 范围") from error
            if any(type(number) not in (float, int) for number in record[1:]) or not all(map(math.isfinite, values)):
                _fail("形态增量包含无效数值")
            quantized.append([vertex, *values])
        delta["records"] = quantized
    low, high = _bounds(actual_positions, deltas, channels)
    mapping_hash = capture.get("mapping_sha256")
    if not isinstance(mapping_hash, str) or re.fullmatch("[0-9a-f]{64}", mapping_hash) is None:
        _fail("形态来源映射摘要无效")
    result = dict(unit_id=f"{unit_id:016x}", mesh_index=mesh_index,
                  lod_index=_integer(_field(info, "LodIndex"), "LodIndex", -1),
                  stream_index=stream_index, vertex_offset=vertex_offset,
                  vertex_count=vertex_count, stream_vertex_count=stream_vertices,
                  stride=stride, vertex_buffer_offset=vb_offset, vertex_buffer_size=vb_size,
                  stream_sha256=hashlib.sha256(vertex_bytes).hexdigest(),
                  index_sha256=hashlib.sha256(b"".join(index_parts)).hexdigest(),
                  unit_sha256=unit_hash, unit_version=unit.Version, components=components,
                  bounds_min=low, bounds_max=high, deltas=deltas,
                  mapping_sha256=mapping_hash, index_stride=index_stride,
                  index_buffer_offset=ib_offset, index_buffer_size=ib_size,
                  stream_index_count=stream_indices, sections=section_metadata,
                  normal_encoding="packed_oct_10_10",
                  stream_hash_scope="complete_vertex_buffer_including_padding",
                  index_hash_scope="section_bytes_in_section_order",
                  bounds_scope="all_channel_slider_combinations_and_baseline")
    result.update(_transform_metadata(unit, info))
    return result


def build_document(plan, source_sha256, units):
    """Build and validate a document from final Unit bytes and prepared captures.

    Unit IDs are numeric integers. Capture keys are final MeshInfoArray indices,
    not pre-removal authoring indices; absent RawMeshes never weakens byte checks.
    The source hash is supplied by the caller's complete authoring revision gate.
    """
    document = dict(schema="HD2Morph1", project_id=str(plan.get("project_id", "")).lower(),
                    archive_id=str(plan.get("archive_id", plan.get("archive_name", ""))).lower(),
                    source_sha256=source_sha256, coordinate_space="mesh_local_pre_skinning",
                    normal_mode="normalized_linear_delta", channels=[], meshes=[])
    channels, seen_units = {}, set()
    for unit_id, unit, main, stream, gpu, captures in units:
        unit_id = _integer(unit_id, "Unit ID", 1, 0xFFFFFFFFFFFFFFFF)
        if unit_id in seen_units:
            _fail("形态封包包含重复 Unit ID")
        seen_units.add(unit_id)
        if not isinstance(captures, dict):
            _fail("形态捕获必须按最终 MeshInfoIndex 索引")
        if not captures:
            continue
        main, stream, gpu = (_bytes(main, "Unit main"), _bytes(stream, "Unit stream"), _bytes(gpu, "Unit GPU"))
        for capture in captures.values():
            if not isinstance(capture, dict) or not isinstance(capture.get("channels"), list):
                _fail("形态捕获缺少通道定义")
            for channel in capture["channels"]:
                if not isinstance(channel, dict) or not isinstance(channel.get("id"), str):
                    _fail("形态通道定义无效")
                identity = channel["id"]
                if identity in channels and channels[identity] != channel:
                    _fail("同一形态通道的元数据在不同网格间冲突")
                channels[identity] = copy.deepcopy(channel)
        # Validate shared channel semantics before bounds arithmetic uses them.
        document["channels"] = sorted(channels.values(), key=lambda channel: channel["id"])
        try:
            morph_format.validate(dict(document, meshes=[]))
        except morph_format.MorphFormatError as error:
            raise MorphPackagingError(str(error)) from error
        for index in captures:
            _integer(index, "形态捕获 MeshInfoIndex")
        raw = _raw_meshes(unit, captures)
        fingerprint = _unit_fingerprint(main, stream, gpu)
        for mesh_index, capture in sorted(captures.items()):
            document["meshes"].append(_mesh_document(unit_id, unit, gpu, mesh_index,
                                                    capture, raw.get(mesh_index), fingerprint, channels))
    document["channels"] = sorted(channels.values(), key=lambda channel: channel["id"])
    document["meshes"].sort(key=lambda mesh: (mesh["unit_id"], mesh["mesh_index"], mesh["lod_index"], mesh["stream_index"]))
    try:
        morph_format.validate(document)
    except morph_format.MorphFormatError as error:
        raise MorphPackagingError(str(error)) from error
    return document
