"""One named ZIP of separately saved runtime members; never model/material data."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

LIMIT = 256 * 1024 * 1024
LEAF = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(rigbin|hd2phys|hd2shared|hd2pose|hd2preview|ini|hd2author)\Z')
IDENTITY = re.compile(r'([0-9a-f]{32})\.([0-9a-f]{16})\.rigbin\Z')


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def read_member(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError('配套 ZIP 缺失或超过 256 MiB')
    original = path.read_bytes()
    files = {}
    with zipfile.ZipFile(path) as archive:
        if not 1 <= len(archive.infolist()) <= 4096:
            raise ValueError('配套 ZIP 文件计数无效')
        total = 0
        for entry in archive.infolist():
            name = entry.filename
            if (not name.startswith('HD2IndependentRig/') or
                    not LEAF.fullmatch(name[len('HD2IndependentRig/'):]) or
                    (entry.external_attr >> 16) & 0xf000 == 0xa000):
                raise ValueError('仅允许独立骨/物理等运行时配套，不能混入模型、材质或嵌套组')
            total += entry.file_size
            if total > LIMIT or entry.file_size > LIMIT or name.lower() in {n.lower() for n in files}:
                raise ValueError('配套 ZIP 超限或含重复路径')
            files[name] = archive.read(entry)
    identities = set()
    for name, data in files.items():
        match = IDENTITY.fullmatch(name.split('/')[-1])
        if match:
            identities.add(match.groups())
        if name.endswith('.hd2shared'):
            from .shared_physics import HEADER, MAGIC
            if len(data) < HEADER.size:
                raise ValueError('共享物理清单截断')
            h = HEADER.unpack_from(data)
            if (h[0] != MAGIC or h[1] not in (2,3) or h[2] != HEADER.size or h[3] != len(data)
                    or (h[1]==3 and h[6] not in (2,3))):
                raise ValueError('共享物理清单格式无效')
            identities.add((h[7].split(b'\0')[0].decode('ascii'), f'{h[8]:016x}'))
    if len(identities) != 1:
        raise ValueError('每个输入包须为单独保存的一套身体或头盔 Archive')
    project, archive = next(iter(identities))
    if not re.fullmatch('[0-9a-f]{32}', project):
        raise ValueError('配套项目身份无效')
    if path.read_bytes() != original:
        raise ValueError('读取期间配套 ZIP 已改变')
    return dict(project=project, archive=archive, files=files)


def write_group(inputs, target, *, identity, name, overwrite=False):
    name = name.strip()
    if (not re.fullmatch('[0-9a-f]{32}', identity) or not name or len(name) > 80 or
            any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError('Mod 组身份或名称无效')
    paths = [Path(p).resolve() for p in inputs]
    target = Path(target).absolute()
    if not 1 <= len(paths) <= 64 or len(set(paths)) != len(paths) or target.resolve() in paths:
        raise ValueError('输入配套数量无效、重复或输出覆盖输入')
    if target.exists() and not overwrite:
        raise ValueError('目标已存在；请明确允许覆盖或另选文件名')
    files, members, identities = {}, [], set()
    for path in paths:
        member = read_member(path)
        key = member['project'], member['archive']
        if key in identities or any(archive==key[1] for _,archive in identities):
            raise ValueError('同一 Archive 重复；请选择其最新配套包，不叠加不同项目')
        identities.add(key)
        rows = []
        for filename, data in sorted(member['files'].items()):
            if filename.lower() in {n.lower() for n in files}:
                raise ValueError('成员运行时路径冲突，不能覆盖或静默合并')
            files[filename] = data
            rows.append(dict(path=filename, sha256=_hash(data)))
        members.append(dict(project=key[0], archive=key[1], files=rows))
    if len(files) > 4096 or sum(map(len,files.values())) > LIMIT:
        raise ValueError('合并配套超出总容量')
    now = datetime.now().astimezone()
    manifest = dict(schema='HD2RuntimeGroup1', id=identity, name=name,
        exported_at=now.isoformat(), members=sorted(members,key=lambda m:(m['project'],m['archive'])))
    files['mod_group.json'] = (json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf8')
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=target.name+'.', suffix='.tmp', dir=target.parent)
    os.close(handle)
    try:
        with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for filename,data in sorted(files.items()):
                info=zipfile.ZipInfo(filename, now.timetuple()[:6])
                info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,data)
        with zipfile.ZipFile(temp) as archive:
            if any(archive.read(n) != b for n,b in files.items()):
                raise ValueError('合并 ZIP 回读不一致')
        if overwrite:
            os.replace(temp,target)
        else:
            os.link(temp,target)  # atomic no-clobber on the destination volume
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return manifest
