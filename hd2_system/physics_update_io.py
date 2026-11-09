"""Read saved model bytes and commit companion batches without Blender state."""
from copy import copy
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile
from types import SimpleNamespace
import zipfile

from .physics_packaging import PhysicsPackagingError

SUFFIX = '.hd2irpack.zip'
DOMAIN_NAMES = {'BODY': '身体', 'HELMET': '头盔'}
UNIT = 0xE0A48D0BE9A7453F


class SavedPatch:
    def __init__(self, path, name, entries):
        self.Path, self.Name, self.TocEntries = str(path), name, entries
        self._entries = {(e.FileID, e.TypeID): e for e in entries}

    def GetEntry(self, file_id, type_id):
        return self._entries.get((int(file_id), int(type_id)))


def parse_patch(blobs, *, path='', name='', password=0):
    """Decode the package table with explicit bounds; never load Blender assets."""
    toc, gpu, stream = blobs
    if len(toc) < 72:
        raise PhysicsPackagingError('已保存 Patch 文件损坏：文件头不完整')
    magic, kinds, count = struct.unpack_from('<III', toc)
    start = 72 + kinds * 32
    if magic != 4026531857 or start + count * 80 > len(toc):
        raise PhysicsPackagingError('已保存 Patch 文件损坏：格式或条目表无效')
    entries, seen = [], set()
    for offset in range(start, start + count * 80, 80):
        uid, kind, at, stream_at, gpu_at = struct.unpack_from('<5Q', toc, offset)
        size, stream_size, gpu_size = struct.unpack_from('<3I', toc, offset + 56)
        stream_size ^= password
        if (uid, kind) in seen:
            raise PhysicsPackagingError('已保存 Patch 文件损坏：包含重复资源')
        seen.add((uid, kind))
        payloads = []
        for blob, pos, length in ((toc, at, size), (gpu, gpu_at, gpu_size), (stream, stream_at, stream_size)):
            if length and (pos > len(blob) or length > len(blob) - pos):
                raise PhysicsPackagingError('已保存 Patch 的主文件、GPU 或 Stream 数据缺失/截断')
            payloads.append(bytes(blob[pos:pos + length]) if length else b'')
        entries.append(SimpleNamespace(FileID=uid, TypeID=kind, TocData=payloads[0],
            GpuData=payloads[1], StreamData=payloads[2], LoadedData=None, IsLoaded=False))
    return SavedPatch(path, name or Path(path).name, entries)


def read_patch(path, archive_name, *, member='', password=0):
    """Read either a loose three-part Patch or its saved ZIP, without extraction."""
    path = Path(path)
    name = member or path.name
    if not re.fullmatch(re.escape(archive_name) + r'\.patch_\d+', name, re.IGNORECASE):
        raise PhysicsPackagingError('已保存 Patch 的名称与目标 Archive 不匹配')
    if not path.is_file():
        raise PhysicsPackagingError(f'找不到已保存 Patch 文件：{path}；请恢复原文件或指定它的新位置，无需重存模型')
    try:
        if member:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)):
                    raise PhysicsPackagingError('模型 ZIP 包含重复文件名，无法确定已保存 Patch')
                if member not in names:
                    raise PhysicsPackagingError(f'模型 ZIP 中找不到已保存 Patch：{member}')
                blobs = [archive.read(member + suffix) if member + suffix in names else b''
                         for suffix in ('', '.gpu_resources', '.stream')]
        else:
            blobs = [Path(str(path) + suffix).read_bytes() if Path(str(path) + suffix).is_file() else b''
                     for suffix in ('', '.gpu_resources', '.stream')]
        return parse_patch(blobs, path=str(path), name=name, password=password)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
        if isinstance(error, PhysicsPackagingError):
            raise
        raise PhysicsPackagingError(f'无法读取已保存 Patch：{path}（{error}）') from error


def units_digest(patch, unit_ids, *, bones_restore=None):
    digest = hashlib.sha256()
    for uid in unit_ids:
        entry = patch.GetEntry(uid, UNIT)
        if entry is None:
            raise PhysicsPackagingError(f'已保存 Patch 缺少目标 Unit {uid:016x}；请检查是否选错文件')
        toc = entry.TocData
        if bones_restore is not None and uid in bones_restore:
            toc = bytearray(toc)
            struct.pack_into('<Q', toc, 8, bones_restore[uid])
        digest.update(struct.pack('<Q', uid))
        for payload in (toc, entry.GpuData, entry.StreamData):
            digest.update(hashlib.sha256(bytes(payload)).digest())
    return digest.hexdigest()


def copy_patch(patch):
    """Reuse immutable payloads, discard decoded state which may be stale."""
    entries = [copy(entry) for entry in patch.TocEntries]
    for entry in entries:
        entry.LoadedData, entry.IsLoaded = None, False
    return SavedPatch(patch.Path, patch.Name, entries)


def choose_targets(records, project_id, content_domain, body_target='', helmet_target=''):
    if content_domain not in {'BODY', 'HELMET', 'BOTH'}:
        raise PhysicsPackagingError('请选择身体、头盔或身体＋头盔')
    selected = []
    for domain in (('BODY', 'HELMET') if content_domain == 'BOTH' else (content_domain,)):
        label = DOMAIN_NAMES[domain]
        candidates = {key: value for key, value in records.items()
                      if value['plan']['project_id'] == project_id and value['plan']['content_domain'] == domain}
        target = body_target if domain == 'BODY' else helmet_target
        if not candidates:
            raise PhysicsPackagingError(f'找不到{label}保存记录：请打开保存过该部位记录的 .blend；首次制作时需保存该部位一次')
        if target and target not in candidates:
            raise PhysicsPackagingError(f'{label}所选保存记录已失效或属于其他项目，请重新打开导出窗口')
        if not target and len(candidates) != 1:
            raise PhysicsPackagingError(f'{label}有多个已保存目标，请在导出窗口明确选择 Archive / Patch')
        key = target or next(iter(candidates))
        selected.append((key, candidates[key]))
    return selected


def output_paths(filepath, domains):
    path = Path(filepath)
    if not path.name.lower().endswith(SUFFIX) or len(path.name) <= len(SUFFIX):
        raise PhysicsPackagingError('请选择 .hd2irpack.zip 配套包文件名')
    if len(domains) == 1:
        return [path]
    stem = path.name[:-len(SUFFIX)]
    return [path.with_name(stem + '_' + DOMAIN_NAMES[d] + SUFFIX) for d in domains]


def commit_batch(paths, writers):
    """Stage every ZIP, then replace all destinations; restore on commit failure."""
    paths = [Path(path) for path in paths]
    if len(paths) != len(writers) or not paths or len({os.path.normcase(str(p.resolve())) for p in paths}) != len(paths):
        raise PhysicsPackagingError('批量导出文件目标无效或重名')
    parent = paths[0].parent
    if any(p.parent != parent for p in paths):
        raise PhysicsPackagingError('批量配套包必须保存到同一目录')
    if any(p.exists() and not p.is_file() for p in paths):
        raise PhysicsPackagingError('配套包目标被同名文件夹占用')
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.hd2physics-', dir=parent))
    published, backups = [], {}
    keep_recovery = False
    try:
        for index, (path, writer) in enumerate(zip(paths, writers)):
            staged = staging / path.name
            writer(staged)
            with zipfile.ZipFile(staged) as archive:
                if archive.testzip() is not None:
                    raise PhysicsPackagingError('批量配套包回读失败')
            if path.exists():
                backup = staging / f'previous-{index}.zip'
                shutil.copy2(path, backup)
                backups[path] = backup
        try:
            for path in paths:
                os.replace(staging / path.name, path)
                published.append(path)
        except BaseException as error:
            failures = []
            for path in reversed(published):
                try:
                    if path in backups:
                        os.replace(backups[path], path)
                    else:
                        path.unlink()
                except OSError as restore_error:
                    failures.append(str(restore_error))
            if failures:
                keep_recovery = True
                raise PhysicsPackagingError(f'批量写入失败且回滚未完成；原包备份保留在 {staging}：' + '；'.join(failures)) from error
            raise
    finally:
        if not keep_recovery:
            shutil.rmtree(staging, ignore_errors=True)
    return [str(path) for path in paths]
