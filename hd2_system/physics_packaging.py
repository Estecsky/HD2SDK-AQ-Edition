"""把独立封包计划与共享 PhysBone 项目连接成整甲编译输入。"""

from __future__ import annotations

import copy
import re


PROJECT_SCHEMA = "hd2ir.armor_physics_project.v1"
PLAN_SCHEMA = "HD2IndependentPackagePlan1"
PHYSICS_AUTHORING_SCHEMA = "HD2PhysBoneAuthoringProject1"
PART_SLOTS = ("Hips", "LeftArm", "RightArm", "Torso", "Torso_Armor", "LeftLeg", "RightLeg", "Head")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,63}$")


class PhysicsPackagingError(ValueError):
    """差分计划、物理项目和实际网格权重不能安全合并。"""


def consumer_bones_by_part(project, weighted_by_part):
    """Real weights plus their ancestors: unweighted simulated parents drive skin.

    This is dependency evidence only, never written back as fabricated weights.
    All original skeleton paths are validated, including unweighted branches.
    """
    parents={b['name']: b.get('parent') for b in project.get('shared_bones', ())}
    if len(parents)!=len(project.get('shared_bones', ())):
        raise PhysicsPackagingError('共享骨架存在重复骨')
    ancestors={}
    def visit(name,seen):
        if not name:return frozenset()
        if name in seen:raise PhysicsPackagingError('共享骨架父链存在循环')
        if name not in parents:raise PhysicsPackagingError(f'共享骨架缺少父骨：{name}')
        if name not in ancestors:ancestors[name]=frozenset((name,)) | visit(parents[name],seen|{name})
        return ancestors[name]
    for name in parents:visit(name,set())
    return {part:set().union(*(ancestors.get(name, {name}) for name in names))
            for part,names in weighted_by_part.items()}


def _can_omit_unconsumed_chain(chain, project, actual_weights):
    """Only an isolated, unweighted, non-influencing export-view leaf is optional.

    Caller has already proven no weighted descendants. Direct logical/root
    weights still signal a likely missing part, preserving that diagnostic.
    Contacts/links that could influence another chain prevent omission.
    """
    logical={j['bone'] for j in chain['joints']}
    if any(logical & set(names) for names in actual_weights.values()):return False
    if any(chain['name'] in (l.get('chain_a'),l.get('chain_b')) for l in project.get('chain_links', ())):return False
    collider_dependencies=consumer_bones_by_part(project, {'colliders':{
        c[k] for c in project.get('colliders', ()) for k in ('bone','bone_b') if c.get(k)}})
    if logical & collider_dependencies['colliders']:return False
    def filters(c):
        return [(j.get('group_mask',c.get('group_mask',1)),j.get('collision_mask',c.get('collision_mask',0x7fffffff)))
                if j.get('override_collision_filter') else (c.get('group_mask',1),c.get('collision_mask',0x7fffffff))
                for j in c['joints']]
    own=filters(chain)
    for collider in project.get('colliders', ()):
        if collider.get('dynamic') and any(g & collider.get('collision_mask',0x7fffffff)
               and collider.get('group_mask',1) & m for g,m in own):return False
    for link in project.get('chain_links', ()):
        for collision in link.get('collisions', ()):
            if any(g & collision.get('collision_mask',0x7fffffff)
                   and collision.get('group_mask',1) & m for g,m in own):return False
    return True


def _project_id(value):
    value = str(value or "").strip()
    if not _SAFE_ID.fullmatch(value):
        raise PhysicsPackagingError("物理包项目 ID 只能包含 ASCII 字母、数字、点、下划线或连字符")
    return value


def _identity_rows(plan):
    rows = {slot: [] for slot in PART_SLOTS}
    seen = set()

    def add(slot, unit_id, kind):
        if slot not in rows:
            raise PhysicsPackagingError(f"未知语义部位：{slot}")
        try:
            numeric = int(unit_id)
        except (TypeError, ValueError) as error:
            raise PhysicsPackagingError(f"{slot} 的 Unit ID 无效：{unit_id!r}") from error
        if numeric <= 0 or numeric > 0xFFFFFFFFFFFFFFFF:
            raise PhysicsPackagingError(f"{slot} 的 Unit ID 超出 uint64")
        if numeric in seen:
            raise PhysicsPackagingError(f"物理包目标 Unit ID 重复：{numeric}")
        seen.add(numeric)
        rows[slot].append({"unit_id": str(numeric), "kind": kind})

    for native in plan.get("native_saves", ()):
        add(native.get("part_slot"), native.get("base_unit_id", native.get("native_unit_id")), "native_base")

    manifest = plan.get("difference_manifest", {})
    for group in manifest.get("body_groups", ()):
        name = str(group.get("name", ""))
        for member in group.get("members", ()):
            slot = member.get("part_slot")
            for route in member.get("routes", ()):
                add(slot, route.get("difference_unit_id"), f"body_difference:{name}")
    for helmet in manifest.get("helmet_differences", ()):
        name = str(helmet.get("name", ""))
        for route in helmet.get("routes", ()):
            add("Head", route.get("difference_unit_id"), f"helmet_difference:{name}")
    for part in manifest.get("free_parts", ()):
        slot = part.get("part_slot")
        for option in part.get("options", ()):
            if option.get("part_slot", slot) != slot:
                raise PhysicsPackagingError("自由搭配差分的选项与所属语义部位不一致")
            identity = str(option.get("id", option.get("name", "")))
            for route in option.get("routes", ()):
                add(slot, route.get("difference_unit_id"), f"free_difference:{identity}")
    return rows


def all_published_unit_ids(plan):
    """按 PartSlot 和数值 ID 返回所有原生/差分目标。"""

    rows = _identity_rows(plan)
    return tuple(
        int(identity["unit_id"])
        for slot in PART_SLOTS
        for identity in sorted(rows[slot], key=lambda item: int(item["unit_id"]))
    )


def published_unit_rows(plan):
    """返回包含语义部位和用途的确定性 Unit 列表。"""

    rows = _identity_rows(plan)
    return tuple(
        {
            "part_slot": slot,
            "unit_id": int(identity["unit_id"]),
            "kind": identity["kind"],
        }
        for slot in PART_SLOTS
        for identity in sorted(
            rows[slot], key=lambda item: (item["kind"], int(item["unit_id"]))
        )
    )


def unit_weight_sets(plan, weighted_bones_by_part, weighted_bones_by_unit=None):
    """验证 Unit 权重摘要的完整性，并保留旧的按部位调用接口。

    提供精确摘要时必须覆盖全部保存目标，且各 Unit 的并集必须与部位摘要一致；
    绝不因缺项而退回槽位并集。部位摘要只用于保存域/链归属检查。
    """
    if not isinstance(weighted_bones_by_part, dict):
        raise PhysicsPackagingError("部位权重摘要必须是对象")

    def clean(names, label):
        if not isinstance(names, (list, tuple, set, frozenset)):
            raise PhysicsPackagingError(f"{label} 的权重摘要必须是名称集合")
        return frozenset(str(name).strip() for name in names if str(name).strip())

    by_part = {}
    for slot, names in weighted_bones_by_part.items():
        if slot not in PART_SLOTS:
            raise PhysicsPackagingError(f"未知语义部位：{slot}")
        by_part[slot] = clean(names, slot)
    rows = published_unit_rows(plan)
    if weighted_bones_by_unit is None:
        return {row['unit_id']: by_part.get(row['part_slot'], frozenset()) for row in rows}
    if not isinstance(weighted_bones_by_unit, dict):
        raise PhysicsPackagingError("Unit 权重摘要必须是对象")
    result = {}
    for key, names in weighted_bones_by_unit.items():
        if isinstance(key, bool) or not isinstance(key, (int, str)):
            raise PhysicsPackagingError("Unit 权重摘要的 ID 无效")
        try:
            uid = int(key)
        except ValueError as exc:
            raise PhysicsPackagingError("Unit 权重摘要的 ID 无效") from exc
        if not 0 < uid <= 0xFFFFFFFFFFFFFFFF or uid in result:
            raise PhysicsPackagingError("Unit 权重摘要的 ID 无效或重复")
        result[uid] = clean(names, f"Unit {uid:016x}")
    if set(result) != {row['unit_id'] for row in rows}:
        raise PhysicsPackagingError("Unit 权重摘要必须准确覆盖本次全部保存目标，不能缺失或包含未知 Unit")
    combined = {}
    for row in rows:
        combined.setdefault(row['part_slot'], set()).update(result[row['unit_id']])
    if any(set(by_part.get(slot, ())) != combined.get(slot, set())
           for slot in set(by_part) | set(combined)):
        raise PhysicsPackagingError("Unit 权重摘要与部位权重并集不一致")
    return result


def required_custom_bones_by_unit(plan, weighted_bones_by_part, source_bone_names,
                                  *, weighted_bones_by_unit=None):
    """返回每个保存 Unit 实际加权的独立辅助骨。

    这里不要求部位必须有辅助骨权重：独立封包中的普通公共骨也需要 Rig Profile，
    才能保留人物专属 Rest/比例。此函数只负责指出额外需要纳入的自定义骨；
    自定义骨的父链会在 ``build_rig_document`` 中从 Unit 场景图自动补齐。
    """

    source_names = {
        str(name).strip() for name in source_bone_names if str(name).strip()
    }
    return {
        uid: names - source_names
        for uid, names in unit_weight_sets(plan, weighted_bones_by_part, weighted_bones_by_unit).items()
    }


def scope_physics_project(plan, authoring_project, weighted_bones_by_part,
                          all_weighted_bones_by_part, *, solver_scope="single_unit"):
    """Select a read-only export view of a shared body/helmet physics project.

    Other-domain weights are evidence of ownership, not additional consumers
    for this export. Never infer ownership from a chain name or bone ancestry.
    Fixed-root weights may identify an excluded domain, but cannot turn an
    unweighted simulated chain into a valid consumer in the selected domain.
    """
    if solver_scope not in {"single_unit", "shared_body_v1_experimental", "shared_body_v2"}:
        raise PhysicsPackagingError("未知物理求解范围")
    shared = solver_scope != "single_unit"
    if plan.get("schema") != PLAN_SCHEMA:
        raise PhysicsPackagingError("不支持的独立封包计划")
    if authoring_project.get("schema") != PHYSICS_AUTHORING_SCHEMA:
        raise PhysicsPackagingError("不支持的 PhysBone 制作项目")
    selected = {slot for slot, rows in _identity_rows(plan).items() if rows}
    if not selected or ("Head" in selected and len(selected) != 1):
        raise PhysicsPackagingError("请分别存储头和身体")
    domain = "HELMET" if selected == {"Head"} else "BODY"
    if shared and domain != "BODY":
        raise PhysicsPackagingError("共享物理首轮仅支持身体，头盔仍需单独存储")
    if plan.get("content_domain", domain) != domain:
        raise PhysicsPackagingError("身体/头盔存储范围与物理部位不一致")
    weights = {}
    for summary in (all_weighted_bones_by_part, weighted_bones_by_part):
        if not isinstance(summary, dict):
            raise PhysicsPackagingError("部位权重摘要必须是对象")
        for slot, names in summary.items():
            if slot not in PART_SLOTS or not isinstance(names, (list, tuple, set, frozenset)):
                raise PhysicsPackagingError(f"{slot} 的权重摘要无效")
            weights.setdefault(slot, set()).update(str(n) for n in names if str(n))
    parents = {}
    for bone in authoring_project.get("shared_bones", ()):
        name = str(bone.get("name", ""))
        if not name or name in parents:
            raise PhysicsPackagingError("共享骨架存在空名或重复骨")
        parents[name] = bone.get("parent")
    dependencies = consumer_bones_by_part(authoring_project, weights)
    retained, excluded, all_names, unconsumed = set(), set(), set(), set()
    simulated = {}
    for chain in authoring_project.get("chains", ()):
        name = str(chain.get("name", ""))
        if not name or name in all_names:
            raise PhysicsPackagingError(f"物理链名称无效或重复：{name}")
        all_names.add(name)
        joints = chain.get("joints", ())
        if not isinstance(joints, (list, tuple)) or not 2 <= len(joints) <= 64:
            raise PhysicsPackagingError(f"物理链“{name}”必须有 2..64 个节点")
        logical = [str(joint.get("bone", "")) for joint in joints]
        if len(set(logical)) != len(logical) or any(b not in parents for b in logical):
            raise PhysicsPackagingError(f"物理链“{name}”引用无效或重复骨")
        if any(parents[logical[i]] != logical[i - 1] for i in range(1, len(logical))):
            raise PhysicsPackagingError(f"物理链“{name}”不是直接父子路径")
        root_swing = chain.get("root_swing", False)
        if type(root_swing) is not bool:
            raise PhysicsPackagingError(f"物理链“{name}”的 root_swing 必须是布尔值")
        affected = logical if root_swing else logical[1:]
        for bone in affected:
            if bone in simulated:
                raise PhysicsPackagingError(f"模拟骨“{bone}”同时属于多条物理链")
            simulated[bone] = name
        consumers = {part for part, bones in dependencies.items() if bones.intersection(affected)}
        current = consumers.intersection(selected)
        if current:
            if shared and not consumers.issubset(selected):
                raise PhysicsPackagingError(f"物理链“{name}”跨保存域或使用未保存的部位 {sorted(consumers - selected)}")
            if not shared and len(consumers) != 1:
                raise PhysicsPackagingError(f"物理链“{name}”跨部位 {sorted(consumers)}；不能跨 Unit 重复求解")
            retained.add(name)
        elif consumers:
            # Only exclude the OTHER save domain. Missing units in the current
            # body domain must not be disguised as a body/helmet split.
            if any((part == "Head") == (domain == "HELMET") for part in consumers):
                raise PhysicsPackagingError(f"物理链“{name}”使用了本次未保存的同域部位 {sorted(consumers)}")
            excluded.add(name)
        else:
            if _can_omit_unconsumed_chain(chain, authoring_project, weights):
                excluded.add(name)
                unconsumed.add(name)
                continue
            root_parts = {part for part, bones in weights.items() if logical[0] in bones}
            if root_parts and all((part == "Head") != (domain == "HELMET") for part in root_parts):
                excluded.add(name)
            else:
                raise PhysicsPackagingError(
                    f"物理链“{name}”没有消费部位：模拟节点没有正权重关联，"
                    "且无法确认属于另一保存域；请检查网格部位、骨绑定与权重")
    scoped = copy.deepcopy(authoring_project)
    scoped["chains"] = [chain for chain in scoped.get("chains", ()) if chain["name"] in retained]
    links = []
    for link in scoped.get("chain_links", ()):
        ends = {link.get("chain_a"), link.get("chain_b")}
        if not ends.issubset(all_names):
            raise PhysicsPackagingError("链组连接引用了未知物理链")
        if ends.intersection(retained) and ends.intersection(excluded):
            raise PhysicsPackagingError("链组连接跨越身体与头盔保存域；不能拆开后丢失约束")
        if ends.issubset(retained):
            links.append(link)
    scoped["chain_links"] = links
    colliders, excluded_colliders = [], []
    for collider in scoped.get("colliders", ()):
        if collider.get("dynamic") is True:
            bone = str(collider.get("bone", "") or "")
            if bone not in simulated:
                raise PhysicsPackagingError(f"动态碰撞体“{collider.get('name', '')}”没有吸附到模拟骨")
            if simulated[bone] in excluded:
                excluded_colliders.append(collider.get("name", ""))
                continue
        # Fixed colliders can intentionally collide with either domain. Keep
        # them; do not silently remove shared head/body collision geometry.
        if retained:
            colliders.append(collider)
    scoped["colliders"] = colliders
    scoped["export_scope"] = {
        "content_domain": domain,
        "excluded_chains": sorted(excluded),
        "unconsumed_isolated_chains": sorted(unconsumed),
        "excluded_dynamic_colliders": sorted(excluded_colliders),
    }
    if 'clothing_pose' in authoring_project:
        from .clothing_pose import scope_rules
        scoped['clothing_pose']=scope_rules(authoring_project,selected,dependencies)
    return scoped


def build_armor_physics_project(plan, authoring_project, weighted_bones_by_part):
    """构建现有整甲编译器可直接消费的无文件依赖项目文档。"""

    if plan.get("schema") != PLAN_SCHEMA:
        raise PhysicsPackagingError("不支持的独立封包计划")
    if authoring_project.get("schema") != PHYSICS_AUTHORING_SCHEMA:
        raise PhysicsPackagingError("不支持的 PhysBone 制作项目")
    if not isinstance(weighted_bones_by_part, dict):
        raise PhysicsPackagingError("部位权重摘要必须是对象")
    identities = _identity_rows(plan)
    bindings = []
    for slot in PART_SLOTS:
        if not identities[slot]:
            continue
        raw_bones = weighted_bones_by_part.get(slot)
        if not isinstance(raw_bones, (list, tuple, set)) or not raw_bones:
            raise PhysicsPackagingError(f"{slot} 没有正权重骨骼，无法判定物理链归属")
        bones = sorted({str(name).strip() for name in raw_bones if str(name).strip()})
        if not bones:
            raise PhysicsPackagingError(f"{slot} 没有有效的正权重骨骼")
        bindings.append({
            "part_slot": slot,
            "weighted_bones": bones,
            "identities": sorted(
                identities[slot], key=lambda item: (item["kind"], int(item["unit_id"]))
            ),
        })

    shared_bones = authoring_project.get("shared_bones")
    if not isinstance(shared_bones, list) or not shared_bones:
        raise PhysicsPackagingError("PhysBone 项目没有共享骨架")
    output_bones = []
    for index, bone in enumerate(shared_bones):
        if not isinstance(bone, dict) or not str(bone.get("name", "")).strip():
            raise PhysicsPackagingError(f"shared_bones[{index}] 无效")
        output_bones.append({
            "name": str(bone["name"]),
            "parent": bone.get("parent"),
            "is_avatar_source": bool(bone.get("is_avatar_source", False)),
        })

    result = {
        "schema": PROJECT_SCHEMA,
        "project_id": _project_id(plan.get("project_id")),
        "shared_bones": output_bones,
        "unit_bindings": bindings,
        "fixed_dt": float(authoring_project.get("fixed_dt", 1.0 / 60.0)),
        "chains": copy.deepcopy(authoring_project.get("chains", [])),
        "colliders": copy.deepcopy(authoring_project.get("colliders", [])),
        "chain_links": copy.deepcopy(authoring_project.get("chain_links", [])),
    }
    if 'clothing_pose' in authoring_project:
        result['clothing_pose']=copy.deepcopy(authoring_project['clothing_pose'])
    return result


def required_profile_bones_by_unit(plan, authoring_project, weighted_bones_by_part,
                                   *, solver_scope="single_unit", weighted_bones_by_unit=None):
    """按最终 Unit 返回其 Rig Profile 必须包含的物理骨。

    共享身体按每个 Unit 的实际权重判定消费，仍保留所消费链的全部节点和依赖。
    头盔单 Unit 求解器仍按部位保留完整物理/衣物依赖，与其现有编译分发一致。
    """

    if solver_scope not in {"single_unit", "shared_body_v2"}:
        raise PhysicsPackagingError("未知物理求解范围")
    shared = solver_scope == "shared_body_v2"
    unit_weights = unit_weight_sets(plan, weighted_bones_by_part, weighted_bones_by_unit)
    if shared:
        # Validate the domain and full-chain closure even for direct callers.
        authoring_project = scope_physics_project(plan, authoring_project,
            weighted_bones_by_part, weighted_bones_by_part, solver_scope=solver_scope)
    project = build_armor_physics_project(plan, authoring_project, weighted_bones_by_part)
    precise = shared and weighted_bones_by_unit is not None
    weights = consumer_bones_by_part(project, unit_weights if precise else {
        binding["part_slot"]: set(binding["weighted_bones"])
        for binding in project["unit_bindings"]
    })
    required_by_part = {part: set() for part in weights}
    chain_owner = {}
    simulated_owner = {}
    for chain in project.get("chains", ()):
        name = str(chain.get("name", ""))
        joints = chain.get("joints", ())
        logical = [str(joint.get("bone", "")) for joint in joints]
        affected = logical if chain.get("root_swing", False) else logical[1:]
        consumers = {
            part for part, bones in weights.items()
            if any(bone in bones for bone in affected)
        }
        if not consumers or (not shared and len(consumers) != 1):
            detail = "没有消费部位" if not consumers else f"跨部位 {sorted(consumers)}"
            raise PhysicsPackagingError(f"物理链“{name}”{detail}")
        chain_owner[name] = consumers
        for owner in consumers:
            required_by_part[owner].update(logical)
        for bone in affected:
            simulated_owner[bone] = consumers
    chain_parts = set().union(*chain_owner.values()) if chain_owner else set()
    for collider in project.get("colliders", ()):
        bone = str(collider.get("bone", "") or "")
        bone_b = str(collider.get("bone_b", "") or "")
        if not bone and not bone_b:
            continue
        if collider.get("dynamic") is True and bone in simulated_owner:
            for part in simulated_owner[bone]:
                required_by_part[part].add(bone)
        else:
            for part in chain_parts:
                required_by_part[part].update(name for name in (bone, bone_b) if name)

    if 'clothing_pose' in authoring_project:
        from .clothing_pose import validate_author, consumer_parts, dependencies as pose_dependencies
        for rule in validate_author(authoring_project):
            if not rule['enabled']:continue
            direct,indirect=consumer_parts(project,weights,rule['target'])
            consumers=direct|indirect
            if not consumers:raise PhysicsPackagingError('衣物驱动没有实际消费部位')
            if not shared and len(consumers)!=1:raise PhysicsPackagingError('单Unit衣物驱动跨部位')
            for part in consumers:required_by_part[part].update(pose_dependencies(rule))
    if precise:
        return {uid: frozenset(required_by_part[uid]) for uid in unit_weights}
    result = {}
    for binding in project["unit_bindings"]:
        required = frozenset(required_by_part[binding["part_slot"]])
        for identity in binding["identities"]:
            result[int(identity["unit_id"])] = required
    return result


__all__ = (
    "PhysicsPackagingError",
    "all_published_unit_ids",
    "build_armor_physics_project",
    "published_unit_rows",
    "required_custom_bones_by_unit",
    "required_profile_bones_by_unit",
    "scope_physics_project",
    "unit_weight_sets",
)
