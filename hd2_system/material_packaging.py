"""Shared material delivery: byte-preserving resources and guarded file writes.

No Blender dependency. Authoring Patch entries are never removed or re-saved;
only final model exports filter these types. A material pack owns its manifest,
not every file starting with the central archive's name.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile
import uuid

ARCHIVE_NAME = "9ba626afa44a3aa3"
MATERIAL_TYPE = 16915718763308572383
TEXTURE_TYPE = 14790446551990181426
UNIT_TYPE = 16187218042980615487
RESOURCE_TYPES = frozenset((MATERIAL_TYPE, TEXTURE_TYPE))
PACK_SCHEMA = "HD2SharedMaterialPack1"
PACK_SUFFIXES = ("", ".gpu_resources", ".stream")
MANIFEST_SUFFIX = ".hd2mat.json"


class MaterialPackagingError(ValueError):
    pass


def _uint64(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 0xffffffffffffffff:
        raise MaterialPackagingError("材质资源包含无效 ID")
    return value


def entry_key(entry):
    return _uint64(entry.FileID), _uint64(entry.TypeID)


def entry_payload(entry):
    return bytes(entry.TocData), bytes(entry.GpuData), bytes(entry.StreamData)


def index_entries(entries, *, only_materials=False):
    result = {}
    for entry in entries:
        key = entry_key(entry)
        if only_materials and key[1] not in RESOURCE_TYPES:
            continue
        if key in result:
            raise MaterialPackagingError(f"包内出现重复资源 {key[0]:016x}/{key[1]:016x}")
        if not entry.TocData:
            raise MaterialPackagingError(f"资源 {key[0]:016x} 没有 Toc 数据")
        result[key] = entry
    return result


def material_texture_ids(data):
    if len(data) < 136:
        raise MaterialPackagingError("材质数据头不完整")
    count = struct.unpack_from("<I", data, 64)[0]
    if count > 4096 or 136 + 12 * count > len(data):
        raise MaterialPackagingError("材质贴图索引越过数据边界")
    return tuple(v for v in struct.unpack_from(f"<{count}Q", data, 136 + 4 * count) if v)


def unit_material_ids(data):
    if len(data) < 0x74:
        raise MaterialPackagingError("Unit 数据头不完整")
    offset = struct.unpack_from("<I", data, 0x70)[0]
    if offset < 0x74 or offset + 4 > len(data):
        raise MaterialPackagingError("Unit 材质表越过数据边界")
    count = struct.unpack_from("<I", data, offset)[0]
    if count > 65536 or offset + 4 + 12 * count > len(data):
        raise MaterialPackagingError("Unit 材质索引越过数据边界")
    return tuple(struct.unpack_from(f"<{count}Q", data, offset + 4 + 4 * count))


def delivered_material_ids(manifest):
    """Actual on-disk identities, not restored legacy authoring aliases."""
    resources = manifest.get('resources')
    if not isinstance(resources, list):
        raise MaterialPackagingError('材质清单缺少实际资源列表')
    result = set()
    for resource in resources:
        try:
            if not isinstance(resource, str) or not re.fullmatch(r'[0-9a-f]{16}:[0-9a-f]{16}', resource):
                raise ValueError()
            file_id, kind = (int(value, 16) for value in resource.split(':'))
            _uint64(file_id)
        except (ValueError, TypeError) as error:
            raise MaterialPackagingError('材质清单的实际资源身份无效') from error
        if kind == MATERIAL_TYPE:
            result.add(file_id)
    return sorted(result)


def external_model_material_ids(entries, resolve_stock, linked_material_ids=()):
    """Unverified external dependencies, not proof that a material is missing.

    The game resolves resource IDs across installed archives. A model-only
    export may intentionally depend on a separately authored material pack
    without a Blender-side pack record. Preserve those IDs; never fabricate a
    resource or infer that an unavailable external pack has been verified.
    """
    linked = {_uint64(value) for value in linked_material_ids}
    indexed = index_entries(entries)
    referenced = {
        _uint64(material_id)
        for (_file_id, type_id), entry in indexed.items()
        if type_id == UNIT_TYPE
        for material_id in unit_material_ids(entry.TocData)
    }
    textures = {file_id for file_id,kind in indexed if kind == TEXTURE_TYPE}
    external = set()
    for material_id in referenced - linked:
        if (material_id,MATERIAL_TYPE) in indexed:
            external.add(material_id)
            continue
        stock = resolve_stock(material_id, MATERIAL_TYPE)
        if stock is None or (textures and textures.intersection(material_texture_ids(stock.TocData))):
            external.add(material_id)
    return sorted(external)


def collect_material_entries(current, previous=(), resolve_texture=None, *, external_refs=None):
    """All current material/texture entries; merge previous SAME-project pack.

    Explicit current edits win. Referenced textures are included, even when
    borrowed from a base archive, but unrelated base-archive assets are not.
    """
    merged = index_entries(previous, only_materials=True)
    merged.update(index_entries(current, only_materials=True))
    if not merged:
        raise MaterialPackagingError("当前 Patch 没有已保存的材质或贴图")
    for (file_id, type_id), entry in list(merged.items()):
        if type_id != MATERIAL_TYPE:
            continue
        for texture_id in material_texture_ids(entry.TocData):
            key = texture_id, TEXTURE_TYPE
            if key not in merged:
                found = resolve_texture(texture_id) if resolve_texture is not None else None
                if found is None and external_refs is not None:
                    external_refs.add(texture_id)
                    continue
                if found is None or entry_key(found) != key or not found.TocData:
                    raise MaterialPackagingError(f"材质 {file_id:016x} 引用的贴图 {texture_id:016x} 缺失；未写出")
                merged[key] = found
    return [merged[key] for key in sorted(merged, key=lambda k:(k[1], k[0]))]


def external_texture_ids(entries):
    indexed = index_entries(entries, only_materials=True)
    return sorted({texture_id for key, entry in indexed.items() if key[1] == MATERIAL_TYPE
                   for texture_id in material_texture_ids(entry.TocData)
                   if (texture_id, TEXTURE_TYPE) not in indexed})


def read_archive_payloads(path, *, material_only=False):
    path = Path(path)
    blobs = [Path(str(path) + suffix).read_bytes() for suffix in PACK_SUFFIXES]
    toc, gpu, stream = blobs
    if len(toc) < 72 or struct.unpack_from("<I", toc)[0] != 0xf0000011:
        raise MaterialPackagingError("不是有效 Archive")
    type_count, file_count = struct.unpack_from("<II", toc, 4)
    table_end = 72 + 32 * type_count + 80 * file_count
    if type_count > 4096 or file_count > 1000000 or table_end > len(toc):
        raise MaterialPackagingError("Archive 索引越界")
    declared = {}
    for i in range(type_count):
        kind, count = struct.unpack_from("<QQ", toc, 72 + 32 * i + 8)
        if kind in declared:
            raise MaterialPackagingError("Archive 类型重复")
        declared[kind] = count
    result = {}
    for i in range(file_count):
        at = 72 + 32 * type_count + 80 * i
        file_id, type_id, to, so, go = struct.unpack_from("<5Q", toc, at)
        ts, ss, gs = struct.unpack_from("<3I", toc, at + 56)
        key = _uint64(file_id), _uint64(type_id)
        if key in result or material_only and type_id not in RESOURCE_TYPES:
            raise MaterialPackagingError("Archive 出现重复资源或材质包包含其他资源类型")
        for offset, size, blob, minimum in ((to, ts, toc, table_end), (go, gs, gpu, 0), (so, ss, stream, 0)):
            if size and (offset < minimum or offset + size > len(blob)):
                raise MaterialPackagingError("Archive 资源数据越界")
        if not ts:
            raise MaterialPackagingError("Archive 资源缺少 Toc 数据")
        result[key] = toc[to:to+ts], gpu[go:go+gs], stream[so:so+ss]
    if Counter(key[1] for key in result) != declared:
        raise MaterialPackagingError("Archive 类型计数不一致")
    return result


def _sha(path):
    with Path(path).open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        return digest.hexdigest()


def validate_pack(path, project_id, *, manifest=None):
    path = Path(path)
    if not re.fullmatch(ARCHIVE_NAME + r"\.patch_\d+", path.name):
        raise MaterialPackagingError("独立材质包名称必须使用 9ba 基础 Archive")
    if manifest is None:
        manifest = json.loads(Path(str(path) + MANIFEST_SUFFIX).read_text(encoding="utf-8"))
    if manifest.get("schema") != PACK_SCHEMA or manifest.get("project_id") != project_id or manifest.get("archive") != path.name:
        raise MaterialPackagingError("材质包不属于当前项目或清单名称不符；不会覆盖")
    if set(manifest.get("files", {})) != set(PACK_SUFFIXES):
        raise MaterialPackagingError("材质包文件清单不完整")
    for suffix, digest in manifest["files"].items():
        if _sha(str(path) + suffix) != digest:
            raise MaterialPackagingError("材质包已被外部修改；请恢复已验证备份后再保存，不自动覆盖")
    payloads = read_archive_payloads(path, material_only=True)
    expected = {f"{key[0]:016x}:{key[1]:016x}" for key in payloads}
    if expected != set(manifest.get("resources", ())):
        raise MaterialPackagingError("材质包资源清单与实际数据不符")
    external = {t for (_, kind), values in payloads.items() if kind == MATERIAL_TYPE
                for t in material_texture_ids(values[0]) if (t, TEXTURE_TYPE) not in payloads}
    declared_external = manifest.get('external_textures', [])
    if not isinstance(declared_external, list) or set(declared_external) != {f'{t:016x}' for t in external}:
        raise MaterialPackagingError("材质包缺少贴图且未完整声明外部依赖")
    return manifest, payloads


def next_pack_path(directory, occupied=()):
    """Reserve 0/1 for the demo's boot/registration; never overwrite by name."""
    directory = Path(directory)
    names = {p.name for p in directory.iterdir()} if directory.exists() else set()
    names.update(str(name) for name in occupied)
    for index in range(2, 100000):
        name = f"{ARCHIVE_NAME}.patch_{index}"
        if not any(n == name or n.startswith(name + ".") for n in names):
            return directory / name
    raise MaterialPackagingError("无法分配材质包序号")


def write_pack(path, entries, project_id, writer, *, external_refs=(), resource_aliases=(),
               emit_manifest=True, previous_manifest=None):
    """Stage and round-trip before writing; backup and restore this pack only."""
    path = Path(path).absolute()
    if not re.fullmatch(r"[0-9a-f]{32}", project_id):
        raise MaterialPackagingError("独立项目 UUID 无效")
    if not re.fullmatch(ARCHIVE_NAME + r"\.patch_\d+", path.name):
        raise MaterialPackagingError("独立材质包文件名无效")
    selected = index_entries(entries)
    if not selected or any(k[1] not in RESOURCE_TYPES for k in selected):
        raise MaterialPackagingError("独立材质包只允许材质和贴图")
    external = external_texture_ids(selected.values())
    if set(external_refs) != set(external):
        raise MaterialPackagingError("缺少贴图时必须明确记录全部外部引用；未写出")
    # Old authoring sidecars remain supported and are updated, never deleted.
    # New player exports keep this metadata in the Blend project instead.
    sidecar = Path(str(path) + MANIFEST_SUFFIX)
    suffixes = (*PACK_SUFFIXES, MANIFEST_SUFFIX) if emit_manifest or sidecar.exists() else PACK_SUFFIXES
    targets = [Path(str(path) + s) for s in suffixes]
    exists = any(p.exists() for p in targets)
    if exists:
        validate_pack(path, project_id, manifest=previous_manifest)
    if any(p.is_symlink() for p in targets) or path.parent.is_symlink():
        raise MaterialPackagingError("独立材质包不覆盖链接目标")
    path.parent.mkdir(parents=True, exist_ok=True)
    stage_dir = Path(tempfile.mkdtemp(prefix=".hd2mat-stage-", dir=path.parent))
    stage = stage_dir / path.name
    changed = []
    backup = None
    try:
        writer(stage, list(selected.values()))
        payloads = read_archive_payloads(stage, material_only=True)
        if payloads != {k:entry_payload(v) for k, v in selected.items()}:
            raise MaterialPackagingError("材质包最终回读与制作数据不一致")
        manifest = dict(schema=PACK_SCHEMA, archive=path.name, project_id=project_id,
                        files={s:_sha(str(stage)+s) for s in PACK_SUFFIXES},
                        resources=sorted(f"{k[0]:016x}:{k[1]:016x}" for k in payloads),
                        materials=sum(k[1]==MATERIAL_TYPE for k in payloads),
                        textures=sum(k[1]==TEXTURE_TYPE for k in payloads),
                        external_textures=[f'{t:016x}' for t in external],
                        resource_aliases=list(resource_aliases))
        Path(str(stage)+MANIFEST_SUFFIX).write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        validate_pack(stage, project_id)
        if exists:
            # Revalidate immediately before replacing; externally changed files
            # must not be captured as an implicit author-approved new baseline.
            old_manifest, _ = validate_pack(path, project_id, manifest=previous_manifest)
            backup = path.parent / ".hd2mat-backups" / uuid.uuid4().hex
            backup.mkdir(parents=True)
            for target in targets:
                if target.exists():
                    shutil.copy2(target, backup / target.name)
            for suffix, digest in old_manifest['files'].items():
                if _sha(backup / (path.name+suffix)) != digest:
                    raise MaterialPackagingError("材质包备份校验失败")
        for target in targets:
            source = stage_dir / target.name
            if exists:
                os.replace(source, target)
            else:
                # Same-volume hard link is atomic and refuses an existing name.
                # An existence check followed by replace would race other saves.
                os.link(source, target)
            changed.append(target)
        validate_pack(path, project_id, manifest=manifest)
        return manifest
    except BaseException:
        for target in reversed(changed):
            if backup is not None and (backup / target.name).exists():
                shutil.copy2(backup / target.name, target)
            else:
                target.unlink()
        raise
    finally:
        # Only this function's exact, newly allocated stage directory.
        assert stage_dir.resolve().parent == path.parent.resolve()
        shutil.rmtree(stage_dir)
