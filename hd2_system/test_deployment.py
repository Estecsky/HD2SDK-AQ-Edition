"""Closed-game test deployment of one model Patch and its runtime companions.

No ZIP round trip, no fixed drive, and no access to unrelated runtime files.
The supplied writer stages the actual SDK Archive before any final replacement.
Existing files with the same names are replaced directly. Old bytes exist only
inside the transaction's temporary staging directories for failure rollback and
are never retained as user backups after the operation finishes.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

PATCH_SUFFIXES = ("", ".gpu_resources", ".stream")
RUNTIME_SUFFIXES = (".rigbin", ".hd2phys", ".hd2shared", ".hd2pose", ".hd2preview", ".ini", ".hd2author")


class TestDeploymentError(ValueError):
    """A test package cannot be safely installed into this game directory."""


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_payloads(compiled, *companions):
    files = {}
    for name, data in (() if compiled is None else compiled["files"].items()):
        if not isinstance(name, str):
            raise TestDeploymentError("运行时配套文件名无效")
        if name.endswith(".hd2physpack.json"):
            continue
        files[name] = data
    for companion in companions:
        if companion is not None:
            name = companion["file_name"]
            if name in files:
                raise TestDeploymentError("运行时配套文件名重复：" + name)
            files[name] = companion["text"].encode("utf-8")
    for name, data in files.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
                or name in {".", ".."} or not name.endswith(RUNTIME_SUFFIXES)
                or not isinstance(data, bytes)):
            raise TestDeploymentError("运行时配套文件名或数据类型无效")
    return files


def require_game_closed():
    if os.name != "nt":
        raise TestDeploymentError("当前平台无法确认游戏已关闭，未执行测试部署")
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq helldivers2.exe", "/FO", "CSV", "/NH"],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode or b"helldivers2.exe" in result.stdout.lower():
        raise TestDeploymentError("请先正常关闭绝地潜兵2，再点击测试 Mod；不会强制结束游戏")


def deploy(data_directory, patch_name, writer, runtime_files, *,
           idle_check=require_game_closed, on_committed=None):
    """Stage, verify, and directly replace same-name test deployment files."""
    requested = Path(data_directory).absolute()
    data_dir = requested.resolve()
    bin_dir = (requested.parent / "bin").resolve()
    if not data_dir.is_dir() or not (bin_dir / "helldivers2.exe").is_file():
        raise TestDeploymentError("请选择实际游戏 data 目录，并确认相邻 bin 中存在 helldivers2.exe")
    if not re.fullmatch(r"[0-9a-f]{16}\.patch_\d+", patch_name):
        raise TestDeploymentError("测试 Patch 文件名无效")
    # The same validation also covers externally injected test fixtures.
    runtime_files = runtime_payloads({"files": runtime_files})
    idle_check()
    runtime_dir = bin_dir / "HD2IndependentRig"
    runtime_dir.mkdir(exist_ok=True)
    runtime_dir = runtime_dir.resolve()
    patches = [data_dir / (patch_name + suffix) for suffix in PATCH_SUFFIXES]
    targets = patches + [runtime_dir / name for name in runtime_files]
    for path in targets:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise TestDeploymentError("测试部署目标不是普通文件：" + path.name)
    stage_data = None
    stage_runtime = None
    installed = []
    rollback = {}
    before, after, sources = {}, {}, {}

    try:
        stage_data = Path(tempfile.mkdtemp(prefix=".hd2sdk-stage-", dir=data_dir))
        stage_runtime = Path(tempfile.mkdtemp(prefix=".hd2sdk-stage-", dir=runtime_dir))
        writer(stage_data / patch_name)
        for suffix, target in zip(PATCH_SUFFIXES, patches):
            source = stage_data / (patch_name + suffix)
            if not source.is_file() or source.is_symlink():
                raise TestDeploymentError("SDK 未生成完整的三个 Patch 文件")
            sources[target] = source
        for name, payload in runtime_files.items():
            source = stage_runtime / name
            source.write_bytes(payload)
            if source.read_bytes() != payload:
                raise TestDeploymentError("运行时暂存回读不一致：" + name)
            sources[runtime_dir / name] = source
        before = {path: _sha(path) if path.exists() else None for path in sources}
        after = {path: _sha(source) for path, source in sources.items()}
        idle_check()
        # Rollback bytes live only inside the exact staging directories. They
        # are removed in finally after success or rollback, so the test button
        # never creates a retained backup directory or transaction receipt.
        for index, (path, digest) in enumerate(before.items()):
            if digest is None or digest == after[path]:
                continue
            stage = stage_data if path.parent == data_dir else stage_runtime
            saved = stage / f".rollback-{index}-{path.name}"
            shutil.copy2(path, saved)
            if _sha(saved) != digest:
                raise TestDeploymentError("临时回退副本校验失败：" + path.name)
            rollback[path] = saved
        # Runtime companions precede the new model patch. Every write checks
        # closed-game and baseline state again; unchanged companions stay put.
        ordered = sorted(sources, key=lambda path: path.parent == data_dir)
        for target in ordered:
            idle_check()
            digest = _sha(target) if target.exists() else None
            if digest != before[target] or target.is_symlink():
                raise TestDeploymentError("部署期间目标发生变化：" + target.name)
            if digest == after[target]:
                continue
            source = sources[target]
            if digest is None:
                # Stage is on the same volume; link creation cannot replace a
                # file created by somebody else after the existence check.
                os.link(source, target)
            else:
                os.replace(source, target)
            installed.append(target)
            if _sha(target) != after[target]:
                raise TestDeploymentError("部署回读失败：" + target.name)
        if any(_sha(p) != h for p,h in after.items()):
            raise TestDeploymentError("最终部署内容不一致")
        if on_committed is not None:
            on_committed(patches[0], runtime_dir)
        return dict(patch_path=str(patches[0]), runtime_directory=str(runtime_dir),
                    files=len(after), updated=len(installed),
                    overwritten=sum(before[path] is not None and before[path] != after[path]
                                    for path in sources),
                    created=sum(before[path] is None for path in installed),
                    hashes={str(p):h for p,h in after.items()})
    except BaseException:
        try:
            for target in reversed(installed):
                idle_check()
                if not target.is_file() or target.is_symlink() or _sha(target) != after[target]:
                    raise TestDeploymentError("失败后文件被外部更改，未覆盖：" + str(target))
                if before[target] is None:
                    target.unlink()
                else:
                    saved = rollback.get(target)
                    if saved is None or _sha(saved) != before[target]:
                        raise TestDeploymentError("临时回退数据无效：" + target.name)
                    os.replace(saved, target)
                if before[target] is not None and _sha(target) != before[target]:
                    raise TestDeploymentError("失败回退校验不一致：" + target.name)
        except BaseException as rollback_error:
            raise TestDeploymentError(
                "测试部署失败且未能完整回退；按设置未保留永久备份，请重新生成并部署测试 Mod"
            ) from rollback_error
        raise
    finally:
        # Only exact directories allocated by this transaction are removed.
        # Attempt both cleanups even if the first one fails; a retained staging
        # directory could contain the temporary rollback bytes the user asked
        # this test workflow not to preserve.
        cleanup_errors = []
        for stage, parent in ((stage_data, data_dir), (stage_runtime, runtime_dir)):
            if stage is None:
                continue
            try:
                if stage.resolve().parent != parent or not stage.name.startswith(".hd2sdk-stage-"):
                    raise TestDeploymentError("暂存清理路径异常")
                shutil.rmtree(stage)
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        if cleanup_errors:
            raise TestDeploymentError("测试部署暂存清理不完整；请删除残留的 .hd2sdk-stage-* 目录") from cleanup_errors[0]
