"""从 AQSDK 已保存的 Unit 结构生成独立骨架 Rig Profile。

模块本身不依赖 Blender 或 AQSDK 类。调用方只需把 Unit 的场景图整理成
``unit_id/name/parent/world`` 快照；这样矩阵、骨架拓扑和安全门可以在普通 Python
单元测试中覆盖。作者只标记公共工作骨，新增的物理辅助骨不会被错误地当成玩家
Avatar 必须提供的 source node。
"""

from __future__ import annotations

import math
import json
from pathlib import Path


PROFILE_SCHEMA = "HD2GeneratedRigProfiles1"
PROFILE_DYNAMIC_POSE_MATCH = "dynamic_pose_match"
SOURCE_BONE_PROPERTY = "HD2BT_WorkRigSourceBone"
OPTIONAL_FINGER_TARGETS = frozenset(f'{side}_{finger}_finger{joint}'
    for side in ('l', 'r') for finger in ('thumb', 'index', 'middle', 'ring', 'pinky')
    for joint in (1, 2, 3))


class UnitRigProfileError(ValueError):
    """Unit 场景图无法安全生成运行时 Rig Profile。"""


def snapshot_from_loaded_unit(unit_id, loaded_data, name_by_hash, authored_name_by_hash):
    """把 AQSDK ``StingrayMeshFile`` 转为不含 SDK 类的只读快照。

    ``authored_name_by_hash`` 必须由当前共享 Armature 的真实骨名生成，优先级高于
   全局名称表，因此作者添加的字符串骨名在保存成 Murmur32 后仍能还原。
    """

    transform = getattr(loaded_data, "TransformInfo", None)
    hashes = list(getattr(transform, "NameHashes", ()) or ())
    matrices = list(getattr(transform, "TransformMatrices", ()) or ())
    entries = list(getattr(transform, "TransformEntries", ()) or ())
    if not hashes or len(hashes) != len(matrices) or len(hashes) != len(entries):
        raise UnitRigProfileError(f"Unit {int(unit_id):016x} 的 TransformInfo 不完整")
    nodes = []
    seen_names = set()
    for index, (name_hash, matrix, entry) in enumerate(zip(hashes, matrices, entries)):
        numeric_hash = int(name_hash)
        name = str(
            authored_name_by_hash.get(
                numeric_hash,
                name_by_hash.get(numeric_hash, numeric_hash),
            )
        )
        if name in seen_names:
            raise UnitRigProfileError(
                f"Unit {int(unit_id):016x} 的场景图骨名解析冲突：{name}"
            )
        seen_names.add(name)
        values = list(getattr(matrix, "v", ()) or ())
        if len(values) != 16:
            raise UnitRigProfileError(
                f"Unit {int(unit_id):016x} 的 TransformMatrix[{index}] 不完整"
            )
        # Stingray Unit 按 row-vector 存 4x4；运行时 Rig JSON 使用 column-vector
        # 3x4。这里等价于完整转置后取前三行。
        world = [
            values[0], values[4], values[8], values[12],
            values[1], values[5], values[9], values[13],
            values[2], values[6], values[10], values[14],
        ]
        raw_parent = int(getattr(entry, "ParentBone", 0))
        parent = -1 if raw_parent == index else raw_parent
        nodes.append({
            "name": name,
            "name_hash": numeric_hash,
            "parent": parent,
            "world": world,
        })
    return {
        "unit_id": int(unit_id),
        "palette_slots": len(nodes),
        "nodes": nodes,
    }


def _text(value, where):
    value = str(value or "").strip()
    if not value:
        raise UnitRigProfileError(f"{where}不能为空")
    if len(value.encode("utf-8")) > 63:
        raise UnitRigProfileError(f"{where}的 UTF-8 名称超过 63 字节")
    return value


def _mat34(value, where):
    if not isinstance(value, (list, tuple)) or len(value) != 12:
        raise UnitRigProfileError(f"{where}必须是 12 个数字的 3x4 矩阵")
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError) as error:
        raise UnitRigProfileError(f"{where}包含非数字") from error
    if not all(math.isfinite(item) for item in result):
        raise UnitRigProfileError(f"{where}包含 NaN 或无穷值")
    columns = [(result[0], result[4], result[8]),
               (result[1], result[5], result[9]),
               (result[2], result[6], result[10])]
    dot = lambda left, right: sum(a * b for a, b in zip(left, right))
    if any(abs(math.sqrt(dot(column, column)) - 1.0) > 0.002 for column in columns):
        raise UnitRigProfileError(f"{where}含非刚性缩放")
    if any(abs(dot(columns[a], columns[b])) > 0.002
           for a, b in ((0, 1), (0, 2), (1, 2))):
        raise UnitRigProfileError(f"{where}旋转轴不正交")
    return result


def _multiply(left, right):
    result = [0.0] * 12
    for row in range(3):
        for column in range(3):
            result[row * 4 + column] = sum(
                left[row * 4 + k] * right[k * 4 + column] for k in range(3)
            )
        result[row * 4 + 3] = (
            sum(left[row * 4 + k] * right[k * 4 + 3] for k in range(3))
            + left[row * 4 + 3]
        )
    return result


def _inverse_rigid(matrix):
    result = [0.0] * 12
    for row in range(3):
        for column in range(3):
            result[row * 4 + column] = matrix[column * 4 + row]
    for row in range(3):
        result[row * 4 + 3] = -sum(
            result[row * 4 + k] * matrix[k * 4 + 3] for k in range(3)
        )
    return result


def _rest_globals(bones, where):
    result = []
    for index, bone in enumerate(bones):
        parent = bone["parent"]
        local = _mat34(bone["rest_local"], f"{where}[{index}].rest_local")
        if not isinstance(parent, int) or parent < -1 or parent >= index:
            raise UnitRigProfileError(f"{where}[{index}].parent 必须指向更早的骨骼")
        result.append(local if parent < 0 else _multiply(result[parent], local))
    return result


def load_avatar_source():
    return json.loads(Path(__file__).with_name("avatar_source.json").read_text(
        encoding="utf-8"))["source_bones"]


def _source_skeleton(authoring_project, runtime_source_bones=None):
    raw_bones = authoring_project.get("shared_bones")
    if not isinstance(raw_bones, list) or not raw_bones:
        raise UnitRigProfileError("物理项目没有共享骨架")
    by_name = {}
    for index, raw in enumerate(raw_bones):
        if not isinstance(raw, dict):
            raise UnitRigProfileError(f"shared_bones[{index}]不是对象")
        name = _text(raw.get("name"), f"shared_bones[{index}].name")
        if name in by_name:
            raise UnitRigProfileError(f"共享骨架存在重复骨名：{name}")
        by_name[name] = raw

    if runtime_source_bones is not None:
        # The game exposes a calibrated set of public animation channels.
        # Blender helpers/mesh nodes are target authoring data, not additional
        # animation channels the live Avatar can necessarily supply.
        _rest_globals(runtime_source_bones, "runtime_source_bones")
        return runtime_source_bones, by_name

    source_names = {
        name for name, raw in by_name.items() if raw.get("is_avatar_source") is True
    }
    if not source_names:
        raise UnitRigProfileError(
            "共享骨架没有公共工作骨标记；请从新版普通/含头灯工作骨架开始制作"
        )

    ordered = []
    pending = set(source_names)
    while pending:
        progressed = False
        for raw in raw_bones:
            name = raw["name"]
            if name not in pending:
                continue
            parent_name = raw.get("parent")
            if parent_name in pending:
                continue
            parent_index = next(
                (i for i, row in enumerate(ordered) if row["name"] == parent_name), -1
            )
            ordered.append({
                "name": name,
                "parent": parent_index,
                "flags": ["root"] if parent_index < 0 else [],
                # ``rest_local`` is the fitted character target Rest used by
                # PhysBone authoring.  Animation deltas, however, must be
                # measured from the pristine public Avatar Rest.  Keeping the
                # two contracts separate is what preserves custom proportions.
                "rest_local": _mat34(
                    raw.get("avatar_source_rest_local", raw.get("rest_local")),
                    f"shared_bones[{name}].avatar_source_rest_local",
                ),
            })
            pending.remove(name)
            progressed = True
        if not progressed:
            raise UnitRigProfileError("公共工作骨的父子关系存在环或缺失")
    if len(ordered) > 256:
        raise UnitRigProfileError("公共工作骨超过运行时上限 256")
    return ordered, by_name


def required_physics_bones(authoring_project):
    """返回所有骨链/碰撞体实际引用的逻辑骨名。"""

    names = set()
    chains = authoring_project.get("chains")
    if not isinstance(chains, list) or not chains:
        raise UnitRigProfileError("物理项目至少需要一条骨链")
    for chain_index, chain in enumerate(chains):
        joints = chain.get("joints") if isinstance(chain, dict) else None
        if not isinstance(joints, list) or len(joints) < 2:
            raise UnitRigProfileError(f"chains[{chain_index}]至少需要驱动骨和一根模拟骨")
        for joint_index, joint in enumerate(joints):
            if not isinstance(joint, dict):
                raise UnitRigProfileError(
                    f"chains[{chain_index}].joints[{joint_index}]不是对象"
                )
            names.add(_text(
                joint.get("bone"),
                f"chains[{chain_index}].joints[{joint_index}].bone",
            ))
    for collider_index, collider in enumerate(authoring_project.get("colliders", ())):
        if not isinstance(collider, dict):
            raise UnitRigProfileError(f"colliders[{collider_index}]不是对象")
        if collider.get("bone"):
            names.add(_text(collider["bone"], f"colliders[{collider_index}].bone"))
    return frozenset(names)


def _ordered_selected_nodes(snapshot, wanted_names, required_names, include_ancestors=True):
    raw_nodes = snapshot.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise UnitRigProfileError("Unit 场景图为空")
    node_by_name = {}
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise UnitRigProfileError(f"nodes[{index}]不是对象")
        name = _text(raw.get("name"), f"nodes[{index}].name")
        if name in node_by_name:
            raise UnitRigProfileError(f"Unit 场景图存在重复骨名：{name}")
        parent = raw.get("parent")
        if not isinstance(parent, int) or parent < -1 or parent >= len(raw_nodes) or parent == index:
            raise UnitRigProfileError(f"nodes[{index}].parent 无效")
        node_by_name[name] = (index, raw)
    missing = sorted(name for name in required_names if name not in node_by_name)
    if missing:
        location = str(snapshot.get("source_label") or "未记录来源对象")
        unit_id = snapshot.get("unit_id")
        unit_label = f"{unit_id:016x}" if isinstance(unit_id, int) else "未知"
        raise UnitRigProfileError(
            f"导出骨骼不完整：{location}（Unit {unit_label}）。\n"
            "缺少骨骼：" + "、".join(missing) + "。\n"
            "这些骨未出现在已保存的模型里，不一定是没做物理。请按顺序检查：\n"
            "1. 检查该对象的骨架修改器是否指向当前工作骨架，并核对上述骨名是否被删除或改名；"
            "若绑定已失效，请在物理插件中重新绑定对应骨链或碰撞体。\n"
            "2. 检查这些骨的顶点组权重：若仅有不需要的微小残留权重（不大于 0.001），确认后清理；"
            "真正用于变形、骨链末端或碰撞的骨不要直接删除，也不要为过检随意刷大权重。\n"
            "3. 若缺骨只属于同部位的另一个差分，请核对部位和差分指定；仍报错时提供工程及本条信息，"
            "不要把其他差分的骨强行加到当前网格。\n"
            "检查后重新点击“保存独立部位与差分”，不要只重新导出物理包。"
        )

    selected_indices = {
        index for name, (index, _raw) in node_by_name.items() if name in wanted_names
    }
    # 物理节点和 source 节点的父链都需要存在，才能把世界 Rest 稳定还原为局部 Rest。
    for index in tuple(selected_indices):
        visited = {index}
        parent = raw_nodes[index]["parent"]
        while parent >= 0:
            if parent in visited:
                raise UnitRigProfileError("Unit 场景图父子关系存在环")
            visited.add(parent)
            if include_ancestors:
                selected_indices.add(parent)
            parent = raw_nodes[parent]["parent"]

    ordered = []
    pending = set(selected_indices)
    while pending:
        progressed = False
        for index, raw in enumerate(raw_nodes):
            if index not in pending:
                continue
            parent = raw["parent"]
            if not include_ancestors:
                while parent >= 0 and parent not in selected_indices:
                    parent = raw_nodes[parent]["parent"]
            if parent in pending:
                continue
            ordered.append((index, raw))
            pending.remove(index)
            progressed = True
        if not progressed:
            raise UnitRigProfileError("Unit 场景图父子关系存在环")
    if len(ordered) > 256:
        raise UnitRigProfileError("Unit Rig Profile 骨骼超过运行时上限 256")
    return ordered


def authored_hand_grips(author_bones, targets, source_bones):
    """Preserve unweighted author attachment frames as metadata, not mesh nodes."""
    cache, visiting = {}, set()
    def world(name):
        if name in cache:
            return cache[name]
        if name in visiting or name not in author_bones:
            raise UnitRigProfileError("握持校准骨架父链缺失或循环：" + name)
        visiting.add(name)
        raw = author_bones[name]
        local = _mat34(raw.get("rest_local"), "grip." + name)
        parent = raw.get("parent")
        cache[name] = _multiply(world(parent), local) if parent else local
        visiting.remove(name)
        return cache[name]
    grips = []
    for side, suffix in enumerate(("r", "l")):
        hand, attach = suffix + "_hand", "attach_hand_" + suffix
        matches = [i for i,b in enumerate(targets) if b["source_index"] >= 0 and
                   source_bones[b["source_index"]]["name"] == hand and
                   "source_parent_space" in b["flags"]]
        if len(matches) != 1 or hand not in author_bones or attach not in author_bones:
            continue
        chain, cursor = [], matches[0]
        while cursor >= 0:
            bone = targets[cursor]
            si = bone["source_index"]
            if si >= 0 and "source_parent_space" in bone["flags"]:
                chain.append(source_bones[si]["name"])
            cursor = bone["parent"]
        if not all(suffix + "_" + part in chain for part in ("hand", "elbow", "shoulder")):
            continue  # A partial hand-only mesh has no two-bone constraint chain.
        grips.append({"hand": matches[0], "side": side,
                      "hand_to_grip": _multiply(_inverse_rigid(world(hand)), world(attach))})
    return grips


def build_rig_document(authoring_project, unit_snapshots, required_bones_by_unit=None,
                       runtime_source_bones=None, weighted_bones_by_unit=None, *, rig_gender=None):
    """生成可交给 HD2IRG1 producer 的 Rig JSON 数据。"""

    if authoring_project.get("schema") != "HD2PhysBoneAuthoringProject1":
        raise UnitRigProfileError("不支持的物理制作项目格式")
    if rig_gender not in {None, 'MALE', 'FEMALE'}:
        raise UnitRigProfileError('发布骨架类型无效，不推断或默认性别')
    source_bones, author_bones = _source_skeleton(authoring_project, runtime_source_bones)
    if required_bones_by_unit is None:
        required = required_physics_bones(authoring_project)
    else:
        if not isinstance(required_bones_by_unit, dict):
            raise UnitRigProfileError("required_bones_by_unit 必须是对象")
        required = frozenset(
            str(name)
            for names in required_bones_by_unit.values()
            for name in names
        )
    unknown = sorted(required.difference(author_bones))
    if unknown:
        raise UnitRigProfileError("物理项目引用共享骨架以外的骨骼：" + "、".join(unknown))
    source_index_by_name = {bone["name"]: index for index, bone in enumerate(source_bones)}
    source_global = _rest_globals(source_bones, "source_bones")

    profiles = []
    seen_units = set()
    for snapshot_index, snapshot in enumerate(unit_snapshots):
        if not isinstance(snapshot, dict):
            raise UnitRigProfileError(f"unit_snapshots[{snapshot_index}]不是对象")
        try:
            unit_id = int(snapshot.get("unit_id"))
        except (TypeError, ValueError) as error:
            raise UnitRigProfileError(f"unit_snapshots[{snapshot_index}].unit_id 无效") from error
        if unit_id <= 0 or unit_id > 0xFFFFFFFFFFFFFFFF or unit_id in seen_units:
            raise UnitRigProfileError(f"Unit ID 无效或重复：{unit_id}")
        seen_units.add(unit_id)

        unit_required = (
            frozenset(required_bones_by_unit.get(unit_id, ()))
            if required_bones_by_unit is not None
            else required
        )
        unknown_required = sorted(unit_required.difference(author_bones))
        if unknown_required:
            raise UnitRigProfileError(
                f"Unit {unit_id:016x} 需要共享骨架以外的骨骼：" + "、".join(unknown_required)
            )
        wanted = set(source_index_by_name).union(unit_required)
        required_for_selection = unit_required
        if runtime_source_bones is not None and weighted_bones_by_unit is not None:
            if not isinstance(weighted_bones_by_unit, dict) or unit_id not in weighted_bones_by_unit:
                raise UnitRigProfileError(f'Unit {unit_id:016x} 缺少实际权重摘要')
            weights = weighted_bones_by_unit[unit_id]
            if not isinstance(weights, (set, frozenset, list, tuple)):
                raise UnitRigProfileError('实际权重摘要必须是骨名集合')
            explicit = set(weights).union(unit_required)
            required_for_selection = explicit
            unknown_weights = explicit.difference(author_bones)
            if unknown_weights:
                raise UnitRigProfileError('实际权重引用未知骨：' + '、'.join(sorted(unknown_weights)))
            # Input source channels stay complete. Only unused finger output
            # targets can be omitted; structural fitting/IK bones stay intact.
            wanted = (set(source_index_by_name) - OPTIONAL_FINGER_TARGETS) | explicit
            for name in explicit:
                seen = set()
                while name and name not in seen:
                    seen.add(name)
                    if name in OPTIONAL_FINGER_TARGETS and name in source_index_by_name:
                        wanted.add(name)
                    name = author_bones.get(name, {}).get('parent')
        ordered = _ordered_selected_nodes(
            snapshot, wanted, required_for_selection,
            include_ancestors=runtime_source_bones is None,
        )
        if runtime_source_bones is not None and weighted_bones_by_unit is not None:
            # A complete authored hand constraint consumes finger landmarks even
            # when they have no mesh weights (e.g. helmet hand colliders). Probe
            # the actual selected ancestry using the same grip eligibility gate;
            # retain available source fingers only for those constrained sides.
            preliminary_indices = {old: new for new, (old, _raw) in enumerate(ordered)}
            preliminary = []
            for _old, raw in ordered:
                parent = raw['parent']
                while parent >= 0 and parent not in preliminary_indices:
                    parent = snapshot['nodes'][parent]['parent']
                source_index = source_index_by_name.get(raw['name'], -1)
                preliminary.append(dict(parent=preliminary_indices.get(parent, -1),
                    source_index=source_index,
                    flags=['source_parent_space'] if source_index >= 0 else []))
            grips = authored_hand_grips(author_bones, preliminary, source_bones)
            dependencies = {name for grip in grips for name in OPTIONAL_FINGER_TARGETS
                            if name.startswith(('r_' if grip['side'] == 0 else 'l_'))
                            and name in source_index_by_name}
            if dependencies - wanted:
                wanted.update(dependencies)
                ordered = _ordered_selected_nodes(snapshot, wanted, required_for_selection,
                                                  include_ancestors=False)
        old_to_new = {old: new for new, (old, _raw) in enumerate(ordered)}
        target_bones = []
        target_globals = []
        mapped_count = 0
        for old_index, raw in ordered:
            name = raw["name"]
            parent_old = raw["parent"]
            if runtime_source_bones is not None:
                while parent_old >= 0 and parent_old not in old_to_new:
                    parent_old = snapshot["nodes"][parent_old]["parent"]
            parent_new = old_to_new.get(parent_old, -1)
            world = _mat34(raw.get("world"), f"Unit {unit_id}.nodes[{old_index}].world")
            local = (
                world
                if parent_new < 0
                else _multiply(_inverse_rigid(target_globals[parent_new]), world)
            )
            source_index = source_index_by_name.get(name, -1)
            flags = ["scene_graph_index"]
            if source_index >= 0:
                mapped_count += 1
                if runtime_source_bones is not None:
                    flags.extend(["source_parent_space", "transfer_translation"])
                elif max(abs(a - b) for a, b in zip(world, source_global[source_index])) <= 2.0e-4:
                    flags.append("source_global_pose")
                else:
                    flags.append("transfer_translation")
            elif parent_new >= 0:
                flags.append("inherit_parent_pose")
                # The Lua bridge needs a public source label even when the
                # target is a custom node whose pose simply follows its parent.
                if runtime_source_bones is not None:
                    source_index = target_bones[parent_new]["source_index"]
            elif name in unit_required:
                raise UnitRigProfileError(
                    f"Unit {unit_id:016x} 的自定义物理根骨 {name} 没有可继承的父节点"
                )
            target_bones.append({
                "name": name,
                "parent": parent_new,
                "source_index": source_index,
                "palette_slot": old_index,
                "flags": flags,
                "rest_local": local,
            })
            target_globals.append(world)
        if mapped_count < 3:
            raise UnitRigProfileError(
                f"Unit {unit_id:016x} 只有 {mapped_count} 个公共工作骨，无法稳定识别实例"
            )
        palette_slots = int(snapshot.get("palette_slots", len(snapshot["nodes"])))
        if palette_slots < len(snapshot["nodes"]) or palette_slots > 4096:
            raise UnitRigProfileError(f"Unit {unit_id:016x} 的调色板容量无效")
        profiles.append({
            "unit_id": f"{unit_id:016x}",
            "palette_slots": palette_slots,
            "needle_to_palette": 0,
            "needle_hex": "00" * 16,
            # Scene-node authoring uses the proven Lua writer. It must not
            # re-enable the retired GPU palette discovery/write path.
            "flags": ([] if runtime_source_bones is not None else [PROFILE_DYNAMIC_POSE_MATCH]) +
                     ([] if rig_gender is None else ['gender_' + rig_gender.lower()]),
            "target_bones": target_bones,
            "hand_grips": authored_hand_grips(author_bones, target_bones, source_bones)
                          if runtime_source_bones is not None else [],
        })
    if not profiles:
        raise UnitRigProfileError("没有可生成的 Unit Rig Profile")
    return {
        "schema": PROFILE_SCHEMA,
        "source_bones": source_bones,
        "profiles": profiles,
    }


__all__ = (
    "PROFILE_SCHEMA",
    "SOURCE_BONE_PROPERTY",
    "UnitRigProfileError",
    "build_rig_document",
    "required_physics_bones",
    "snapshot_from_loaded_unit",
)
