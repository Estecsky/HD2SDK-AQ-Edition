"""独立封包模式的纯数据规划器。

Blender 操作、AQSDK Toc 修改和文件写入都必须发生在本模块生成完整且通过验证的
计划之后。这样错误 archive、重复差分和 ID 冲突会在第一个游戏资源被修改前失败。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path


CATALOG_SCHEMA = "HD2ArmorPartIdCatalog1"
PLAN_SCHEMA = "HD2IndependentPackagePlan1"
MANIFEST_SCHEMA = "HD2DifferenceGroupManifest1"

PART_SLOTS = (
    "Hips",
    "LeftArm",
    "RightArm",
    "Torso",
    "Torso_Armor",
    "LeftLeg",
    "RightLeg",
    "Head",
)
BODY_PART_SLOTS = PART_SLOTS[:-1]

CONTENT_BODY = "BODY"
CONTENT_HELMET = "HELMET"

VARIANT_BASE = "BASE"
VARIANT_BODY_GROUP = "BODY_GROUP"
VARIANT_HELMET_SINGLE = "HELMET_SINGLE"
VARIANT_KINDS = (VARIANT_BASE, VARIANT_BODY_GROUP, VARIANT_HELMET_SINGLE)

PROP_PART_SLOT = "HD2BT_PartSlot"
PROP_VARIANT_KIND = "HD2BT_VariantKind"
PROP_BASE_GROUP = "HD2BT_BaseGroup"
PROP_DIFFERENCE_GROUP = "HD2BT_DifferenceGroup"
PROP_DIFFERENCE_NAME = "HD2BT_DifferenceName"


class IndependentPackagingError(ValueError):
    """封包计划不安全或信息不足。"""


@dataclass(frozen=True)
class ObjectMetadata:
    object_name: str
    part_slot: str
    variant_kind: str
    display_name: str


def load_part_catalog(path=None):
    if path is None:
        path = Path(__file__).parent / "armor_table" / "armor_part_unit_ids.json"
    path = Path(path)
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if catalog.get("schema") != CATALOG_SCHEMA:
        raise IndependentPackagingError(f"不支持的部位 ID 库：{catalog.get('schema')!r}")
    if tuple(catalog.get("part_slots", {}).keys()) != PART_SLOTS:
        raise IndependentPackagingError("部位 ID 库缺少或打乱了语义 PartSlot")
    return catalog


def _clean_name(value, label):
    value = str(value or "").strip()
    if not value:
        raise IndependentPackagingError(f"{label}不能为空")
    if len(value) > 64 or any(character in value for character in "\r\n\t"):
        raise IndependentPackagingError(f"{label}格式无效")
    return value


def _metadata_from_mapping(object_name, mapping):
    slot = str(mapping.get(PROP_PART_SLOT, "") or "").strip()
    kind = str(mapping.get(PROP_VARIANT_KIND, VARIANT_BASE) or "").strip()
    if slot not in PART_SLOTS:
        raise IndependentPackagingError(f"对象 {object_name} 没有有效的语义部位")
    if kind not in VARIANT_KINDS:
        raise IndependentPackagingError(f"对象 {object_name} 的输出类型无效：{kind}")
    if kind == VARIANT_BODY_GROUP:
        if slot not in BODY_PART_SLOTS:
            raise IndependentPackagingError(f"对象 {object_name} 把头盔加入了身体差分组")
        name = _clean_name(mapping.get(PROP_DIFFERENCE_GROUP), "身体差分组名称")
    elif kind == VARIANT_HELMET_SINGLE:
        if slot != "Head":
            raise IndependentPackagingError(f"对象 {object_name} 的头盔差分不是 Head 部位")
        name = _clean_name(mapping.get(PROP_DIFFERENCE_NAME), "头盔差分名称")
    else:
        name = _clean_name(mapping.get(PROP_BASE_GROUP) or "基础组", "基础组名称")
    return ObjectMetadata(str(object_name), slot, kind, name)


def _active_targets(catalog, active_unit_ids, slot):
    known = set(catalog["part_slots"][slot])
    return tuple(sorted(known.intersection(active_unit_ids), key=int))


def _content_domain(metadata):
    domains = {
        CONTENT_HELMET if row.part_slot == "Head" else CONTENT_BODY
        for row in metadata
    }
    if len(domains) > 1:
        raise IndependentPackagingError("请分别存储头和身体")
    return next(iter(domains))


def _custom_unit_id(project_id, archive_name, kind, display_name, slot, native_id):
    payload = "\0".join(
        ("HD2IR1", project_id, archive_name, kind, display_name, slot, native_id)
    ).encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=8, person=b"HD2IRUnit").digest()
    value = int.from_bytes(digest, "little")
    return str(value or 1)


def build_package_plan(
    project_name,
    archive_name,
    active_unit_ids,
    objects,
    catalog=None,
    project_id=None,
):
    """生成一次 archive 的确定性保存计划与运行时差分组 Manifest。

    ``active_unit_ids`` 必须只来自当前活动的基础游戏 archive。一个 PartSlot 在包中
    同时出现 Stocky/Slim 两个 Unit 时会全部返回；作者端没有体型选择。
    """

    catalog = catalog or load_part_catalog()
    project_name = _clean_name(project_name, "独立封包项目名称")
    project_id = _clean_name(project_id or project_name, "独立封包项目 ID")
    archive_name = _clean_name(archive_name, "活动 archive 名称")
    active_unit_ids = {str(int(value)) for value in active_unit_ids}
    all_known_ids = {
        unit_id
        for ids in catalog["part_slots"].values()
        for unit_id in ids
    }
    if not active_unit_ids.intersection(all_known_ids):
        raise IndependentPackagingError(
            "当前 archive 不包含已知护甲/头盔 Unit；独立封包模式拒绝写入非护甲 archive"
        )

    objects = list(objects)
    if any(mapping.get("HD2BT_DifferenceLogic", "GROUP") == "FREE"
           or mapping.get(PROP_VARIANT_KIND) == "FREE_PART" for _, mapping in objects):
        from .free_parts import build_free_plan
        return build_free_plan(project_name, archive_name, active_unit_ids, objects,
                               catalog, project_id)
    metadata = [_metadata_from_mapping(name, mapping) for name, mapping in objects]
    if not metadata:
        raise IndependentPackagingError("没有选中带语义部位的网格")
    content_domain = _content_domain(metadata)
    archive_domains = {
        CONTENT_HELMET if slot == 'Head' else CONTENT_BODY
        for slot, ids in catalog['part_slots'].items()
        if active_unit_ids.intersection(ids)
    }
    if content_domain not in archive_domains:
        selected_label = '头盔' if content_domain == CONTENT_HELMET else '身体'
        archive_label = '头盔' if archive_domains == {CONTENT_HELMET} else '身体'
        raise IndependentPackagingError(
            f"当前保存范围是{selected_label}，但加载的是{archive_label} Archive；"
            f"对象：{metadata[0].object_name}。若保存{archive_label}，请取消当前网格选择，"
            f"改选{archive_label}部位；若保存{selected_label}，请加载对应{selected_label} Archive。"
            "切换 Archive 不会自动切换所选网格"
        )

    body_members = {}
    helmet_names = {}
    base_members = {}
    base_names = set()
    for row in metadata:
        if row.variant_kind == VARIANT_BASE:
            base_names.add(row.display_name.casefold())
            if row.part_slot in base_members:
                raise IndependentPackagingError(
                    f"基础组的 {row.part_slot} 有两个对象："
                    f"{base_members[row.part_slot]}、{row.object_name}"
                )
            base_members[row.part_slot] = row.object_name
        elif row.variant_kind == VARIANT_BODY_GROUP:
            key = (row.display_name.casefold(), row.part_slot)
            if key in body_members:
                raise IndependentPackagingError(
                    f"身体差分组“{row.display_name}”的 {row.part_slot} 有两个差分对象："
                    f"{body_members[key]}、{row.object_name}"
                )
            body_members[key] = row.object_name
        elif row.variant_kind == VARIANT_HELMET_SINGLE:
            key = row.display_name.casefold()
            if key in helmet_names:
                raise IndependentPackagingError(
                    f"头盔差分“{row.display_name}”有两个对象："
                    f"{helmet_names[key]}、{row.object_name}"
                )
            helmet_names[key] = row.object_name
    if len(base_names) > 1:
        raise IndependentPackagingError("一个独立封包工程只能有一个具名基础组")
    if not base_members:
        raise IndependentPackagingError("没有指定基础组；默认显示必须来自基础组")
    for row in metadata:
        if row.variant_kind != VARIANT_BASE and row.part_slot not in base_members:
            raise IndependentPackagingError(
                f"差分对象 {row.object_name} 的 {row.part_slot} 在基础组中不存在"
            )

    native_groups = defaultdict(list)
    body_groups = defaultdict(list)
    helmets = []
    generated_ids = set()
    base_ids = {}
    for row in metadata:
        targets = _active_targets(catalog, active_unit_ids, row.part_slot)
        if not targets:
            raise IndependentPackagingError(
                f"当前 archive 中找不到 {row.part_slot} 对应 Unit；对象：{row.object_name}"
            )
        if row.variant_kind == VARIANT_BASE:
            for target in targets:
                native_groups[(row.part_slot, target)].append(row.object_name)
                # Archive files do not namespace Stingray FileIDs. A stock ID
                # here replaces every armour which shares that resource.
                # Naming the base group must not change its resource identity.
                generated = _custom_unit_id(
                    project_id, archive_name, VARIANT_BASE, "",
                    row.part_slot, target,
                )
                if generated in active_unit_ids or generated in generated_ids:
                    raise IndependentPackagingError("基础组独立 Unit ID 冲突，未修改 Patch")
                generated_ids.add(generated)
                base_ids[(row.part_slot, target)] = generated
            continue

        routes = []
        for target in targets:
            generated = _custom_unit_id(
                project_id,
                archive_name,
                row.variant_kind,
                row.display_name,
                row.part_slot,
                target,
            )
            if generated in active_unit_ids or generated in generated_ids:
                raise IndependentPackagingError(
                    f"为 {row.object_name} 生成的差分 Unit ID 冲突：{generated}"
                )
            generated_ids.add(generated)
            routes.append({"native_unit_id": target, "difference_unit_id": generated})
        member = {
            "part_slot": row.part_slot,
            "object_names": [row.object_name],
            "routes": routes,
        }
        if row.variant_kind == VARIANT_BODY_GROUP:
            body_groups[row.display_name].append(member)
        else:
            helmets.append({"name": row.display_name, **member})

    body_group_rows = []
    slots_by_group = {}
    for name in sorted(body_groups, key=str.casefold):
        members = sorted(body_groups[name], key=lambda row: PART_SLOTS.index(row["part_slot"]))
        slots = {member["part_slot"] for member in members}
        slots_by_group[name] = slots
        body_group_rows.append({"name": name, "members": members, "conflicts": []})
    for row in body_group_rows:
        row["conflicts"] = sorted(
            (
                other
                for other, slots in slots_by_group.items()
                if other != row["name"]
            ),
            key=str.casefold,
        )

    native = [
        {
            "part_slot": slot,
            "native_unit_id": target,
            "base_unit_id": base_ids[(slot, target)],
            "object_names": names,
        }
        for (slot, target), names in sorted(
            native_groups.items(), key=lambda row: (PART_SLOTS.index(row[0][0]), int(row[0][1]))
        )
    ]
    helmets.sort(key=lambda row: row["name"].casefold())
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "project_name": project_name,
        "project_id": project_id,
        "archive_name": archive_name,
        "base_group": {
            "name": next(row.display_name for row in metadata if row.variant_kind == VARIANT_BASE),
            "members": [
                {
                    "part_slot": row["part_slot"],
                    "native_unit_id": row["native_unit_id"],
                    "base_unit_id": row["base_unit_id"],
                }
                for row in native
            ],
        },
        "body_groups": body_group_rows,
        "helmet_differences": helmets,
        "body_group_policy": "SINGLE_GROUP_REPLACE_BASE",
        "helmet_policy": "SINGLE_SELECTION_WITH_DEFAULT",
    }
    return {
        "schema": PLAN_SCHEMA,
        "project_name": project_name,
        "project_id": project_id,
        "archive_name": archive_name,
        "content_domain": content_domain,
        "resource_target_policy": "PROJECT_ARCHIVE_ALIASES",
        "native_saves": native,
        "difference_manifest": manifest,
    }
