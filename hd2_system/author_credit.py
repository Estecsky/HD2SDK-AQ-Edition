"""Public integration only; the optional encryption helper is binary-only.

An author declaration is not identity verification. No URL is opened here.
"""
from datetime import datetime
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit


def validate(name, homepage):
    name, homepage = name.strip(), homepage.strip()
    if len(name) > 80 or len(homepage) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in name + homepage):
        raise ValueError('作者名限 80 字、主页限 512 字，不能含控制字符')
    if homepage:
        url = urlsplit(homepage)
        if not name or url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password:
            raise ValueError('请填写作者名；主页仅支持不带登录信息的 HTTP/HTTPS 地址')
    return name, homepage


def attach(compiled, plan, files, name, homepage, *, helper=None):
    name, homepage = validate(name, homepage)
    if not name:
        return  # Legacy/anonymous packages remain supported, never fabricate a name.
    if any(n.endswith('.hd2author') for n in files):
        raise ValueError('作者配套已存在，不能重复加密')
    payload = dict(schema='HD2AuthorDeclaration1', name=name, homepage=homepage,
                   project_id=plan['project_id'], archive_name=plan['archive_name'],
                   exported_at=datetime.now().astimezone().isoformat(),
                   files=[dict(path=n, sha256=hashlib.sha256(b).hexdigest()) for n, b in sorted(files.items())])
    executable = Path(helper) if helper else Path(__file__).resolve().parents[1] / 'helpers/HD2AuthorSeal.exe'
    if os.name != 'nt' or not executable.is_file():
        raise ValueError('作者信息加密器不可用，请安装包含辅助 EXE 的完整 Windows 插件包')
    result = subprocess.run([str(executable), '--encrypt-stdin'], input=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf8'),
                            capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError('作者信息加密失败，未写出配套包')
    try:
        envelope = base64.b64decode(result.stdout.strip(), validate=True)
    except ValueError as error:
        raise ValueError('作者信息加密器返回无效内容') from error
    if not 425 <= len(envelope) <= 33192 or envelope[:8] != b'HD2AUTH1':
        raise ValueError('作者信息密文格式无效')
    from .physics_compiler import _runtime_stem
    # Shared packs contain three differently named Rig files. The author
    # identity belongs to the project/archive, not arbitrary dict order.
    _project, stem = _runtime_stem(plan)
    compiled['files'][stem + '.hd2author'] = envelope
