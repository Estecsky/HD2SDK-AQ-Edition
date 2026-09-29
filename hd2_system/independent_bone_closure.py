"""Independent Unit Rest dependencies, separate from skin/physics ownership.

All body pieces use the same authored public animation skeleton, including
unweighted IK/twist/grip landmarks. Custom bones still belong only to their
actual mesh, light or physics consumers; empty vertex groups are not consumers.
"""

from .independent_packaging import BODY_PART_SLOTS
from .unit_rig_profiles import load_avatar_source


def independent_export_bones(bones, used_bone_names, part_slot):
    """Return authored bones plus ancestors, in the original armature order.

    This is called only for disposable independent-save meshes. Do not add
    vertex groups, fake weights, or physical consumers to keep Rest dependencies.
    Head/non-body exports retain their original weighted/physics/light closure.
    """
    bones = list(bones)
    by_name = {bone.name: bone for bone in bones}
    required = set(used_bone_names)
    if part_slot in BODY_PART_SLOTS:
        # The profile builder maps these exact public names. Include fingers as
        # well: hand-grip fitting can consume their unweighted Rest landmarks.
        required.update(bone['name'] for bone in load_avatar_source()
                        if bone['name'] in by_name)
    closure = set()
    for name in required:
        bone = by_name.get(name)
        while bone is not None and bone.name not in closure:
            closure.add(bone.name)
            bone = bone.parent
    return [bone for bone in bones if bone.name in closure]
