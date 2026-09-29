"""AQSDK 内置的整甲物理编译器。

输入全部来自当前 Blend、独立封包计划和刚写入 Patch 的 Unit 快照；输出仍使用
运行时既有 HD2IRG1/HD2PHY1，不引入新的游戏端格式。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

from .physics_packaging import build_armor_physics_project, published_unit_rows, consumer_bones_by_part
from .rig_format import RigFormatError, build as build_rig, rest_globals


MANIFEST_SCHEMA = "HD2IntegratedPhysicsPack1"
_ARCHIVE_ID = re.compile(r"(?i)(?<![0-9a-f])([0-9a-f]{16})(?![0-9a-f])")


class PhysicsCompileError(ValueError):
    """整甲物理项目无法安全展开为逐 Unit 配置。"""


def _canonical_json(value):
    return (json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n").encode("utf-8")


def _sha256(value):
    return hashlib.sha256(value).hexdigest()


def _unit_number(value, where):
    try:
        text = str(value).strip().lower().removeprefix("0x")
        base = 16 if len(text) == 16 or any(character in "abcdef" for character in text) else 10
        number = int(text, base)
    except (TypeError, ValueError) as error:
        raise PhysicsCompileError(f"{where} 的 Unit ID 无效") from error
    if number <= 0 or number > 0xFFFFFFFFFFFFFFFF:
        raise PhysicsCompileError(f"{where} 的 Unit ID 超出 uint64")
    return number


def _runtime_stem(plan):
    """Return a collision-free stem for one project/archive compilation.

    Body and helmet are deliberately saved as separate Archive transactions,
    but they share one authoring project UUID.  Naming runtime payloads from
    only that UUID makes the second installed pack overwrite the first one.
    """

    project_id = str(plan.get("project_id", "")).strip()
    if not project_id:
        raise PhysicsCompileError("独立封包计划缺少项目 ID")
    matches = {
        match.lower()
        for match in _ARCHIVE_ID.findall(str(plan.get("archive_name", "")))
    }
    if len(matches) != 1:
        raise PhysicsCompileError("独立封包计划缺少唯一的 16 位 Archive ID")
    return project_id, f"{project_id}.{next(iter(matches))}"


def _physics_bones(profile):
    targets = profile["target_bones"]
    globals_ = rest_globals(targets, "profile.target_bones")
    result = []
    for index, (target, global_rest) in enumerate(zip(targets, globals_)):
        parent = target["parent"]
        result.append({
            "name": target["name"],
            "palette_slot": target["palette_slot"],
            "parent_slot": -1 if parent < 0 else targets[parent]["palette_slot"],
            "rest_head": [global_rest[3], global_rest[7], global_rest[11]],
            "rest_basis": [
                global_rest[0], global_rest[1], global_rest[2],
                global_rest[4], global_rest[5], global_rest[6],
                global_rest[8], global_rest[9], global_rest[10],
            ],
        })
    return result


def compile_rig_pack(plan, rig_document):
    """编译不含 PhysBone 链的独立骨架运行时包。"""

    try:
        rig_blob, rig_summary = build_rig(rig_document)
    except (RigFormatError, TypeError, ValueError) as error:
        raise PhysicsCompileError(f"Rig Profile 编译失败：{error}") from error

    profiles = {
        _unit_number(profile.get("unit_id"), f"profiles[{index}]")
        for index, profile in enumerate(rig_document.get("profiles", ()))
    }
    published = {row["unit_id"]: row for row in published_unit_rows(plan)}
    unknown = sorted(profiles.difference(published))
    if unknown:
        raise PhysicsCompileError(
            "Rig Profile 引用了独立封包计划以外的 Unit："
            + "、".join(f"{unit_id:016x}" for unit_id in unknown)
        )
    project_id, runtime_stem = _runtime_stem(plan)
    rig_name = f"{runtime_stem}.rigbin"
    unit_rows = [
        {
            "part_slot": published[unit_id]["part_slot"],
            "unit_id": f"{unit_id:016x}",
            "kind": published[unit_id]["kind"],
            "physics_file": None,
            "physics_summary": None,
        }
        for unit_id in sorted(profiles)
    ]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "project_id": project_id,
        "solver_scope": "rig_only",
        "rig": {
            "file": rig_name,
            "sha256": _sha256(rig_blob),
            "summary": rig_summary,
        },
        "chain_consumers": {},
        "units": unit_rows,
    }
    files = {
        rig_name: rig_blob,
        f"{runtime_stem}.hd2physpack.json": _canonical_json(manifest),
    }
    return {"files": files, "manifest": manifest}


def compile_physics_pack(
    plan,
    authoring_project,
    weighted_bones_by_part,
    rig_document,
    physbone_build,
):
    """返回 ``{"files": {name: bytes}, "manifest": ...}``。

    ``physbone_build`` 是 HD2PhysBoneTool 的 canonical producer；通过显式注入保持
    AQSDK 代码可单测，也确保最终 wire bytes 仍由物理插件唯一实现。
    """

    project = build_armor_physics_project(
        plan, authoring_project, weighted_bones_by_part
    )
    try:
        rig_blob, rig_summary = build_rig(rig_document)
    except (RigFormatError, TypeError, ValueError) as error:
        raise PhysicsCompileError(f"Rig Profile 编译失败：{error}") from error

    profiles = {}
    for index, profile in enumerate(rig_document.get("profiles", ())):
        unit_id = _unit_number(profile.get("unit_id"), f"profiles[{index}]")
        if unit_id in profiles:
            raise PhysicsCompileError(f"Rig Profile 重复声明 Unit {unit_id:016x}")
        profiles[unit_id] = profile

    parent_by_bone = {
        row["name"]: row.get("parent") for row in project["shared_bones"]
    }
    weighted_by_part = consumer_bones_by_part(project, {
        binding["part_slot"]: set(binding["weighted_bones"])
        for binding in project["unit_bindings"]
    })
    chain_owner = {}
    simulated_owner = {}
    chains = []
    drivers = set()
    for index, raw in enumerate(project["chains"]):
        if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
            raise PhysicsCompileError(f"chains[{index}] 无效")
        name = str(raw["name"])
        if name in chain_owner:
            raise PhysicsCompileError(f"物理链名称重复：{name}")
        joints = raw.get("joints")
        if not isinstance(joints, list) or not 2 <= len(joints) <= 64:
            raise PhysicsCompileError(f"物理链“{name}”必须有 2..64 个节点")
        logical = [str(joint.get("bone", "")) for joint in joints]
        root_swing = raw.get("root_swing", False)
        if type(root_swing) is not bool:
            raise PhysicsCompileError(f"物理链“{name}”的 root_swing 必须是布尔值")
        if root_swing and any(row["name"] == logical[0] and row.get("is_avatar_source")
                              for row in project["shared_bones"]):
            raise PhysicsCompileError(f"根骨摆动不能覆盖游戏原生骨“{logical[0]}”")
        affected = logical if root_swing else logical[1:]
        if any(bone not in parent_by_bone for bone in logical):
            raise PhysicsCompileError(f"物理链“{name}”引用共享骨架以外的骨骼")
        if any(parent_by_bone[logical[i]] != logical[i - 1] for i in range(1, len(logical))):
            raise PhysicsCompileError(f"物理链“{name}”不是直接父子路径")
        consumers = {
            part
            for part, bones in weighted_by_part.items()
            if any(bone in bones for bone in affected)
        }
        if not consumers:
            raise PhysicsCompileError(f"物理链“{name}”没有影响任何语义部位的正权重顶点")
        if len(consumers) != 1:
            raise PhysicsCompileError(
                f"物理链“{name}”同时影响 {sorted(consumers)}；首版不允许跨 Unit 重复求解"
            )
        owner = next(iter(consumers))
        chain_owner[name] = owner
        if not root_swing:
            drivers.add(logical[0])
        for bone in affected:
            if bone in simulated_owner:
                raise PhysicsCompileError(
                    f"模拟骨“{bone}”同时属于“{simulated_owner[bone]}”和“{name}”"
                )
            simulated_owner[bone] = name
        chains.append((owner, copy.deepcopy(raw)))
    overlap = drivers.intersection(simulated_owner)
    if overlap:
        raise PhysicsCompileError(f"模拟骨“{sorted(overlap)[0]}”不能再作为另一条链的驱动骨")

    links_by_part = {part: [] for part in weighted_by_part}
    for index, link in enumerate(project.get("chain_links", ())):
        if not isinstance(link, dict):
            raise PhysicsCompileError(f"chain_links[{index}] 无效")
        name_a, name_b = link.get("chain_a"), link.get("chain_b")
        if name_a not in chain_owner or name_b not in chain_owner:
            raise PhysicsCompileError(f"chain_links[{index}] 引用了未知物理链")
        if chain_owner[name_a] != chain_owner[name_b]:
            raise PhysicsCompileError("链组连接跨越两个会同时显示的身体 Unit，首版无法安全求解")
        links_by_part[chain_owner[name_a]].append(copy.deepcopy(link))

    chain_parts = set(chain_owner.values())
    colliders_by_part = {part: [] for part in weighted_by_part}
    collider_names = set()
    for index, collider in enumerate(project.get("colliders", ())):
        if not isinstance(collider, dict) or not str(collider.get("name", "")).strip():
            raise PhysicsCompileError(f"colliders[{index}] 无效")
        name = str(collider["name"])
        if name in collider_names:
            raise PhysicsCompileError(f"碰撞体名称重复：{name}")
        collider_names.add(name)
        bone = str(collider.get("bone", "") or "")
        bone_b = str(collider.get("bone_b", "") or "")
        if bone_b and (collider.get("dynamic") is True or bone_b in simulated_owner):
            raise PhysicsCompileError(f"双端胶囊“{name}”的终点必须是固定骨")
        if collider.get("dynamic") is True:
            if bone not in simulated_owner:
                raise PhysicsCompileError(f"动态碰撞体“{name}”没有吸附到模拟骨")
            owners = {chain_owner[simulated_owner[bone]]}
        elif bone in simulated_owner:
            raise PhysicsCompileError(f"碰撞体“{name}”吸附模拟骨时必须设为动态")
        else:
            owners = set(chain_parts)
        canonical = copy.deepcopy(collider)
        for owner in owners:
            colliders_by_part[owner].append(copy.deepcopy(canonical))

    project_id, runtime_stem = _runtime_stem(plan)
    if project["project_id"] != project_id:
        raise PhysicsCompileError("物理项目与独立封包计划的项目 ID 不一致")
    files = {f"{runtime_stem}.rigbin": rig_blob}
    unit_rows = []
    for binding in project["unit_bindings"]:
        part = binding["part_slot"]
        owned_chains = [raw for owner, raw in chains if owner == part]
        for identity in binding["identities"]:
            unit_id = _unit_number(identity["unit_id"], f"{part}.identities")
            profile = profiles.get(unit_id)
            if profile is None:
                raise PhysicsCompileError(f"Unit {unit_id:016x} 没有对应 Rig Profile")
            physics_name = None
            physics_summary = None
            if owned_chains:
                targets = {row["name"] for row in profile["target_bones"]}
                required = {
                    joint["bone"] for chain in owned_chains for joint in chain["joints"]
                }
                required.update(
                    collider[field] for collider in colliders_by_part[part]
                    for field in ("bone", "bone_b") if collider.get(field)
                )
                missing = sorted(required.difference(targets))
                if missing:
                    raise PhysicsCompileError(
                        f"Unit {unit_id:016x} 的 Rig Profile 缺少：" + "、".join(missing)
                    )
                document = {
                    "unit_id": f"{unit_id:016x}",
                    "palette_slots": profile["palette_slots"],
                    "fixed_dt": project["fixed_dt"],
                    "bones": _physics_bones(profile),
                    "chains": copy.deepcopy(owned_chains),
                    "colliders": copy.deepcopy(colliders_by_part[part]),
                    "chain_links": copy.deepcopy(links_by_part[part]),
                }
                try:
                    physics_blob, physics_summary = physbone_build(document)
                except (TypeError, ValueError) as error:
                    raise PhysicsCompileError(
                        f"Unit {unit_id:016x} 的 HD2PHY1 编译失败：{error}"
                    ) from error
                physics_name = f"{runtime_stem}.{part}.{unit_id:016x}.hd2phys"
                files[physics_name] = physics_blob
            unit_rows.append({
                "part_slot": part,
                "unit_id": f"{unit_id:016x}",
                "kind": identity["kind"],
                "physics_file": physics_name,
                "physics_summary": physics_summary,
            })

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "project_id": project_id,
        "solver_scope": "single_consumer_part_v1",
        "rig": {
            "file": f"{runtime_stem}.rigbin",
            "sha256": _sha256(rig_blob),
            "summary": rig_summary,
        },
        "chain_consumers": {
            name: chain_owner[name] for name in sorted(chain_owner)
        },
        "units": sorted(unit_rows, key=lambda row: (row["part_slot"], row["unit_id"])),
    }
    manifest_blob = _canonical_json(manifest)
    files[f"{runtime_stem}.hd2physpack.json"] = manifest_blob
    return {"files": files, "manifest": manifest}


__all__ = ("PhysicsCompileError", "compile_physics_pack", "compile_rig_pack")
