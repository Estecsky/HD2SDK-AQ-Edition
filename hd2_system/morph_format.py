"""Portable HD2Morph1 sidecar codec (stdlib only; no Blender dependency).

The 72-byte little-endian header is ``<8sIIQQQ32s>``: magic, version,
header size, file size, UTF-8 directory size, record count, and SHA-256 of
everything after the header. The canonical JSON directory is followed by
``<I6f>`` records: mesh-local vertex index, position delta, normal delta.
Each directory delta references a contiguous range by *record* offset/count.
Ranges must cover the payload once, in directory order. ZIP supplies compression.

Document order is preserved; dictionary keys are sorted on disk. Unknown JSON
metadata is retained for compatible readers, but cannot override wire fields.
All hashes identify exported source bytes, not this sidecar's own file hash.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct


SUFFIX = ".hd2morph"
CAPABILITY = "morph-v1"
EXPORT_AVAILABLE = False
EXPORT_UNAVAILABLE = "当前公开版暂未开放形态键导出"
MAGIC = b"HD2MOR1\0"
VERSION = 1
HEADER = struct.Struct("<8sIIQQQ32s")
RECORD = struct.Struct("<I6f")
MAX_BYTES = 256 * 1024 * 1024
MAX_DIRECTORY_BYTES = 16 * 1024 * 1024
MAX_RECORDS = (MAX_BYTES - HEADER.size) // RECORD.size
MAX_CHANNELS = 4096
MAX_MESHES = 65536
MAX_NAME_BYTES = 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 1_000_000
_UINT32 = (1 << 32) - 1
_UINT64 = (1 << 64) - 1
_FLOAT32_MAX = 3.4028234663852886e38


class MorphFormatError(ValueError):
    """The document or binary is not an unambiguous HD2Morph1 payload."""


def _fail(where, message):
    raise MorphFormatError(f"{where}: {message}")


def _mapping(value, where):
    if type(value) is not dict:
        _fail(where, "expected an object")
    return value


def _array(value, where, maximum):
    if type(value) is not list or len(value) > maximum:
        _fail(where, f"expected an array with at most {maximum} items")
    return value


def _integer(value, where, minimum=0, maximum=_UINT32):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(where, f"expected an integer in [{minimum}, {maximum}]")
    return value


def _number(value, where):
    if type(value) not in (int, float):
        _fail(where, "expected a finite number")
    try:
        valid = math.isfinite(value) and abs(value) <= _FLOAT32_MAX
    except OverflowError:
        valid = False
    if not valid:
        _fail(where, "number is not finite or exceeds float32 range")
    return value


def _name(value, where, allow_empty=False):
    if type(value) is not str or (not value and not allow_empty) or "\0" in value:
        _fail(where, "expected a nonempty UTF-8 name without NUL")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as error:
        raise MorphFormatError(f"{where}: invalid UTF-8 name") from error
    if size > MAX_NAME_BYTES:
        _fail(where, f"name exceeds {MAX_NAME_BYTES} UTF-8 bytes")
    return value


def _hex(value, width, where):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{%d}" % width, value) is None:
        _fail(where, f"expected {width} lowercase hexadecimal digits")
    return value


def _vector(value, where):
    if type(value) is not list or len(value) != 3:
        _fail(where, "expected three coordinates")
    for index, coordinate in enumerate(value):
        _number(coordinate, f"{where}[{index}]")
    return value


def _safe_json(value):
    """Bound recursion and validate extension metadata before JSON encoding."""
    ancestors = set()
    nodes = 0

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            _fail("directory", "JSON complexity limit exceeded")
        kind = type(item)
        if item is None or kind is bool:
            return
        if kind is int:
            if not -(1 << 63) <= item <= _UINT64:
                _fail("directory", "JSON integer exceeds 64-bit range")
            return
        if kind is float:
            if not math.isfinite(item):
                _fail("directory", "JSON contains a nonfinite number")
            return
        if kind is str:
            try:
                if len(item.encode("utf-8")) > MAX_DIRECTORY_BYTES:
                    _fail("directory", "JSON string is too large")
            except UnicodeError as error:
                raise MorphFormatError("directory: invalid UTF-8 string") from error
            return
        if kind not in (dict, list):
            _fail("directory", "unsupported JSON value")
        identity = id(item)
        if identity in ancestors:
            _fail("directory", "cyclic JSON data")
        ancestors.add(identity)
        if kind is dict:
            for key, child in item.items():
                if type(key) is not str:
                    _fail("directory", "JSON object keys must be strings")
                _name(key, "directory key", allow_empty=True)
                visit(child, depth + 1)
        else:
            for child in item:
                visit(child, depth + 1)
        ancestors.remove(identity)

    visit(value, 0)


def _canonical_json(value):
    _safe_json(value)
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False,
                            sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise MorphFormatError("directory: invalid JSON data") from error
    if len(result) > MAX_DIRECTORY_BYTES:
        _fail("directory", "directory byte limit exceeded")
    return result


def _prepare(document):
    """Validate semantic document and make metadata without mutating the caller."""
    _mapping(document, "document")
    for key, expected in (("schema", "HD2Morph1"),
                          ("coordinate_space", "mesh_local_pre_skinning"),
                          ("normal_mode", "normalized_linear_delta")):
        if document.get(key) != expected:
            _fail(key, f"expected {expected!r}")
    for key, width in (("project_id", 32), ("archive_id", 16), ("source_sha256", 64)):
        _hex(document.get(key), width, key)

    channels = _array(document.get("channels"), "channels", MAX_CHANNELS)
    channel_ids = set()
    for index, channel in enumerate(channels):
        where = f"channels[{index}]"
        _mapping(channel, where)
        channel_id = _hex(channel.get("id"), 32, where + ".id")
        if channel_id in channel_ids:
            _fail(where, "duplicate channel id")
        channel_ids.add(channel_id)
        for key in ("name", "source_object", "relative_key"):
            _name(channel.get(key), where + "." + key)
        for key in ("min", "max", "default", "baseline", "normal_sample_weight"):
            _number(channel.get(key), where + "." + key)
        if channel["min"] > channel["max"]:
            _fail(where, "minimum exceeds maximum")
        for key in ("has_driver", "muted"):
            if type(channel.get(key)) is not bool:
                _fail(where + "." + key, "expected a boolean")
        for key in ("default", "baseline"):
            if not channel["min"] <= channel[key] <= channel["max"]:
                # Blender evaluates muted keys as zero even when the author's
                # slider bounds exclude zero. Retain both facts faithfully.
                if not (channel["muted"] and channel[key] == 0):
                    _fail(where, "default/baseline is outside channel range")
        if abs(channel["normal_sample_weight"] - channel["baseline"]) <= 1e-12:
            _fail(where, "normal sample weight must differ from baseline")

    meshes = _array(document.get("meshes"), "meshes", MAX_MESHES)
    directory = dict(document)
    directory["meshes"] = []
    mesh_keys = set()
    record_count = 0
    for mesh_index, mesh in enumerate(meshes):
        where = f"meshes[{mesh_index}]"
        _mapping(mesh, where)
        _hex(mesh.get("unit_id"), 16, where + ".unit_id")
        for key in ("mesh_index", "stream_index", "vertex_offset", "unit_version"):
            _integer(mesh.get(key), where + "." + key)
        _integer(mesh.get("lod_index"), where + ".lod_index", minimum=-1)
        for key in ("vertex_count", "stream_vertex_count"):
            _integer(mesh.get(key), where + "." + key, minimum=1)
        _integer(mesh.get("stride"), where + ".stride", minimum=1, maximum=4096)
        _integer(mesh.get("vertex_buffer_offset"), where + ".vertex_buffer_offset", maximum=_UINT64)
        _integer(mesh.get("vertex_buffer_size"), where + ".vertex_buffer_size", maximum=_UINT64)
        identity = tuple(mesh[key] for key in ("unit_id", "mesh_index", "lod_index", "stream_index"))
        if identity in mesh_keys:
            _fail(where, "duplicate mesh key")
        mesh_keys.add(identity)
        if mesh["vertex_offset"] + mesh["vertex_count"] > mesh["stream_vertex_count"]:
            _fail(where, "mesh vertex range exceeds stream")
        if mesh["stream_vertex_count"] * mesh["stride"] > mesh["vertex_buffer_size"]:
            _fail(where, "vertex stream exceeds vertex buffer size")
        if mesh["vertex_buffer_offset"] + mesh["vertex_buffer_size"] > _UINT64:
            _fail(where, "vertex buffer offset overflows uint64")
        for key in ("stream_sha256", "index_sha256", "unit_sha256"):
            _hex(mesh.get(key), 64, where + "." + key)
        components = _array(mesh.get("components"), where + ".components", 256)
        if not components:
            _fail(where, "vertex components cannot be empty")
        component_ids = set()
        occupied = []
        for component_index, component in enumerate(components):
            cwhere = f"{where}.components[{component_index}]"
            _mapping(component, cwhere)
            for key in ("type", "format", "index", "offset"):
                _integer(component.get(key), cwhere + "." + key)
            _integer(component.get("size"), cwhere + ".size", minimum=1)
            component_id = (component["type"], component["index"])
            if component_id in component_ids:
                _fail(cwhere, "duplicate vertex component")
            component_ids.add(component_id)
            start, end = component["offset"], component["offset"] + component["size"]
            if end > mesh["stride"] or any(start < b and end > a for a, b in occupied):
                _fail(cwhere, "vertex component overlaps or exceeds stride")
            occupied.append((start, end))
        low = _vector(mesh.get("bounds_min"), where + ".bounds_min")
        high = _vector(mesh.get("bounds_max"), where + ".bounds_max")
        if any(a > b for a, b in zip(low, high)):
            _fail(where, "bounds minimum exceeds maximum")
        deltas = _array(mesh.get("deltas"), where + ".deltas", MAX_CHANNELS)
        mesh_directory = dict(mesh)
        mesh_directory["deltas"] = []
        directory["meshes"].append(mesh_directory)
        seen_channels = set()
        for delta_index, delta in enumerate(deltas):
            dwhere = f"{where}.deltas[{delta_index}]"
            _mapping(delta, dwhere)
            channel_id = _hex(delta.get("channel_id"), 32, dwhere + ".channel_id")
            if channel_id not in channel_ids or channel_id in seen_channels:
                _fail(dwhere, "unknown or duplicate channel reference")
            seen_channels.add(channel_id)
            if "record_offset" in delta or "record_count" in delta:
                _fail(dwhere, "document must not contain reserved wire range fields")
            records = _array(delta.get("records"), dwhere + ".records", MAX_RECORDS)
            if record_count + len(records) > MAX_RECORDS:
                _fail(dwhere, "record limit exceeded")
            previous = -1
            for record_index, record in enumerate(records):
                rwhere = f"{dwhere}.records[{record_index}]"
                if type(record) not in (list, tuple) or len(record) != 7:
                    _fail(rwhere, "expected vertex index and six floats")
                vertex = _integer(record[0], rwhere + ".vertex", maximum=mesh["vertex_count"] - 1)
                if vertex <= previous:
                    _fail(rwhere, "vertex indices must be unique and ascending")
                previous = vertex
                for number in record[1:]:
                    _number(number, rwhere)
            delta_directory = {key: value for key, value in delta.items() if key != "records"}
            delta_directory["record_offset"] = record_count
            delta_directory["record_count"] = len(records)
            mesh_directory["deltas"].append(delta_directory)
            record_count += len(records)
    directory_bytes = _canonical_json(directory)
    if HEADER.size + len(directory_bytes) + record_count * RECORD.size > MAX_BYTES:
        _fail("document", "file byte limit exceeded")
    return directory_bytes, record_count


def validate(document):
    """Raise :class:`MorphFormatError` for invalid documents; otherwise return None."""
    _prepare(document)


def encode(document):
    """Encode a semantic document deterministically without changing it."""
    directory, count = _prepare(document)
    total = HEADER.size + len(directory) + count * RECORD.size
    output = bytearray(total)
    output[HEADER.size:HEADER.size + len(directory)] = directory
    offset = HEADER.size + len(directory)
    for mesh in document["meshes"]:
        for delta in mesh["deltas"]:
            for record in delta["records"]:
                RECORD.pack_into(output, offset, *record)
                offset += RECORD.size
    digest = hashlib.sha256(memoryview(output)[HEADER.size:]).digest()
    HEADER.pack_into(output, 0, MAGIC, VERSION, HEADER.size, total, len(directory), count, digest)
    return bytes(output)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("directory", f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value):
    _fail("directory", f"nonfinite JSON constant {value}")


def decode(blob):
    """Verify integrity, ranges and semantics before returning a document."""
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        _fail("file", "expected bytes")
    # Normalize memoryviews: a typed/noncontiguous view must not change byte offsets.
    if isinstance(blob, memoryview):
        if blob.nbytes > MAX_BYTES:
            _fail("file", "file byte limit exceeded")
        blob = blob.tobytes()
    if not HEADER.size <= len(blob) <= MAX_BYTES:
        _fail("file", "invalid file size")
    magic, version, header_size, total, directory_size, count, digest = HEADER.unpack_from(blob)
    if magic != MAGIC or version != VERSION or header_size != HEADER.size:
        _fail("header", "unsupported magic, version or header size")
    if total != len(blob) or directory_size > MAX_DIRECTORY_BYTES or count > MAX_RECORDS:
        _fail("header", "invalid file, directory or record size")
    payload_start = HEADER.size + directory_size
    if payload_start + count * RECORD.size != total:
        _fail("header", "payload size does not match record count")
    if hashlib.sha256(memoryview(blob)[HEADER.size:]).digest() != digest:
        _fail("file", "SHA-256 integrity check failed")
    directory_bytes = bytes(blob[HEADER.size:payload_start])
    try:
        document = json.loads(directory_bytes.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise MorphFormatError(f"directory: cannot decode JSON: {error}") from error
    _mapping(document, "directory")
    if _canonical_json(document) != directory_bytes:
        _fail("directory", "JSON is not in canonical UTF-8 form")
    next_record = 0
    for mesh_index, mesh in enumerate(_array(document.get("meshes"), "meshes", MAX_MESHES)):
        where = f"meshes[{mesh_index}]"
        _mapping(mesh, where)
        for delta_index, delta in enumerate(_array(mesh.get("deltas"), where + ".deltas", MAX_CHANNELS)):
            dwhere = f"{where}.deltas[{delta_index}]"
            _mapping(delta, dwhere)
            if "records" in delta:
                _fail(dwhere, "inline records are forbidden in the directory")
            start = _integer(delta.get("record_offset"), dwhere + ".record_offset", maximum=count)
            length = _integer(delta.get("record_count"), dwhere + ".record_count", maximum=count)
            if start != next_record or start + length > count:
                _fail(dwhere, "record ranges overlap, contain gaps or exceed payload")
            del delta["record_offset"]
            del delta["record_count"]
            delta["records"] = [list(RECORD.unpack_from(blob, payload_start + i * RECORD.size))
                                for i in range(start, start + length)]
            next_record += length
    if next_record != count:
        _fail("directory", "unreferenced payload records")
    validate(document)
    return document
