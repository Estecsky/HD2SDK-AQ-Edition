"""Archive-scoped, invisible armor aliases. Original Units are never edited."""
from copy import deepcopy
import hashlib

from .independent_packaging import IndependentPackagingError, load_part_catalog
from .runtime_manifest import load_runtime_target_catalog
from .sdk_adapter import build_save_jobs


def retained_cleanup_rows(plan, rows):
    """Newly saved slots supersede old point routes, never the other way round."""
    protected = {int(job.native_unit_id) for job in build_save_jobs(plan)}
    return [deepcopy(row) for row in rows if int(row['native_unit_id']) not in protected]


def build_cleanup_plan(plan, active_unit_ids, saved_unit_ids, *, parts=None, targets=None):
    """Clear every unplanned native row inside this armour's verified boundary.

    Patch entries prove the current plan's outputs exist and reserve IDs;
    unrelated/stale Patch entries do not protect slots. For helmets, ``parts``
    also identifies main Head Units: every owned main head must be saved before
    clearing accessories, never mistaken for an unused plate.
    """
    domain = plan.get("content_domain")
    if domain not in ("BODY", "HELMET") or not (plan.get("native_saves") or
            plan.get('difference_manifest', {}).get('free_parts')):
        raise IndependentPackagingError("请先保存当前身体部位或头盔，再清理剩余甲片/配件")
    archive = str(plan["archive_name"]).lower()
    project = str(plan["project_id"])
    explicit_targets = targets is not None
    if (not explicit_targets or domain == 'HELMET') and plan.get('resource_target_policy') == 'PROJECT_ARCHIVE_ALIASES':
        # NPC/display archives may not be selectable player appearances. Their
        # independently verified descriptor boundary is still authoritative;
        # only Units owned by EVERY matching descriptor can be cleaned.
        # Helmet accessories must use this complete closure, never the older
        # appearance/semantic table which may list only the main Head Unit.
        from .resource_isolation import load_binding_catalog
        binding = load_binding_catalog()['targets'].get(archive)
        if binding and binding['domain'] == domain and binding['equipment']:
            owned = set.intersection(*(set(row['unit_ids']) for row in binding['equipment']))
            target = {'unit_ids': sorted(owned)}
        else:
            raise IndependentPackagingError('当前 Archive 未收录对应身体/头盔装备独立引用边界，不能清理')
    elif domain == 'HELMET':
        raise IndependentPackagingError('头盔配件清理要求独立资源计划，请重新保存头盔')
    else:
        targets = targets if explicit_targets else load_runtime_target_catalog()
        target = targets['targets'].get(archive)
    if target is None:
        raise IndependentPackagingError("当前 Archive 未收录护甲资源边界，不能自动清理")
    active = {int(value) for value in active_unit_ids}
    saved = {int(value) for value in saved_unit_ids}
    jobs = build_save_jobs(plan)
    if not jobs or any((job.part_slot == 'Head') != (domain == 'HELMET') for job in jobs):
        raise IndependentPackagingError('清理计划的已保存部位与身体/头盔范围不一致')
    protected = {int(job.native_unit_id) for job in jobs}
    published = {int(job.published_unit_id or job.native_unit_id) for job in jobs}
    if not published <= saved:
        raise IndependentPackagingError('当前 Patch 缺少本次计划的部位/差分，请先重新保存')
    owned_active = {int(value, 16) for value in target['unit_ids']} & active
    if not protected <= owned_active:
        raise IndependentPackagingError('已保存计划的部位超出当前护甲资源边界')
    if domain == 'HELMET':
        part_catalog = parts if parts is not None else load_part_catalog()
        main_heads = {int(value) for value in part_catalog['part_slots']['Head']}
        if not protected <= main_heads or (owned_active & main_heads) - protected:
            raise IndependentPackagingError('头盔主部件未完整保存或目标无效，不能清理配件')
    # Protect only native routes actually represented by the saved plan, across
    # every base/group/free choice and body type. Unused Torso_Armor (or any
    # other body slot) is intentionally eligible for point-mesh cleanup.
    # In HELMET, validated main heads are protected and only accessory rows
    # inside the same equipment closure can remain as candidates.
    candidates = owned_active - protected
    rows = []
    occupied = active | saved
    previous = {int(row["native_unit_id"]): row for row in plan.get("armor_cleanup", ())}
    for native in sorted(candidates):
        payload = f"HD2ArmorPoint1\0{project}\0{archive}\0{native}".encode()
        alias = int.from_bytes(hashlib.blake2b(payload, digest_size=8, person=b"HD2Point").digest(), "little")
        row = {"native_unit_id": str(native), "point_unit_id": str(alias)}
        if alias == 0 or (alias in occupied and previous.get(native) != row):
            raise IndependentPackagingError("独立甲片点网格 ID 冲突，未修改 Patch")
        occupied.add(alias)
        rows.append(row)
    return rows


def make_point_unit(source):
    """One vertex + one zero-area triangle per draw/LOD, retaining skin layout.

    Keep non-empty buffers (some consumers require them), but no visible
    triangle. Every vertex channel is copied from the same original vertex;
    bone palette indices and weights therefore remain valid together.
    """
    unit = deepcopy(source)
    if not unit.RawMeshes or len(unit.RawMeshes) != len(unit.MeshInfoArray):
        raise IndependentPackagingError("甲片网格布局不完整，不能安全生成点网格")
    for mesh in unit.RawMeshes:
        if not mesh.VertexPositions or not mesh.Materials:
            raise IndependentPackagingError("甲片缺少顶点或材质槽，不能安全生成点网格")
        for attr in ("VertexPositions", "VertexNormals", "VertexTangents",
                     "VertexBiTangents", "VertexColors", "VertexWeights"):
            values = getattr(mesh, attr)
            setattr(mesh, attr, deepcopy(values[:1]))
        mesh.VertexPositions = [[0.0, 0.0, 0.0]]
        for attr in ("VertexUVs", "VertexBoneIndices"):
            setattr(mesh, attr, [deepcopy(values[:1]) for values in getattr(mesh, attr)])
        # Retain all material/remap slots: an original palette is per section.
        # Use vertex zero's palette only for the first draw. Empty subsequent
        # draws keep their material slots without reinterpreting its skin data.
        mesh.Indices = [[0, 0, 0]]
        for index, material in enumerate(mesh.Materials):
            material.StartIndex = 0 if index == 0 else 3
            material.NumIndices = 3 if index == 0 else 0
        mesh.DEV_Use32BitIndices = False
    return unit


def validate_point_unit(unit):
    if not unit.RawMeshes:
        raise IndependentPackagingError("点网格回读为空")
    for mesh in unit.RawMeshes:
        if (len(mesh.VertexPositions) != 1 or mesh.Indices != [[0, 0, 0]]
                or any(float(value) != 0.0 for value in mesh.VertexPositions[0])):
            raise IndependentPackagingError("点网格回读失败：仍包含可见面或多余顶点")
