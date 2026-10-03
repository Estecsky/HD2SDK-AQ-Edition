"""AQSDK 与独立封包纯数据计划之间的窄适配层。

本模块不依赖 Blender。它只负责把已验证计划展开为逐对象保存任务、临时写入
AQSDK 旧保存器需要的 ``Z_ObjectID``/``Z_SwapID_*``，以及原子写出伴随清单。
作者工程中的语义字段不会被替换成真实 Unit ID。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile


PLAN_SCHEMA = "HD2IndependentPackagePlan1"
TARGET_ID_PROPERTIES = (
    "Z_ObjectID",
    "Z_SwapID",
    "Z_SwapID_0",
    "Z_SwapID_1",
    "Z_SwapID_2",
    "Z_SwapID_3",
    "Z_SwapID_4",
)
TARGET_MESH_PROPERTIES = (
    "MeshInfoIndex",
    "BoneInfoIndex",
)


class SDKAdapterError(ValueError):
    """计划无法安全交给 AQSDK 旧保存器。"""


@dataclass(frozen=True)
class SaveJob:
    object_name: str
    native_unit_id: str
    published_unit_id: str | None
    variant_kind: str
    display_name: str
    part_slot: str
    base_object_name: str = ""


def format_save_job_error(job, error):
    """Name authoring sources, never mislabel a processed index as a source vertex."""
    if job.base_object_name and job.base_object_name != job.object_name:
        sources = f'来源网格：差分“{job.object_name}” + 基础“{job.base_object_name}”（合并导出）'
    else:
        sources = f'来源网格：“{job.object_name}”'
    prefix = f'{sources} / 部位 {job.part_slot} / Unit {job.published_unit_id or job.native_unit_id}：'
    detail = str(error)
    return detail if detail.startswith(prefix) else prefix + detail


def _single_object_name(row, label):
    names = tuple(row.get("object_names", ()))
    if len(names) != 1:
        raise SDKAdapterError(f"{label} 必须恰好对应一个网格对象")
    return str(names[0])


def build_save_jobs(plan):
    """将计划展开为确定顺序的逐 Unit 保存任务。"""

    if plan.get("schema") != PLAN_SCHEMA:
        raise SDKAdapterError(f"不支持的独立封包计划：{plan.get('schema')!r}")
    jobs = []
    destinations = set()
    for row in plan.get("native_saves", ()):
        native_id = str(int(row["native_unit_id"]))
        published_id = str(int(row["base_unit_id"])) if "base_unit_id" in row else None
        if plan.get("resource_target_policy") == "PROJECT_ARCHIVE_ALIASES" and (
            published_id is None or published_id == native_id
        ):
            raise SDKAdapterError("基础组缺少独立 Unit ID，请重新生成保存计划")
        destination = published_id or native_id
        if destination in destinations:
            raise SDKAdapterError(f"保存目标 Unit ID 重复：{destination}")
        destinations.add(destination)
        jobs.append(
            SaveJob(
                _single_object_name(row, "基础部位"),
                native_id,
                published_id,
                "BASE",
                plan["difference_manifest"]["base_group"]["name"],
                row["part_slot"],
            )
        )

    difference_manifest = plan["difference_manifest"]
    difference_rows = []
    for group in difference_manifest.get("body_groups", ()):
        for member in group.get("members", ()):
            difference_rows.append(("BODY_GROUP", group["name"], member))
    for helmet in difference_manifest.get("helmet_differences", ()):
        difference_rows.append(("HELMET_SINGLE", helmet["name"], helmet))
    for part in difference_manifest.get("free_parts", ()):
        for option in part['options']:
            difference_rows.append(("FREE_PART", part['name'] + '/' + option['name'], option))

    for kind, display_name, row in difference_rows:
        object_name = _single_object_name(row, f"差分“{display_name}”")
        for route in row.get("routes", ()):
            native_id = str(int(route["native_unit_id"]))
            published_id = str(int(route["difference_unit_id"]))
            if published_id in destinations:
                raise SDKAdapterError(f"保存目标 Unit ID 重复：{published_id}")
            destinations.add(published_id)
            jobs.append(
                SaveJob(
                    object_name,
                    native_id,
                    published_id,
                    kind,
                    display_name,
                    row["part_slot"],
                    row.get("base_object_name", ""),
                )
            )
    return tuple(jobs)


def capture_target_properties(mapping):
    return {
        key: mapping[key]
        for key in tuple(mapping.keys())
        if (
            key in TARGET_ID_PROPERTIES
            or key in TARGET_MESH_PROPERTIES
            or key.startswith("Z_SwapID_")
            or key.startswith("matslot")
        )
    }


def weighted_bones_by_save_job(plan, weighted_by_object):
    """按真正的保存组合汇总骨骼，不把同槽的其他差分混入当前 Unit。"""
    if not isinstance(weighted_by_object, dict):
        raise SDKAdapterError("对象权重摘要必须是对象")
    result = {}
    for job in build_save_jobs(plan):
        names = set()
        for object_name in {job.object_name, job.base_object_name} - {""}:
            if object_name not in weighted_by_object:
                raise SDKAdapterError(f"保存计划缺少网格的权重摘要：{object_name}")
            bones = weighted_by_object[object_name]
            if not isinstance(bones, (list, tuple, set, frozenset)):
                raise SDKAdapterError(f"{object_name} 的权重摘要必须是名称集合")
            names.update(str(name).strip() for name in bones if str(name).strip())
        result[int(job.published_unit_id or job.native_unit_id)] = frozenset(names)
    return result


def clear_target_properties(mapping):
    for key in tuple(mapping.keys()):
        if (
            key in TARGET_ID_PROPERTIES
            or key in TARGET_MESH_PROPERTIES
            or key.startswith("Z_SwapID_")
            or key.startswith("matslot")
        ):
            del mapping[key]


def apply_temporary_target(
    mapping,
    job,
    *,
    mesh_info_index=None,
    bone_info_index=None,
):
    """只为一次旧保存器调用注入目标 ID 与目标 Unit 的 LOD0 槽位。"""

    clear_target_properties(mapping)
    mapping["Z_ObjectID"] = job.native_unit_id
    if job.published_unit_id is not None:
        mapping["Z_SwapID_0"] = job.published_unit_id
    if mesh_info_index is not None:
        mapping["MeshInfoIndex"] = int(mesh_info_index)
    if bone_info_index is not None:
        mapping["BoneInfoIndex"] = int(bone_info_index)


def restore_target_properties(mapping, snapshot):
    clear_target_properties(mapping)
    for key, value in snapshot.items():
        mapping[key] = value


def format_plan_summary(plan):
    manifest = plan["difference_manifest"]
    domain = "头盔" if plan.get("content_domain") == "HELMET" else "身体"
    return (
        f"本次存储：{domain}；基础保存 {len(plan['native_saves'])} 项；"
        f"身体差分组 {len(manifest.get('body_groups', ()))} 个；"
        f"头盔差分 {len(manifest.get('helmet_differences', ()))} 个；"
        f"自由差分部位 {len(manifest.get('free_parts', ()))} 个；"
        f"保存任务 {len(build_save_jobs(plan))} 项"
    )


def split_plan_summary_lines(summary, *, first_line_prefix=""):
    """Split a semicolon-delimited status into at most two balanced UI lines."""

    text = str(summary or "").strip()
    clauses = [clause.strip() for clause in text.split("；") if clause.strip()]
    if len(clauses) < 2:
        return (text,)

    candidates = []
    for split_index in range(1, len(clauses)):
        first = "；".join(clauses[:split_index])
        second = "；".join(clauses[split_index:])
        imbalance = abs(len(first_line_prefix) + len(first) - len(second))
        candidates.append((imbalance, split_index, first, second))
    _imbalance, _split_index, first, second = min(candidates)
    return first, second


def atomic_write_json(path, value):
    """在目标目录内原子替换 JSON，失败时不破坏旧清单。"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    handle, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return path
