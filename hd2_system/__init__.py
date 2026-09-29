"""HD2 独立骨架/物理/差分封包扩展。"""

from .independent_packaging import (  # noqa: F401
    IndependentPackagingError,
    build_package_plan,
    load_part_catalog,
)
from .sdk_adapter import (  # noqa: F401
    SDKAdapterError,
    SaveJob,
    apply_temporary_target,
    atomic_write_json,
    build_save_jobs,
    format_save_job_error,
    capture_target_properties,
    format_plan_summary,
    restore_target_properties,
    split_plan_summary_lines,
)
from .physics_packaging import (  # noqa: F401
    PhysicsPackagingError,
    all_published_unit_ids,
    build_armor_physics_project,
    published_unit_rows,
    required_custom_bones_by_unit,
    required_profile_bones_by_unit,
)
from .unit_rig_profiles import (  # noqa: F401
    UnitRigProfileError,
    build_rig_document,
    required_physics_bones,
    snapshot_from_loaded_unit,
)
from .physics_compiler import (  # noqa: F401
    PhysicsCompileError,
    compile_physics_pack,
    compile_rig_pack,
)
from .runtime_manifest import (  # noqa: F401
    RuntimeManifestError,
    build_runtime_difference_manifest,
    load_runtime_target_catalog,
)
