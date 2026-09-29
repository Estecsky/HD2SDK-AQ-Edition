"""自包含的 HD2IRG1 producer，供 AQSDK 独立封包模式使用。"""

from __future__ import annotations

import math
import struct
import zlib


MAGIC = b"HD2IRG1\0"
VERSION = 1
HEADER = struct.Struct("<8s6I")
SOURCE = struct.Struct("<64siI12f")
PROFILE = struct.Struct("<QIIiII")
TARGET = struct.Struct("<64siiIIf12f")
GRIP = struct.Struct("<4I12f")
SOURCE_FLAGS = {"root": 1}
PROFILE_FLAGS = {"dynamic_pose_match": 1, "observe_only": 2, "gender_male": 4, "gender_female": 8}
TARGET_FLAGS = {
    "transfer_translation": 1,
    "attachment": 2,
    "left_hand": 4,
    "right_hand": 8,
    "backpack": 16,
    "scene_graph_index": 32,
    "anchor_only": 64,
    "diagnostic_motion": 128,
    "inherit_parent_pose": 256,
    "source_global_pose": 512,
    "source_parent_space": 1024,
}


class RigFormatError(ValueError):
    """Rig 文档违反 HD2IRG1 格式门。"""


def _flags(value, names, where):
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if not isinstance(value, list):
        raise RigFormatError(f"{where}.flags 必须是整数或字符串数组")
    result = 0
    for name in value:
        if name not in names:
            raise RigFormatError(f"{where}.flags 包含未知值 {name!r}")
        result |= names[name]
    return result


def _name64(value, where):
    if not isinstance(value, str) or not value:
        raise RigFormatError(f"{where}.name 不能为空")
    encoded = value.encode("utf-8")
    if len(encoded) > 63:
        raise RigFormatError(f"{where}.name 的 UTF-8 长度超过 63 字节")
    return encoded + bytes(64 - len(encoded))


def _matrix(value, where):
    if not isinstance(value, list) or len(value) != 12:
        raise RigFormatError(f"{where}.rest_local 必须包含 12 个数字")
    try:
        matrix = [float(item) for item in value]
    except (TypeError, ValueError) as error:
        raise RigFormatError(f"{where}.rest_local 包含非数字") from error
    if not all(math.isfinite(item) for item in matrix):
        raise RigFormatError(f"{where}.rest_local 包含 NaN 或无穷值")
    columns = [(matrix[0], matrix[4], matrix[8]),
               (matrix[1], matrix[5], matrix[9]),
               (matrix[2], matrix[6], matrix[10])]
    dot = lambda left, right: sum(a * b for a, b in zip(left, right))
    if any(abs(math.sqrt(dot(column, column)) - 1.0) > 0.002 for column in columns):
        raise RigFormatError(f"{where}.rest_local 的旋转列不是单位长度")
    if any(abs(dot(columns[a], columns[b])) > 0.002
           for a, b in ((0, 1), (0, 2), (1, 2))):
        raise RigFormatError(f"{where}.rest_local 的旋转列不正交")
    # ``matrix`` is the row-major 3x4 affine layout used by the wire format.
    # Spell the determinant out in those indices instead of mixing column
    # tuples: the former expression's middle minor accidentally used c1[2]
    # where c2[1] was required and rejected ordinary rotations as reflections.
    determinant = (
        matrix[0] * (matrix[5] * matrix[10] - matrix[6] * matrix[9])
        - matrix[1] * (matrix[4] * matrix[10] - matrix[6] * matrix[8])
        + matrix[2] * (matrix[4] * matrix[9] - matrix[5] * matrix[8])
    )
    if abs(determinant - 1.0) > 0.005:
        raise RigFormatError(f"{where}.rest_local 的旋转行列式不是 +1")
    return matrix


def _parent(value, index, where):
    if not isinstance(value, int) or value < -1 or value >= index:
        raise RigFormatError(f"{where}.parent 必须为 -1 或指向更早的骨骼")
    return value


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


def rest_globals(bones, where="bones"):
    result = []
    for index, bone in enumerate(bones):
        if not isinstance(bone, dict):
            raise RigFormatError(f"{where}[{index}] 不是对象")
        parent = _parent(bone.get("parent"), index, f"{where}[{index}]")
        local = _matrix(bone.get("rest_local"), f"{where}[{index}]")
        result.append(local if parent < 0 else _multiply(result[parent], local))
    return result


def _unit_id(value, where):
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        text = value.strip().lower().removeprefix("0x")
        base = 16 if len(text) == 16 or any(character in "abcdef" for character in text) else 10
        try:
            result = int(text, base)
        except ValueError as error:
            raise RigFormatError(f"{where}.unit_id 无效") from error
    else:
        raise RigFormatError(f"{where}.unit_id 必须是整数或十六进制文本")
    if result <= 0 or result > 0xFFFFFFFFFFFFFFFF:
        raise RigFormatError(f"{where}.unit_id 超出 uint64")
    return result


def build(document):
    """返回 ``(rigbin bytes, deterministic summary)``。"""

    if not isinstance(document, dict):
        raise RigFormatError("Rig 文档顶层必须是对象")
    sources = document.get("source_bones")
    profiles = document.get("profiles")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 256:
        raise RigFormatError("source_bones 数量必须是 1..256")
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 512:
        raise RigFormatError("profiles 数量必须是 1..512")

    payload = bytearray()
    source_names = set()
    for index, bone in enumerate(sources):
        where = f"source_bones[{index}]"
        if not isinstance(bone, dict):
            raise RigFormatError(f"{where} 不是对象")
        name = bone.get("name")
        if name in source_names:
            raise RigFormatError(f"{where}.name 重复")
        source_names.add(name)
        payload += SOURCE.pack(
            _name64(name, where),
            _parent(bone.get("parent"), index, where),
            _flags(bone.get("flags"), SOURCE_FLAGS, where),
            *_matrix(bone.get("rest_local"), where),
        )
    source_rest_global = rest_globals(sources, "source_bones")

    summary = {"source_bones": len(sources), "profiles": []}
    declared_gender = False
    seen_units = set()
    for profile_index, profile in enumerate(profiles):
        where = f"profiles[{profile_index}]"
        if not isinstance(profile, dict):
            raise RigFormatError(f"{where} 不是对象")
        unit_id = _unit_id(profile.get("unit_id"), where)
        if unit_id in seen_units:
            raise RigFormatError(f"{where}.unit_id 重复")
        seen_units.add(unit_id)
        targets = profile.get("target_bones")
        if not isinstance(targets, list) or not 1 <= len(targets) <= 256:
            raise RigFormatError(f"{where}.target_bones 数量必须是 1..256")
        palette_slots = profile.get("palette_slots")
        if not isinstance(palette_slots, int) or not 1 <= palette_slots <= 4096:
            raise RigFormatError(f"{where}.palette_slots 必须是 1..4096")
        needle_hex = profile.get("needle_hex")
        if not isinstance(needle_hex, str):
            raise RigFormatError(f"{where}.needle_hex 缺失")
        try:
            needle = bytes.fromhex("".join(needle_hex.split()))
        except ValueError as error:
            raise RigFormatError(f"{where}.needle_hex 无效") from error
        if not 16 <= len(needle) <= (1 << 20):
            raise RigFormatError(f"{where}.needle_hex 长度必须是 16 字节..1 MiB")
        needle_offset = profile.get("needle_to_palette")
        if not isinstance(needle_offset, int) or not -0x80000000 <= needle_offset <= 0x7FFFFFFF:
            raise RigFormatError(f"{where}.needle_to_palette 超出 int32")
        profile_flags = _flags(profile.get('flags'), PROFILE_FLAGS, where)
        if profile_flags < 0 or profile_flags & ~15 or profile_flags & 12 == 12:
            raise RigFormatError(f'{where}.flags 包含未知位或冲突的男女标记')
        declared_gender = declared_gender or bool(profile_flags & 12)
        payload += PROFILE.pack(
            unit_id,
            len(targets),
            len(needle),
            needle_offset,
            palette_slots,
            profile_flags,
        )

        target_names = set()
        target_slots = set()
        target_rest_global = []
        for target_index, bone in enumerate(targets):
            target_where = f"{where}.target_bones[{target_index}]"
            if not isinstance(bone, dict):
                raise RigFormatError(f"{target_where} 不是对象")
            name = bone.get("name")
            if name in target_names:
                raise RigFormatError(f"{target_where}.name 重复")
            target_names.add(name)
            source_index = bone.get("source_index")
            if not isinstance(source_index, int) or not -1 <= source_index < len(sources):
                raise RigFormatError(f"{target_where}.source_index 超出 source_bones")
            palette_slot = bone.get("palette_slot")
            if not isinstance(palette_slot, int) or not 0 <= palette_slot < palette_slots:
                raise RigFormatError(f"{target_where}.palette_slot 超出调色板")
            if palette_slot in target_slots:
                raise RigFormatError(f"{target_where}.palette_slot 重复")
            target_slots.add(palette_slot)
            translation_scale = float(bone.get("translation_scale", 1.0))
            if not math.isfinite(translation_scale) or not 0.0 <= translation_scale <= 100.0:
                raise RigFormatError(f"{target_where}.translation_scale 无效")
            target_flags = _flags(bone.get("flags"), TARGET_FLAGS, target_where)
            if target_flags & TARGET_FLAGS["scene_graph_index"] and target_flags & TARGET_FLAGS["anchor_only"]:
                raise RigFormatError(f"{target_where} 的 scene_graph_index 与 anchor_only 冲突")
            if target_flags & TARGET_FLAGS["diagnostic_motion"] and not target_flags & TARGET_FLAGS["scene_graph_index"]:
                raise RigFormatError(f"{target_where} 的 diagnostic_motion 需要 scene_graph_index")
            target_parent = _parent(bone.get("parent"), target_index, target_where)
            if target_flags & TARGET_FLAGS["source_parent_space"] and (
                source_index < 0
                or not target_flags & TARGET_FLAGS["scene_graph_index"]
                or target_flags & (TARGET_FLAGS["inherit_parent_pose"] | TARGET_FLAGS["source_global_pose"])
                or (target_parent >= 0 and targets[target_parent]["source_index"] < 0)
            ):
                raise RigFormatError(f"{target_where} 的 source_parent_space 标志或父级映射无效")
            if target_flags & TARGET_FLAGS["inherit_parent_pose"] and target_parent < 0:
                raise RigFormatError(f"{target_where} 的 inherit_parent_pose 需要父骨")
            if target_flags & TARGET_FLAGS["source_global_pose"] and (
                not target_flags & TARGET_FLAGS["scene_graph_index"]
                or target_flags & TARGET_FLAGS["inherit_parent_pose"]
                or source_index < 0
            ):
                raise RigFormatError(f"{target_where} 的 source_global_pose 标志组合无效")
            local = _matrix(bone.get("rest_local"), target_where)
            global_rest = local if target_parent < 0 else _multiply(
                target_rest_global[target_parent], local
            )
            target_rest_global.append(global_rest)
            if target_flags & TARGET_FLAGS["source_global_pose"] and any(
                abs(actual - expected) > 2.0e-4
                for actual, expected in zip(global_rest, source_rest_global[source_index])
            ):
                raise RigFormatError(f"{target_where} 的 source_global_pose Rest 不一致")
            payload += TARGET.pack(
                _name64(name, target_where),
                target_parent,
                source_index,
                palette_slot,
                target_flags,
                translation_scale,
                *local,
            )
        payload += needle
        summary["profiles"].append({
            "unit_id": f"{unit_id:016x}",
            "target_bones": len(targets),
            "palette_slots": palette_slots,
            "needle_bytes": len(needle),
            "rig_gender": 'MALE' if profile_flags & 4 else 'FEMALE' if profile_flags & 8 else None,
        })

    grips = bytearray()
    for pi, profile in enumerate(profiles):
        seen_sides = set()
        rows = profile.get("hand_grips", [])
        if not isinstance(rows, list):
            raise RigFormatError("hand_grips 必须是列表")
        for grip in rows:
            if not isinstance(grip, dict):
                raise RigFormatError("hand_grip 必须是对象")
            hand, side = grip.get("hand"), grip.get("side")
            targets = profile["target_bones"]
            if (type(hand) is not int or not 0 <= hand < len(targets) or
                    type(side) is not int or side not in (0, 1) or side in seen_sides):
                raise RigFormatError("握持校准索引/左右手无效或重复")
            source = targets[hand]["source_index"]
            if source < 0 or sources[source]["name"] != ("r_hand", "l_hand")[side]:
                raise RigFormatError("握持校准没有对应到正确的手腕语义")
            chain, cursor = [], hand
            while cursor >= 0:
                bone = targets[cursor]
                si = bone["source_index"]
                if si >= 0 and "source_parent_space" in bone.get("flags", []):
                    chain.append(sources[si]["name"])
                cursor = bone["parent"]
            prefix = ("r_", "l_")[side]
            if not all(prefix + suffix in chain for suffix in ("hand", "elbow", "shoulder")):
                raise RigFormatError("握持校准需要完整映射的肩肘手链")
            local = _matrix(grip.get("hand_to_grip"), "hand_grip")
            seen_sides.add(side)
            grips += GRIP.pack(pi, hand, side, 0, *local)
    if grips:
        payload += struct.pack("<8sII", b"HD2GRIP1", len(grips)//GRIP.size, 0) + grips
    summary["hand_grips"] = len(grips)//GRIP.size
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    summary["payload_crc32"] = f"{crc:08x}"
    # Old receivers reject this extension bit instead of silently ignoring the
    # gender policy. The record layout stays HD2IRG1; no trailing data is added.
    extensions = int(bool(grips)) | (2 if declared_gender else 0)
    return HEADER.pack(MAGIC, VERSION, HEADER.size, len(sources), len(profiles), crc, extensions) + payload, summary


__all__ = ("RigFormatError", "build", "rest_globals")
