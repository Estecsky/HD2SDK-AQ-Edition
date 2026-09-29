"""Independent exports own resource IDs; ordinary AQ saves are not affected.

An Archive is a loading container, NOT a FileID namespace. Base meshes and
their mutable dependencies must be published under private IDs, and only the
declared equipment descriptors may select them. No blind binary search/replace.
"""
from copy import copy
import hashlib
import json
from pathlib import Path
import struct

from .independent_packaging import IndependentPackagingError
from .sdk_adapter import build_save_jobs
from . import material_packaging as materials

UNIT = 0xE0A48D0BE9A7453F
BONES = 0x18DEAD01056B72E9
PACKAGE = 0xAD9C6D9ED1E5E77A
POLICY = "PROJECT_ARCHIVE_ALIASES"


def private_id(project, kind, original, scope=""):
    data = f"HD2OwnedResource1\0{project}\0{scope}\0{kind}\0{int(original)}".encode("utf8")
    return int.from_bytes(hashlib.blake2b(data, digest_size=8, person=b"HD2Owned").digest(), "little") or 1


def load_binding_catalog():
    return json.loads(Path(__file__).with_name("resource_binding_targets.json").read_text(encoding="utf8"))


def binding_for_plan(plan, rig_profile_unit_ids=(), catalog=None):
    if plan.get("resource_target_policy") != POLICY:
        raise IndependentPackagingError("旧独立封包计划仍使用原版 ID，请重新保存独立部位")
    catalog = catalog or load_binding_catalog()
    archive = str(plan["archive_name"]).lower()
    target = catalog["targets"].get(archive)
    if target is None or target["domain"] != plan["content_domain"]:
        raise IndependentPackagingError("活动 Archive 未收录对应身体/头盔的独立引用边界，不能安全导出")
    from .free_parts import default_routes
    routes = default_routes(plan)
    routes.extend((int(row["native_unit_id"]), int(row["point_unit_id"])) for row in plan.get("armor_cleanup", ()))
    sources, destinations = [r[0] for r in routes], [r[1] for r in routes]
    if (not routes or len(routes) > 64 or len(set(sources)) != len(sources)
            or len(set(destinations)) != len(destinations) or set(sources) & set(destinations)
            or any(not 0 < value <= 0xffffffffffffffff for r in routes for value in r)):
        raise IndependentPackagingError("独立基础组路由重复、冲突或超出容量")
    if not target["equipment"]:
        raise IndependentPackagingError("Archive 没有经过核对的装备配置")
    for equipment in target["equipment"]:
        owned = {int(value, 16) for value in equipment["unit_ids"]}
        if not set(sources) <= owned:
            raise IndependentPackagingError("基础部位超出该 Archive 的装备引用范围")
    manifest_id = "owned_" + hashlib.blake2s(
        f"{plan['project_id']}\0{archive}".encode(), digest_size=12).hexdigest()
    lines = ["isolation_version=1", f"isolation_id={manifest_id}",
             "domain=" + ("body" if target["domain"] == "BODY" else "helmet"),
             f"archive=0x{int(archive,16):016X}"]
    for equipment in sorted(target["equipment"], key=lambda r:r["key"]):
        lines.append(f"equipment=0x{int(equipment['key'],16):08X}")
    for native, alias in sorted(routes):
        lines.append(f"unit=0x{native:016X},0x{alias:016X}")
    profiles = set(map(int, rig_profile_unit_ids))
    published = {int(job.published_unit_id) for job in build_save_jobs(plan)}
    if not profiles <= published:
        raise IndependentPackagingError("独立清单引用了不属于本计划的 Rig Profile")
    for unit in sorted(profiles):
        lines.append(f"rig_profile=0x{unit:016X}")
    return dict(file_name=manifest_id+".isolation.ini", text="\n".join(lines)+"\n",
                archive=archive, routes=routes, equipment=target["equipment"])


def append_package_dependencies(data, dependencies):
    """Retain every native dependency; append ONLY collision-free aliases."""
    if len(data) < 16:
        raise IndependentPackagingError("原版 Package 资源头不完整")
    count = struct.unpack_from("<I", data, 8)[0]
    if count > 1000000 or len(data) != 16 + 16 * count:
        raise IndependentPackagingError("原版 Package 资源索引越界")
    original = [struct.unpack_from("<QQ", data, 16 + i * 16) for i in range(count)]
    if len(set(original)) != len(original):
        raise IndependentPackagingError("原版 Package 包含重复依赖，无法安全扩展")
    added = sorted(set(dependencies) - set(original))
    if any(not 0 < kind <= 0xffffffffffffffff or not 0 < unit <= 0xffffffffffffffff for kind,unit in added):
        raise IndependentPackagingError("Package 新增依赖 ID 无效")
    result = bytearray(data)
    struct.pack_into("<I", result, 8, count + len(added))
    for kind, unit in added:
        result.extend(struct.pack("<QQ", kind, unit))
    assert result[16:16+16*count] == data[16:]
    return bytes(result)


def clone_entry(entry, file_id=None, toc=None):
    result = copy(entry)
    result.FileID = int(file_id if file_id is not None else entry.FileID)
    result.TocData = bytearray(toc if toc is not None else entry.TocData)
    result.GpuData = bytearray(entry.GpuData)
    result.StreamData = bytearray(entry.StreamData)
    # The output has already been serialized. Do not let a stale LoadedData
    # serialize original references back over these bounded field updates.
    result.IsLoaded = False
    result.LoadedData = None
    result.IsModified = False
    return result


def payload_fingerprint(entry):
    parts = materials.entry_payload(entry)
    return hashlib.sha256(struct.pack("<3Q", *(len(p) for p in parts)) + b"".join(parts)).hexdigest()


def rewrite_material_textures(data, mapping):
    materials.material_texture_ids(data) # Validate the entire table first.
    # This reader omits zero references, so obtain the real table count for
    # offsets; an empty slot must not shift later references.
    count = struct.unpack_from("<I", data, 64)[0]
    offset = 136 + count * 4
    result = bytearray(data)
    for index in range(count):
        source = struct.unpack_from("<Q", data, offset + index * 8)[0]
        if source in mapping:
            struct.pack_into("<Q", result, offset + index * 8, mapping[source])
    return bytes(result)


def isolate_material_entries(project, entries, resolve_stock):
    """One shared, project-owned material namespace, across body and helmet.

    Authoring IDs and pixel/GPU bytes are unchanged; only output FileIDs and
    material texture-reference fields differ. Private copies also avoid the
    same hand-authored custom ID colliding between unrelated Mod projects.
    """
    indexed = materials.index_entries(entries, only_materials=True)
    aliases, targets = {}, set()
    for key in indexed:
        alias = private_id(project, key[1], key[0])
        if alias == key[0] or (alias, key[1]) in targets or resolve_stock(alias, key[1]) is not None:
            raise IndependentPackagingError("独立材质/贴图 ID 冲突，未写出")
        aliases[key] = alias
        targets.add((alias, key[1]))
    if targets & set(indexed):
        raise IndependentPackagingError("材质/贴图独立 ID 形成引用链，未写出")
    textures = {source:alias for (source,kind),alias in aliases.items() if kind == materials.TEXTURE_TYPE}
    output, records = [], []
    for key, entry in sorted(indexed.items()):
        toc = rewrite_material_textures(entry.TocData, textures) if key[1] == materials.MATERIAL_TYPE else bytes(entry.TocData)
        output.append(clone_entry(entry, aliases[key], toc))
        records.append(dict(source=f"{key[0]:016x}",type=f"{key[1]:016x}",published=f"{aliases[key]:016x}",source_sha256=payload_fingerprint(entry)))
    return output, records


def restore_authoring_material_entries(project, entries, records):
    """Verified inverse for UI lookup and the next save; never change the file."""
    if not records:
        return list(entries) # Migration from the earlier byte-preserving pack.
    indexed = materials.index_entries(entries, only_materials=True)
    reverse = {}
    source_keys = set()
    for row in records:
        source, kind, published = (int(row[k],16) for k in ("source","type","published"))
        key = published, kind
        if (kind not in materials.RESOURCE_TYPES or key in reverse or (source,kind) in source_keys
                or published != private_id(project,kind,source) or key not in indexed):
            raise IndependentPackagingError("独立材质包身份映射损坏")
        reverse[key] = source
        source_keys.add((source,kind))
    if set(reverse) != set(indexed):
        raise IndependentPackagingError("独立材质包身份映射不完整")
    textures = {published:source for (published,kind),source in reverse.items() if kind == materials.TEXTURE_TYPE}
    restored = {}
    for key, entry in indexed.items():
        toc = rewrite_material_textures(entry.TocData,textures) if key[1] == materials.MATERIAL_TYPE else entry.TocData
        restored[key] = clone_entry(entry, reverse[key],toc)
    for row in records:
        key = int(row['published'],16),int(row['type'],16)
        if payload_fingerprint(restored[key]) != row['source_sha256']:
            raise IndependentPackagingError("独立材质包反向回读与制作数据摘要不符")
    return list(restored.values())


def scope_model_entries(plan, entries):
    """Select the saved model closure on an output copy, with an omission audit.

    Authoring patches may retain old native saves, earlier aliases, or another
    domain. Do not delete these from the user's Patch or publish them globally.
    Missing planned models and malformed/duplicate inputs remain hard failures.
    """
    indexed = materials.index_entries(entries)
    expected = {int(j.published_unit_id) for j in build_save_jobs(plan)}
    expected.update(int(r['point_unit_id']) for r in plan.get('armor_cleanup', ()))
    actual = {file_id for file_id, kind in indexed if kind == UNIT}
    missing = expected - actual
    if missing:
        raise IndependentPackagingError('当前 Patch 缺少本次计划模型：' +
            '、'.join(f'{value:016x}' for value in sorted(missing)) + '；请重新保存部位/差分')
    bones = set()
    for (file_id, kind), entry in indexed.items():
        if kind != UNIT:
            continue
        materials.unit_material_ids(entry.TocData)
        if file_id in expected:
            bones.add(struct.unpack_from('<Q', entry.TocData, 8)[0])
    kept, omitted = [], []
    for (file_id, kind), entry in indexed.items():
        if (kind == UNIT and file_id not in expected) or (kind == BONES and file_id not in bones):
            omitted.append(dict(id=f'{file_id:016x}', type=f'{kind:016x}',
                                reason='UNPLANNED_UNIT' if kind == UNIT else 'UNREFERENCED_BONES'))
        else:
            kept.append(entry)
    return kept, sorted(omitted, key=lambda row:(row['type'], row['id']))


def isolate_model_entries(plan, entries, resolve_stock, *, material_aliases=None):
    """Create an output-only closure. Caller Patch and source Blend stay intact.

    Extra unchanged stock resources are omitted. Modified stock animation /
    state-machine resources require a future understood closure and fail here,
    rather than leaking global replacements into an independent export.
    """
    binding_for_plan(plan)
    indexed = materials.index_entries(entries)
    jobs = build_save_jobs(plan)
    expected = {int(j.published_unit_id) for j in jobs}
    expected.update(int(r["point_unit_id"]) for r in plan.get("armor_cleanup", ()))
    if {key[0] for key in indexed if key[1] == UNIT} != expected:
        raise IndependentPackagingError("独立 Patch 含未归属计划的模型或旧原版替换，请重新保存到独立 Patch")
    project, archive = str(plan["project_id"]), str(plan["archive_name"]).lower()
    mutable_bones = {}
    result = {}
    for key, entry in indexed.items():
        if key[1] == UNIT or key[1] in materials.RESOURCE_TYPES:
            continue
        stock = resolve_stock(*key)
        if stock is not None and materials.entry_payload(stock) == materials.entry_payload(entry):
            continue
        if key[1] != BONES:
            raise IndependentPackagingError(f"资源 {key[0]:016x}/{key[1]:016x} 尚无安全独立引用转换；普通存包不受影响")
        alias = private_id(project, BONES, key[0], archive)
        if alias == key[0] or resolve_stock(alias, BONES) is not None or (alias, BONES) in result:
            raise IndependentPackagingError("独立骨骼表 ID 冲突")
        mutable_bones[key[0]] = alias
        result[(alias, BONES)] = clone_entry(entry, alias)
    used_bones = set()
    for unit_id in sorted(expected):
        if resolve_stock(unit_id, UNIT) is not None:
            raise IndependentPackagingError("生成的独立模型 ID 与原版资源冲突")
        entry = indexed[(unit_id, UNIT)]
        payload = bytearray(entry.TocData)
        # unit_material_ids checks the entire table bounds before any write.
        mat_ids = materials.unit_material_ids(payload)
        bones = struct.unpack_from("<Q", payload, 8)[0]
        if bones in mutable_bones:
            struct.pack_into("<Q", payload, 8, mutable_bones[bones])
            used_bones.add(bones)
        if material_aliases:
            at = struct.unpack_from("<I", payload, 0x70)[0] + 4 + len(mat_ids) * 4
            for index, material_id in enumerate(mat_ids):
                struct.pack_into("<Q", payload, at + 8 * index, material_aliases.get(material_id, material_id))
        result[(unit_id, UNIT)] = clone_entry(entry, toc=payload)
    if used_bones != set(mutable_bones):
        raise IndependentPackagingError("独立 Patch 含未被本次模型引用的修改骨骼表")
    native_package = resolve_stock(int(archive, 16), PACKAGE)
    if native_package is None:
        raise IndependentPackagingError("找不到该 Archive 的原版 Package 依赖，不能仅导出无引用的模型")
    package = append_package_dependencies(bytes(native_package.TocData),
                                          [(kind, file_id) for file_id,kind in result])
    result[(int(archive,16), PACKAGE)] = clone_entry(native_package, toc=package)
    return [result[key] for key in sorted(result, key=lambda k:(k[1],k[0]))]
