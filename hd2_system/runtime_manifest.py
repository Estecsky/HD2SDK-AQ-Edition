"""Compile AQSDK's semantic body groups into runtime difference Manifest v4.

Authors name a base group and sparse body groups. Runtime identities stay an
implementation detail: one semantic PartSlot is expanded to every matching
native resource variant in the active archive, and each group member is
expanded to the corresponding generated Unit alias.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


CATALOG_SCHEMA = "HD2RuntimeEquipmentCatalog1"
OUTPUT_SCHEMA = "HD2RuntimeDifferenceManifest4"
PLAN_SCHEMA = "HD2IndependentPackagePlan1"
BODY_SLOTS = ("Hips", "LeftArm", "RightArm", "Torso", "Torso_Armor", "LeftLeg", "RightLeg")
_ARCHIVE_ID = re.compile(r"(?i)(?<![0-9a-f])([0-9a-f]{16})(?![0-9a-f])")


class RuntimeManifestError(ValueError):
    """The authored plan cannot produce one safe runtime Manifest v4."""


def load_runtime_target_catalog(path=None):
    path = Path(path) if path is not None else (
        Path(__file__).parent / "armor_table" / "armor_runtime_targets.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != CATALOG_SCHEMA:
        raise RuntimeManifestError("不支持的运行时护甲目标库")
    if not isinstance(document.get("targets"), dict):
        raise RuntimeManifestError("运行时护甲目标库缺少 targets")
    return document


def _safe_display_name(value, label):
    value = str(value or "").strip()
    encoded = value.encode("utf-8")
    if (
        not value
        or len(encoded) >= 128
        or any(ord(character) < 0x20 for character in value)
        or "#" in value
        or ";" in value
    ):
        raise RuntimeManifestError(f"{label}不能写入运行时清单")
    return value


def _stable_label(prefix, *values, digits=12):
    payload = "\0".join(str(value) for value in values).encode("utf-8")
    digest = hashlib.blake2s(payload, digest_size=16, person=b"HD2DfV4").hexdigest()
    return f"{prefix}_{digest[:digits]}"


def _archive_package(value):
    matches = {match.lower() for match in _ARCHIVE_ID.findall(str(value or ""))}
    if len(matches) != 1:
        raise RuntimeManifestError(
            f"无法从活动 Archive 名称识别唯一的 16 位包 ID：{value!r}"
        )
    return next(iter(matches))


def _hex64(value):
    numeric = int(value)
    if numeric <= 0 or numeric > 0xFFFFFFFFFFFFFFFF:
        raise RuntimeManifestError(f"Unit ID 超出 uint64：{value!r}")
    return f"0x{numeric:016X}"


def build_runtime_difference_manifest(plan, rig_profile_unit_ids=(), catalog=None):
    """Return a deterministic Manifest v4 document, or ``None`` without body groups."""

    if plan.get("schema") != PLAN_SCHEMA:
        raise RuntimeManifestError("不支持的独立封包计划")
    authored = plan.get("difference_manifest")
    if not isinstance(authored, dict):
        raise RuntimeManifestError("独立封包计划缺少差分清单")
    if authored.get('difference_logic') == 'FREE':
        from .free_runtime_manifest import build_free_runtime_manifest
        # v6 uses the verified resource binding catalog, not the v4 appearance catalog.
        return build_free_runtime_manifest(plan, rig_profile_unit_ids)
    authored_groups = tuple(authored.get("body_groups", ()))
    cleanup = () if plan.get("resource_target_policy") == "PROJECT_ARCHIVE_ALIASES" else tuple(plan.get("armor_cleanup", ()))
    if not authored_groups and not cleanup:
        return None

    default_catalog = catalog is None
    catalog = catalog or load_runtime_target_catalog()
    package = _archive_package(plan.get("archive_name"))
    target = catalog["targets"].get(package)
    if target is None and default_catalog and plan.get("resource_target_policy") == "PROJECT_ARCHIVE_ALIASES":
        # The old player-facing appearance list collapses NPC and other
        # equivalent-looking configurations. Independent saves use the full,
        # explicitly verified equipment closure, including those Archives.
        from .resource_isolation import load_binding_catalog
        binding = load_binding_catalog()["targets"].get(package)
        if binding and binding["domain"] == "BODY" and len(binding["equipment"]) == 1:
            target = {"equipment_key": binding["equipment"][0]["key"]}
    if target is None:
        raise RuntimeManifestError(
            "活动 Archive 尚未收录运行时 equipment key；请更新本地目标库"
        )
    try:
        appearance_key = int(str(target["equipment_key"]), 16)
        carrier_key = int(str(catalog["carrier_equipment_key"]), 16)
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeManifestError("运行时目标库的载体或外观 equipment key 无效") from error
    if (
        appearance_key <= 0
        or appearance_key > 0xFFFFFFFF
        or carrier_key <= 0
        or carrier_key > 0xFFFFFFFF
    ):
        raise RuntimeManifestError("运行时目标库的 equipment key 超出 uint32")

    base_group = authored.get("base_group")
    if not isinstance(base_group, dict):
        raise RuntimeManifestError("差分清单缺少基础组")
    base_display = _safe_display_name(base_group.get("name"), "基础组名称")
    project_id = str(plan.get("project_id") or plan.get("project_name") or "project")
    manifest_id = _stable_label("hd2", project_id, package, digits=16)
    base_label = _stable_label("base", project_id, base_display, digits=10)

    native_by_slot_target = {}
    for row in plan.get("native_saves", ()):
        slot = str(row.get("part_slot", ""))
        if slot not in BODY_SLOTS:
            continue
        target_id = str(int(row.get("native_unit_id")))
        key = (slot, target_id)
        if key in native_by_slot_target:
            raise RuntimeManifestError(f"基础组目标重复：{slot} / {target_id}")
        native_by_slot_target[key] = row

    groups = []
    routed = {}
    group_labels = set()
    for source_group in sorted(
        authored_groups, key=lambda row: str(row.get("name", "")).casefold()
    ):
        display = _safe_display_name(source_group.get("name"), "身体差分组名称")
        group_label = _stable_label("g", project_id, display, digits=12)
        if group_label in group_labels:
            raise RuntimeManifestError("身体差分组稳定标签冲突")
        group_labels.add(group_label)
        members = []
        member_slots = set()
        for member in source_group.get("members", ()):
            slot = str(member.get("part_slot", ""))
            if slot not in BODY_SLOTS or slot in member_slots:
                raise RuntimeManifestError(
                    f"身体差分组“{display}”包含重复或无效部位：{slot}"
                )
            member_slots.add(slot)
            routes = tuple(member.get("routes", ()))
            if not routes:
                raise RuntimeManifestError(f"身体差分组“{display}”的 {slot} 没有目标")
            for route in routes:
                native = str(int(route.get("native_unit_id")))
                difference = str(int(route.get("difference_unit_id")))
                key = (slot, native)
                if key not in native_by_slot_target:
                    raise RuntimeManifestError(
                        f"身体差分组“{display}”的 {slot} 缺少基础组目标 {native}"
                    )
                if (group_label, key) in routed:
                    raise RuntimeManifestError(
                        f"身体差分组“{display}”重复路由 {slot} / {native}"
                    )
                option_label = _stable_label(
                    "d", project_id, display, slot, native, digits=14
                )
                routed[(group_label, key)] = (difference, option_label)
                members.append((key, option_label))
        groups.append({
            "label": group_label,
            "display_name": display,
            "members": tuple(members),
        })

    routed_targets = {key for _, key in routed}
    profile_units = {int(value) for value in rig_profile_unit_ids}
    part_rows = []
    part_name_by_key = {}
    option_rows_by_key = {}
    for slot, native in sorted(
        routed_targets, key=lambda row: (BODY_SLOTS.index(row[0]), int(row[1]))
    ):
        key = (slot, native)
        part_name = _stable_label("p", slot, native, digits=12)
        base_option = _stable_label("b", slot, native, digits=12)
        base_unit = str(native_by_slot_target[key].get("base_unit_id", native))
        options = [(base_option, base_unit)]
        for group in groups:
            route = routed.get((group["label"], key))
            if route is not None:
                difference, option_label = route
                options.append((option_label, difference))
        if len(options) < 2:
            raise RuntimeManifestError(f"运行时部位 {slot} 没有差分选项")
        part_name_by_key[key] = part_name
        option_rows_by_key[key] = options
        # The isolated default descriptor already points at this base alias.
        # Difference selection must replace that owned row, never the shared
        # original ID still used by unrelated armour descriptors.
        part_rows.append((part_name, base_unit, options))

    if len(part_rows) + len(cleanup) > 32:
        raise RuntimeManifestError("身体差分与独立甲片路由超过运行时 32 个槽位限制，未修改 Patch")
    lines = [
        "manifest_version=5" if cleanup else "manifest_version=4",
        f"manifest_id={manifest_id}",
        f"body_equipment_key=0x{carrier_key:08X}",
        f"body_appearance_key=0x{appearance_key:08X}",
        f"body_base_group={base_label}",
        f"body_base_group_display_name={base_label},{base_display}",
        "",
    ]
    rig_rows = []
    if cleanup:
        lines.append(f"body_archive=0x{package.upper()}")
    cleanup_targets = set()
    published_cleanup = set()
    for row in cleanup:
        native = int(row["native_unit_id"])
        alias = int(row["point_unit_id"])
        if (native in cleanup_targets or alias in published_cleanup or native == alias
                or native not in {int(value, 16) for value in target["unit_ids"]}
                or any(int(value[1]) == native for value in native_by_slot_target)):
            raise RuntimeManifestError("独立甲片清理路由重复、越界或覆盖已保存部位")
        cleanup_targets.add(native)
        published_cleanup.add(alias)
        lines.append(f"body_point={_stable_label('point', native)},{_hex64(native)},{_hex64(alias)}")
    if cleanup:
        lines.append("")
    for part_name, native, options in part_rows:
        lines.append(f"body_part={part_name},{_hex64(native)},0x4,0")
        for option_label, published in options:
            lines.append(
                f"body_option={part_name},{option_label},{_hex64(native)},{_hex64(published)}"
            )
            if int(published) in profile_units:
                rig_rows.append(f"rig_profile=body,{part_name},{option_label}")
        lines.append("")

    for group in groups:
        lines.append(f"body_group={group['label']}")
        lines.append(
            f"body_group_display_name={group['label']},{group['display_name']}"
        )
        for key, option_label in group["members"]:
            lines.append(
                f"body_group_member={group['label']},{part_name_by_key[key]},{option_label}"
            )
        lines.append("")
    lines.extend(rig_rows)
    if rig_rows:
        lines.append("")
    text = "\n".join(lines)
    return {
        "schema": "HD2RuntimeDifferenceManifest5" if cleanup else OUTPUT_SCHEMA,
        "manifest_id": manifest_id,
        "archive_package": package,
        "carrier_equipment_key": f"{carrier_key:08x}",
        "appearance_equipment_key": f"{appearance_key:08x}",
        "file_name": f"{manifest_id}.difference.ini",
        "text": text,
        "body_parts": len(part_rows) + len(cleanup),
        "body_groups": len(groups),
    }


__all__ = (
    "RuntimeManifestError",
    "build_runtime_difference_manifest",
    "load_runtime_target_catalog",
)
