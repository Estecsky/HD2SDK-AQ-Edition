"""Resolve registered companion addons without loading a second package instance.

Keep this dependency-free helper identical across the three addon distributions.
Names identify installation packages, never saved properties or wire-format keys.
"""
import importlib
import sys

ALIASES = {
    "PHYSBONE": ("HD2PhysBoneTool", "HD2-PhysBoneTool"),
    "BATCH": ("HD2BatchTool", "HD2_Batch_Tool", "HD2-BatchTool"),
}
LABELS = {"PHYSBONE": "HD2 PhysBone Tool", "BATCH": "HD2 Batch Tool"}


def _matches_package_name(name, role):
    """Accept known install roots and GitHub's default-branch source ZIP roots."""
    leaf = name.rsplit(".", 1)[-1]
    if leaf.endswith("-main"):
        leaf = leaf[:-5]
    return leaf in ALIASES[role]


def resolve_addon(role, *, context=None, error_type=RuntimeError):
    """Use enabled packages or explicitly registered source-package instances.

    Imported-but-disabled modules are not usable. A second active distribution
    is ambiguous because both distributions own the same Blender RNA/operators.
    No root import or cross-package alias is installed as a side effect.
    """
    if context is None:
        import bpy
        context = bpy.context
    enabled = {item.module for item in
               getattr(getattr(context, "preferences", None), "addons", ())}
    candidates = {}
    for name, module in tuple(sys.modules.items()):
        if module is None or not _matches_package_name(name, role):
            continue
        # Ignore aliases pointing to another root: import children only through
        # the module's actual package so all collaborators share one RNA class.
        if getattr(module, "__name__", None) != name:
            continue
        registered = getattr(module, "_REGISTERED", None)
        if registered is not True and not (name in enabled and registered is not False):
            continue
        candidates[id(module)] = module
    label = LABELS[role]
    if len(candidates) > 1:
        raise error_type(f"检测到多个已启用/注册的 {label}；请仅启用其中一个版本并重启 Blender")
    if not candidates:
        raise error_type(f"此操作需要启用配套的 {label}，然后重试")
    module = next(iter(candidates.values()))
    if getattr(module, "_DIRTY", False):
        raise error_type(f"{label} 注册/清理不完整，请重启 Blender 后重试")
    return module


def companion_module(role, suffix, *, context=None, error_type=RuntimeError):
    addon = resolve_addon(role, context=context, error_type=error_type)
    try:
        return importlib.import_module(f"{addon.__name__}.{suffix}")
    except ImportError as error:
        # A real internal dependency failure must not trigger another package.
        raise error_type(f"{LABELS[role]} 的 {suffix} 加载失败：{error}；请更新配套版本") from error
