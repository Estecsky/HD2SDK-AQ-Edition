"""Verified ultra templates, display schemas and atomic GPU identity rebinding.

The legacy 28 schema stays available. New schemas come from hash-checked shipped
main files and their identity manifests; editable values never identify a root.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct


_CUSTOM_IDENTITY = None


def _custom_identity():
    """Also work when this pure helper is loaded without addon startup."""
    global _CUSTOM_IDENTITY
    if _CUSTOM_IDENTITY is None:
        path = Path(__file__).with_name('custom_material_identity.py')
        spec = importlib.util.spec_from_file_location('_hd2_custom_material_identity', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CUSTOM_IDENTITY = module
    return _CUSTOM_IDENTITY


def is_embedded_shader_root(main, gpu):
    """Root resources own GPU programs independently of a registered template."""
    return bool(gpu) and len(main) >= 136 and struct.unpack_from('<Q', main, 24)[0] == 0


VISIBLE_FIELDS = (
    ("TintMaskColor", 0xc3befbff),
    ("MetallicScale", 0xab4096ab),
    ("RoughnessScale", 0x20d102d1),
    ("RoughnessBias", 0x758d22ba),
    ("NormalStrength", 0xc2e15ee9),
    ("MicroAoIntensity", 0x6a0b8014),
    ("SubsurfaceIntensity", 0x38dae7bd),
    ("SubsurfaceDiffusion", 0xd43cfc8b),
    ("SubsurfaceThickness", 0x15e00526),
    ("ClearcoatIntensity", 0xb132b95e),
    ("ClearcoatRoughness", 0x868e1f43),
    ("ClearcoatNormalMix", 0x92607599),
    ("EmissiveColor", 0xc985395a),
    ("EmissiveIntensity", 0x02f6dc5b),
)
VISIBLE_IDS = tuple(value for _, value in VISIBLE_FIELDS)
TEXTURE_SLOTS = (
    0xa72cb013, 0x479fb1ef, 0x4c567810, 0x90b84a53, 0xe719da57,
    0xe67ac0c7, 0x736a0029, 0x04fb61ad, 0xd2f99d38, 0xf8e31d7b,
    0xa59f5e11, 0x1625c821,
)
# (ID, float class); array element count and stride are both zero.
PARAMETER_SCHEMA = (
    (0xab4096ab, 0), (0x4485c321, 0), (0xd8183551, 0), (0x6e8e2d58, 0),
    (0x886ad0fe, 0), (0x13ffdef9, 0), (0x92607599, 0), (0xa144abc6, 0),
    (0x6f2bffb2, 0), (0xc998ac9f, 0), (0x4012b6b4, 2), (0x07661c1b, 2),
    (0xb66c09c5, 0), (0x80b2e50b, 2), (0x717ab33c, 0), (0x9143d0f6, 2),
    (0x8d32b3d3, 0), (0xcf0746b9, 0), (0xd0a0e0fc, 0), (0x38dae7bd, 0),
    (0x327edb10, 2), (0x68b5e2e8, 0), (0x20d102d1, 0), (0x15e00526, 0),
    (0xb463417c, 2), (0xc985395a, 2), (0x37d22807, 2), (0xcd18a390, 1),
    (0x8de83b3e, 0), (0x6a0b8014, 0), (0x868e1f43, 0), (0x879189ff, 0),
    (0x7d61d484, 0), (0x3de979ac, 0), (0x3412359b, 1), (0x11855634, 0),
    (0x2e2fcb48, 0), (0x9a905015, 2), (0x7feb283e, 0), (0x2c9d0736, 0),
    (0xc3befbff, 2), (0x758d22ba, 0), (0x6fd56a28, 0), (0xcff8242b, 0),
    (0x88b1a3e2, 0), (0x1b01834d, 0), (0x8b153619, 0), (0xe38d2e78, 0),
    (0x4408549c, 0), (0x8f47dd53, 2), (0x35c2cfe5, 0), (0x834e87ba, 0),
    (0x80b10f6d, 2), (0x02f6dc5b, 0), (0x2dbf2086, 0), (0x16906f32, 0),
    (0x0c687406, 2), (0x7415ab88, 0), (0xa85d996a, 0), (0xb132b95e, 0),
    (0x02325c30, 0), (0xd43cfc8b, 0), (0x2ecb5f27, 0), (0x4d0ba89d, 0),
    (0xc2e15ee9, 0), (0x9cde96b8, 2), (0xf85d2474, 0), (0x8b09df61, 2),
    (0x8753c2fe, 2), (0x4c5d8840, 0), (0x14f7ceac, 0), (0x8d81f372, 2),
    (0x31d3188f, 0), (0xe1c515d9, 0), (0xfa08826f, 2), (0xee28022c, 0),
    (0x325d6bd5, 0),
)
_SCHEMA = frozenset((key, klass, 0, 0) for key, klass in PARAMETER_SCHEMA)


def is_legacy_ultra_material(material):
    """Recognize schema, independent of editable values, IDs and list order."""
    try:
        variables = material.ShaderVariables
        return (material.ParentMaterialID == 0
                and material.NumVariables == len(variables) == 77
                and material.NumTextures == len(material.TexIDs) == 12
                and tuple(material.TexUnks) == TEXTURE_SLOTS
                and frozenset((v.ID, v.klass, v.elements, v.elementStride)
                              for v in variables) == _SCHEMA)
    except (AttributeError, TypeError):
        return False


def legacy_visible_parameters(material):
    """Return (real index, variable), with a conservative non-ultra fallback."""
    indexed = list(enumerate(material.ShaderVariables))
    if not is_legacy_ultra_material(material):
        return indexed
    by_id = {variable.ID: (index, variable) for index, variable in indexed}
    return [by_id[key] for key in VISIBLE_IDS]


def is_legacy_ultra_main(main):
    """Recognize serialized main data without importing Blender's serializer."""
    if len(main) < 136:
        return False
    parent = struct.unpack_from("<Q", main, 24)[0]
    textures = struct.unpack_from("<I", main, 64)[0]
    variables = struct.unpack_from("<I", main, 104)[0]
    if parent or textures != 12 or variables != 77:
        return False
    values_start = 136 + textures * 12 + variables * 20
    if len(main) < values_start:
        return False
    if struct.unpack_from("<12I", main, 136) != TEXTURE_SLOTS:
        return False
    schema = set()
    for index in range(variables):
        klass, elements, key, offset, stride = struct.unpack_from(
            "<5I", main, 136 + textures * 12 + index * 20)
        if klass > 3 or values_start + offset + (klass + 1) * 4 > len(main):
            return False
        schema.add((key, klass, elements, stride))
    return schema == _SCHEMA


def _uint(value, bits=32):
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise ValueError("ultra identity integer outside uint%d" % bits)
    return value


def _u32(data, offset):
    if type(offset) is not int or offset < 0 or offset + 4 > len(data):
        raise ValueError("ultra identity offset outside GPU data")
    return struct.unpack_from("<I", data, offset)[0]


def rebind_gpu_identity(gpu, manifest, new_id):
    """Clone trusted, hash-checked template metadata without changing DXBC.

    Manifest offsets are produced by the offline strict container parser. This
    function is not a general parser or a repair path for arbitrary roots.
    """
    _uint(new_id, 64)
    if not new_id or not new_id >> 32:
        raise ValueError("ultra requires a nonzero material high tag")
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported ultra identity manifest")
    expected_hash = manifest.get("gpu_sha256")
    if expected_hash != hashlib.sha256(gpu).hexdigest():
        raise ValueError("ultra GPU template hash mismatch")
    old_id = int(manifest["template_material_id"], 16)
    _uint(old_id, 64)
    if _u32(gpu, 0) != 179:
        raise ValueError("unsupported ultra GPU version")
    table = _u32(gpu, 28)
    block = _u32(gpu, 12)
    programs = _u32(gpu, 20)
    table_end = table + _u32(gpu, 32)
    # Preserve the original exact-end contract. Mixed donor/UI containers may
    # additionally align that end to eight bytes (the shipped template adds two).
    if (block + 8 != table or programs not in (table_end, (table_end + 7) & ~7)
            or gpu[table_end:programs] != bytes(programs - table_end)
            or block < 36 or table + 8 > len(gpu)
            or programs > len(gpu)
            or struct.unpack_from("<Q", gpu, block)[0] != 0x209fb8c3c0a8c3a4
            or _u32(gpu, table) != 1):
        raise ValueError("ultra GPU identity table is inconsistent")
    tag_offsets = manifest.get("material_tag_offsets")
    if tag_offsets != [4, table + 4]:
        raise ValueError("ultra material identity locations mismatch")
    touched = set()
    patches = []
    dxbc_ranges = []
    cursor = 0
    while True:
        cursor = gpu.find(b"DXBC", cursor)
        if cursor < 0:
            break
        if cursor + 32 <= len(gpu):
            length = _u32(gpu, cursor + 24)
            if length >= 32 and cursor + length <= len(gpu):
                dxbc_ranges.append((cursor, cursor + length))
        cursor += 4

    def add(offset, expected, replacement):
        # Container fields may be unaligned; byte ranges still must not overlap.
        _u32(gpu, offset)
        span = set(range(offset, offset + 4))
        if span & touched or _u32(gpu, offset) != expected:
            raise ValueError("ultra identity field overlaps or value mismatches")
        if any(offset < end and offset + 4 > start for start, end in dxbc_ranges):
            raise ValueError("ultra identity field overlaps DXBC")
        touched.update(span)
        patches.append((offset, replacement))

    for offset in tag_offsets:
        add(offset, old_id >> 32, new_id >> 32)
    reserved = {_uint(v) for v in manifest.get("reserved_names", [])}
    families = []
    for family in ("stage_name_fields", "group_name_fields"):
        fields = manifest.get(family)
        if not isinstance(fields, list) or not fields:
            raise ValueError("ultra identity name inventory is missing")
        old_values = set()
        for field in fields:
            value = _uint(field["value"])
            if value in old_values:
                raise ValueError("ultra duplicate name equivalence class")
            old_values.add(value)
            offsets = field["offsets"]
            if not isinstance(offsets, list) or not offsets:
                raise ValueError("ultra empty identity equivalence class")
        reserved.update(old_values)
        families.append((family, fields))
    for family, fields in families:
        for field in sorted(fields, key=lambda entry: entry["value"]):
            old = field["value"]
            for nonce in range(10000):
                seed = ("ultra-v1:%016x:%s:%08x:%d" %
                        (new_id, family, old, nonce)).encode("ascii")
                replacement = int.from_bytes(hashlib.sha256(seed).digest()[:4], "little")
                if replacement and replacement not in reserved:
                    break
            else:
                raise ValueError("ultra identity collision retry limit")
            reserved.add(replacement)
            for offset in field["offsets"]:
                if offset < 36:
                    raise ValueError("ultra name overlaps GPU header")
                add(offset, old, replacement)
    result = bytearray(gpu)
    for offset, value in patches:
        struct.pack_into("<I", result, offset, value)
    return bytes(result)


TEMPLATE_NAMES = (
    'ultra_cull_back', 'ultra_cull_off', 'ultra_alphaclip_cull_back',
    'ultra_alphaclip_cull_off', 'ultra_translucent',
    'ultra_translucent_depth_prepass', 'ultra_translucent_depth_prepass_cull_off', 'ultra',
)
_NEW_TEXTURE_LABELS = (
    'BaseColor/Emissive Mask', 'Roughness/Metallic/AO/TintMask',
    'Normal/SSS/Varnish', 'EmissiveColor', 'CutOutMask', 'AlphaMask',
)
_MATERIALS = Path(__file__).resolve().parents[1] / 'materials'
_BUNDLE_CACHE = {}
_COMPATIBILITY_CACHE = {}
# Presentation only. Never consulted by identity validation or export. Entries
# contain immutable schema/label tuples, not editable material objects or GPU data.
_EDITOR_LAYOUTS = {}


def _editor_record(bundle):
    return (bundle['name'], bundle['signature'], bundle['visible'], bundle['texture_indices'])


def _template_family(name):
    if name in ('ultra_translucent', 'ultra_translucent_depth_prepass',
                'ultra_translucent_depth_prepass_cull_off'):
        return 'ultra_translucent'
    return name.split('_cull_')[0]


def editor_view(material, template_name=None):
    """Read current values/indices without file access, GPU hashing or validation.

    Creation, import and opening the editor populate presentation metadata through
    the verified loaders. Reopening refreshes the exact template label. This view
    is not a verification result and cannot authorize a resource operation.
    """
    indexed = list(enumerate(material.ShaderVariables))
    if template_name is None:
        return False, None, [(i, None) for i in range(len(material.TexIDs))], indexed
    if is_legacy_ultra_material(material):
        return (True, 'ultra',
                [(5, '颜色 / 发光遮罩'), (2, '法线 / AO / 粗糙度'), (3, '金属 / SSS / 清漆 / 染色遮罩')],
                legacy_visible_parameters(material))
    signature = _material_signature(material)
    matches = [b for b in _EDITOR_LAYOUTS.get(Path(_MATERIALS), ()) if b[1] == signature]
    if not matches:
        name = None if str(template_name).startswith('ultra') else template_name
        return False, name, [(i, None) for i in range(len(material.TexIDs))], indexed
    named = [b for b in matches if b[0] == template_name]
    if named:
        matches = named
        name = template_name
    else:
        families = {_template_family(b[0]) for b in matches}
        name = next(iter(families)) if len(families) == 1 else 'ultra (custom)'
    if len({(b[2], b[3]) for b in matches}) != 1:
        return True, name, [(i, '') for i in range(len(material.TexIDs))], indexed
    by_id = {v.ID: (i, v) for i, v in indexed}
    rows = [(i, _NEW_TEXTURE_LABELS[i] if i < len(_NEW_TEXTURE_LABELS) else str(i + 1))
            for i in matches[0][3]]
    return True, name, rows, [by_id[key] for key in matches[0][2]]


def _main_signature(main):
    """Read storage identity, checking descriptor bounds and duplicate IDs."""
    try:
        if len(main) < 136 or struct.unpack_from('<Q', main, 24)[0]:
            return None
        textures, variables = _u32(main, 64), _u32(main, 104)
        if not 0 < textures < 256 or not 0 < variables < 1024:
            return None
        start = 136 + textures * 12 + variables * 20
        if start > len(main):
            return None
        slots = struct.unpack_from('<%dI' % textures, main, 136)
        schema, ids = set(), set()
        for i in range(variables):
            klass, elements, key, offset, stride = struct.unpack_from(
                '<5I', main, 136 + textures * 12 + i * 20)
            if klass > 3 or elements or stride or key in ids:
                return None
            if start + offset + (klass + 1) * 4 > len(main):
                return None
            ids.add(key)
            schema.add((key, klass, elements, stride))
        return slots, frozenset(schema)
    except (ValueError, struct.error, TypeError):
        return None


def _material_signature(material):
    try:
        variables = material.ShaderVariables
        if (material.ParentMaterialID or material.NumVariables != len(variables)
                or material.NumTextures != len(material.TexIDs)
                or len({v.ID for v in variables}) != len(variables)):
            return None
        return (tuple(material.TexUnks), frozenset(
            (v.ID, v.klass, v.elements, v.elementStride) for v in variables))
    except (AttributeError, TypeError):
        return None


def _bundle(directory, name):
    if name not in TEMPLATE_NAMES:
        raise ValueError('unknown ultra template')
    directory = Path(directory)
    paths = [directory / (name + suffix) for suffix in (
        '.material', '.material.gpu_resources', '.material.stream', '.identity.json')]
    stamp = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    key = (str(directory.resolve()), name)
    cached = _BUNDLE_CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    main, gpu, stream = (p.read_bytes() for p in paths[:3])
    manifest = json.loads(paths[3].read_text(encoding='utf-8-sig'))
    for field, data in (('main_sha256', main), ('gpu_sha256', gpu), ('stream_sha256', stream)):
        if manifest.get(field) != hashlib.sha256(data).hexdigest():
            raise ValueError('%s %s mismatch' % (name, field))
    signature = _main_signature(main)
    if signature is None:
        raise ValueError('%s invalid main schema' % name)
    if name == 'ultra':
        if not is_legacy_ultra_main(main):
            raise ValueError('legacy ultra schema mismatch')
        visible = VISIBLE_IDS
        texture_indices = (5, 2, 3)
    else:
        if manifest.get('template_name') != name or manifest.get('schema_version') != 1:
            raise ValueError('%s template contract mismatch' % name)
        declared = (tuple(manifest['texture_slots']), frozenset(
            tuple(row) for row in manifest['parameter_schema']))
        if declared != signature or len(manifest['parameter_schema']) != len(signature[1]):
            raise ValueError('%s declared schema differs from main' % name)
        visible = tuple(row['id'] for row in manifest['visible_parameters'])
        texture_indices = tuple(manifest['visible_texture_indices'])
        if (len(set(visible)) != len(visible)
                or not set(visible).issubset({row[0] for row in signature[1]})
                or len(set(texture_indices)) != len(texture_indices)
                or any(type(i) is not int or not 0 <= i < len(signature[0]) for i in texture_indices)):
            raise ValueError('%s invalid visible schema' % name)
    # All metadata offsets are independently checked against the exact GPU hash.
    rebind_gpu_identity(gpu, manifest, int(manifest['template_material_id'], 16))
    result = dict(name=name, main=main, gpu=gpu, stream=stream, manifest=manifest,
                  signature=signature, visible=visible, texture_indices=texture_indices)
    _BUNDLE_CACHE[key] = stamp, result
    return result


def _compatibility_bundles(directory):
    """Frozen metadata only; historical roots never become creation menu items."""
    path = Path(directory) / 'ultra_compatibility.json'
    if not path.exists():
        return []
    stamp = (path.stat().st_mtime_ns, path.stat().st_size)
    key = str(path.resolve())
    cached = _COMPATIBILITY_CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    catalog = json.loads(path.read_text(encoding='utf-8-sig'))
    if catalog.get('schema_version') != 1 or not isinstance(catalog.get('entries'), list):
        raise ValueError('unsupported ultra compatibility catalog')
    result, seen = [], set()
    for row in catalog['entries']:
        name, identity = row['template_name'], row['compatibility_id']
        manifest = row['manifest']
        if (name not in TEMPLATE_NAMES or not isinstance(identity, str)
                or not identity or identity in seen or manifest.get('schema_version') != 1
                or type(row['gpu_bytes']) is not int or row['gpu_bytes'] < 36):
            raise ValueError('invalid ultra compatibility entry')
        seen.add(identity)
        slots = tuple(row['texture_slots'])
        schema = frozenset(tuple(item) for item in row['parameter_schema'])
        visible, indices = tuple(row['visible_parameters']), tuple(row['visible_texture_indices'])
        if (not slots or len(schema) != len(row['parameter_schema'])
                or len({item[0] for item in schema}) != len(schema)
                or any(len(item) != 4 or item[1] not in (0, 1, 2, 3)
                       or item[2:] != (0, 0) for item in schema)
                or len(set(visible)) != len(visible)
                or not set(visible).issubset({item[0] for item in schema})
                or len(set(indices)) != len(indices)
                or any(type(i) is not int or not 0 <= i < len(slots) for i in indices)):
            raise ValueError('invalid ultra compatibility schema')
        result.append(dict(name=name, manifest=manifest, gpu_bytes=row['gpu_bytes'],
                           compatibility_id=identity, signature=(slots, schema),
                           visible=visible, texture_indices=indices))
    _COMPATIBILITY_CACHE[key] = stamp, result
    return result


def _all_bundles(directory):
    for name in TEMPLATE_NAMES:
        try:
            yield _bundle(directory, name)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    # A malformed catalog must fail closed rather than enable an unverified ID change.
    yield from _compatibility_bundles(directory)


def _matching_bundles(signature, directory=None):
    if signature is None:
        return []
    directory = _MATERIALS if directory is None else directory
    result = []
    bundles = tuple(_all_bundles(directory))
    _EDITOR_LAYOUTS[Path(directory)] = tuple(_editor_record(b) for b in bundles)
    for bundle in bundles:
        if bundle['signature'] == signature:
            result.append(bundle)
    return result


def is_ultra_main(main):
    return is_legacy_ultra_main(main) or bool(_matching_bundles(_main_signature(main)))


def is_ultra_material(material):
    return is_legacy_ultra_material(material) or bool(_matching_bundles(_material_signature(material)))


def visible_parameters(material, template_name=None):
    if is_legacy_ultra_material(material):
        return legacy_visible_parameters(material)
    indexed = list(enumerate(material.ShaderVariables))
    matches = _matching_bundles(_material_signature(material))
    if not matches:
        return indexed
    named = [b for b in matches if b['name'] == template_name]
    if named:
        matches = named
    elif len({b['visible'] for b in matches}) != 1:
        return indexed
    by_id = {v.ID: (i, v) for i, v in indexed}
    return [by_id[key] for key in matches[0]['visible']]


def texture_rows(material, template_name=None):
    if is_legacy_ultra_material(material):
        return [(5, '颜色 / 发光遮罩'), (2, '法线 / AO / 粗糙度'), (3, '金属 / SSS / 清漆 / 染色遮罩')]
    matches = _matching_bundles(_material_signature(material))
    if not matches:
        return [(i, '') for i in range(len(material.TexIDs))]
    named = [b for b in matches if b['name'] == template_name]
    if named:
        matches = named
    elif len({b['texture_indices'] for b in matches}) != 1:
        return [(i, '') for i in range(len(material.TexIDs))]
    return [(i, _NEW_TEXTURE_LABELS[i] if i < len(_NEW_TEXTURE_LABELS) else str(i + 1))
            for i in matches[0]['texture_indices']]


def _current_manifest(bundle, gpu, old_id):
    """Normalize only the verified identity fields; shaders and states must match."""
    original = bundle['manifest']
    length = len(bundle['gpu']) if 'gpu' in bundle else bundle['gpu_bytes']
    if len(gpu) != length:
        raise ValueError('ultra GPU length differs from verified template')
    normalized = bytearray(gpu)
    current = dict(original, template_material_id='%016x' % old_id,
                   gpu_sha256=hashlib.sha256(gpu).hexdigest())
    for offset in original['material_tag_offsets']:
        if _u32(gpu, offset) != old_id >> 32:
            raise ValueError('ultra GPU identity differs from resource ID')
        struct.pack_into('<I', normalized, offset, int(original['template_material_id'], 16) >> 32)
    for family in ('stage_name_fields', 'group_name_fields'):
        fields, seen = [], set()
        for field in original[family]:
            values = {_u32(gpu, offset) for offset in field['offsets']}
            if len(values) != 1 or not next(iter(values)) or values & seen:
                raise ValueError('ultra identity equivalence classes differ')
            value = next(iter(values))
            seen.add(value)
            fields.append(dict(value=value, offsets=list(field['offsets'])))
            for offset in field['offsets']:
                struct.pack_into('<I', normalized, offset, _uint(field['value']))
        current[family] = fields
    if hashlib.sha256(normalized).hexdigest() != original['gpu_sha256']:
        raise ValueError('ultra GPU has unverified non-identity changes; ID change refused')
    # Check every recorded location, overlap and container invariant, also for
    # metadata-only historical entries. The returned bytes are not published.
    rebind_gpu_identity(bytes(normalized), original, int(original['template_material_id'], 16))
    return current


def material_template_name(main, gpu=b''):
    matches = _matching_bundles(_main_signature(main))
    if gpu and len(gpu) >= 8:
        old_id = (_u32(gpu, 4) << 32) | 1
        for bundle in matches:
            try:
                _current_manifest(bundle, gpu, old_id)
                return bundle['name']
            except ValueError:
                continue
        # A familiar parameter layout does not identify a custom shader.
        return None
    if is_legacy_ultra_main(main):
        return 'ultra'
    if matches:
        # Custom shaders retain their editable schema, but never claim a cull mode.
        names = {_template_family(b['name']) for b in matches}
        return next(iter(names)) if len(names) == 1 else 'ultra (custom)'
    return None


def load_template(materials_dir, new_id, template_name='ultra'):
    bundle = _bundle(materials_dir, template_name)
    key = Path(materials_dir)
    records = _EDITOR_LAYOUTS.get(key, ())
    _EDITOR_LAYOUTS[key] = tuple(b for b in records if b[0] != template_name) + (_editor_record(bundle),)
    return (bundle['main'], rebind_gpu_identity(bundle['gpu'], bundle['manifest'], new_id), bundle['stream'])


def load_default_resources(materials_dir, template_name):
    """Validate every default sidecar before any new TOC entry is committed."""
    directory = Path(materials_dir).resolve()
    bundle = _bundle(directory, template_name)
    result, seen = [], set()
    for row in bundle['manifest'].get('default_resources', []):
        rid = int(row['id'], 16) if isinstance(row['id'], str) else _uint(row['id'], 64)
        kind = int(row['type'], 16) if isinstance(row['type'], str) else _uint(row['type'], 64)
        _uint(rid, 64)
        if not rid or kind != 14790446551990181426 or (rid, kind) in seen:
            raise ValueError('invalid or duplicate ultra default texture identity')
        seen.add((rid, kind))
        parts = []
        for field in ('main', 'gpu', 'stream'):
            path = (directory / row[field]).resolve()
            try:
                path.relative_to(directory)
            except ValueError:
                raise ValueError('ultra default path escapes the materials directory')
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != row[field + '_sha256']:
                raise ValueError('ultra default texture hash mismatch')
            parts.append(data)
        result.append((rid, kind, *parts))
    if template_name != 'ultra':
        texture_count = len(bundle['signature'][0])
        references = struct.unpack_from('<%dQ' % texture_count, bundle['main'], 136 + texture_count * 4)
        if not {references[i] for i in bundle['texture_indices']}.issubset({row[0] for row in result}):
            raise ValueError('ultra template core textures are missing default resources')
    return result


def rebind_existing_gpu(materials_dir, gpu, old_id, new_id):
    if old_id == new_id:
        return gpu
    _uint(old_id, 64)
    for bundle in _all_bundles(materials_dir):
        try:
            current = _current_manifest(bundle, gpu, old_id)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        return rebind_gpu_identity(gpu, current, new_id)
    return _custom_identity().rebind_existing_gpu(gpu, old_id, new_id)


def capture_gpu_identity(materials_dir, gpu, material_id):
    """Small reversible identity record for the independent export namespace."""
    for bundle in _all_bundles(materials_dir):
        try:
            current = _current_manifest(bundle, gpu, material_id)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        return dict(schema_version=1, template_name=bundle['name'],
                    compatibility_id=bundle.get('compatibility_id'),
                    material_id='%016x' % material_id,
                    gpu_sha256=hashlib.sha256(gpu).hexdigest(),
                    stage_names=[f['value'] for f in current['stage_name_fields']],
                    group_names=[f['value'] for f in current['group_name_fields']])
    return _custom_identity().capture_gpu_identity(gpu, material_id)


def _restore_gpu_identity(bundle, gpu, material_id, snapshot, restored_id):
    _current_manifest(bundle, gpu, material_id)
    out = bytearray(gpu)
    for offset in bundle['manifest']['material_tag_offsets']:
        struct.pack_into('<I', out, offset, restored_id >> 32)
    for family, key in (('stage_name_fields', 'stage_names'), ('group_name_fields', 'group_names')):
        fields, values = bundle['manifest'][family], snapshot[key]
        if len(fields) != len(values) or len(set(values)) != len(values):
            raise ValueError('ultra identity restore name classes mismatch')
        for field, value in zip(fields, values):
            _uint(value)
            if not value:
                raise ValueError('ultra identity restore contains a zero name')
            for offset in field['offsets']:
                struct.pack_into('<I', out, offset, value)
    _current_manifest(bundle, out, restored_id)
    if hashlib.sha256(out).hexdigest() != snapshot.get('gpu_sha256'):
        raise ValueError('ultra identity restore does not reproduce the source GPU')
    return bytes(out)


def restore_gpu_identity(materials_dir, gpu, material_id, snapshot, restored_id):
    """Restore exact source identity, including records made before an upgrade."""
    if snapshot.get('kind') == 'custom_material_v179':
        return _custom_identity().restore_gpu_identity(gpu, material_id, snapshot, restored_id)
    if snapshot.get('schema_version') != 1 or int(snapshot['material_id'], 16) != restored_id:
        raise ValueError('ultra identity restore record mismatch')
    for bundle in _all_bundles(materials_dir):
        if bundle['name'] != snapshot['template_name']:
            continue
        if (snapshot.get('compatibility_id') is not None
                and bundle.get('compatibility_id') != snapshot['compatibility_id']):
            continue
        try:
            return _restore_gpu_identity(bundle, gpu, material_id, snapshot, restored_id)
        except (ValueError, KeyError, TypeError, struct.error):
            continue
    raise ValueError('ultra identity restore does not match a verified template revision')


def texture_import_hint(main, gpu, texture_index):
    """Advice comes from an exact verified revision, never schema inference."""
    if not gpu or len(gpu) < 8:
        return None
    old_id = (_u32(gpu, 4) << 32) | 1
    for bundle in _matching_bundles(_main_signature(main)):
        try:
            _current_manifest(bundle, gpu, old_id)
        except (ValueError, KeyError, TypeError):
            continue
        manifest = bundle['manifest']
        space = manifest.get('texture_color_spaces', {}).get(str(texture_index))
        lines = []
        if space == 'srgb':
            lines.append('按 sRGB 颜色制作；采样解码 RGB，Alpha 保持线性')
        elif space == 'linear':
            lines.append('按线性数据制作；导入保留通道值，不添加 sRGB 转换')
        if bundle['name'] != 'ultra' and texture_index in (1, 2):
            if manifest.get('pbr_input_contract') == 'squared_metal_with_sss_override':
                lines.append('金属 = M² × MetallicScale（M=RMO.G）；Nsv.B不参与金属度')
                lines.append('接管次表面散射：默认0；>=1启用')
                lines.append('关闭：SSS遮罩=M；启用：SSS遮罩=Nsv.B')
                lines.append('SSS初值 = (1-M) × 遮罩 × SubsurfaceIntensity；后续保留原金属/SSS门控')
            else:
                lines.append('旧版输入：金属按 RMO.G² × MetallicScale；Nsv.B只作SSS遮罩')
        return '\n'.join(lines) or None
    return None
