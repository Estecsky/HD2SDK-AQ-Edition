"""Persistent save records scoped to a project, Archive, Patch and content domain.

This stores metadata only. Export must still validate the active Archive/Patch,
the current semantic plan and the actual Unit payloads in the receiving layer.
"""
from copy import deepcopy
import json
import re

SCHEMA = 'HD2SavedPackageContexts1'


class ContextError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContextError('已保存封包记录包含重复键，请重新保存')
        result[key] = value
    return result


def _nonfinite(value):
    raise ContextError('已保存封包记录包含非有限数值')


def _decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=_object,
                          parse_constant=_nonfinite)
    except (TypeError, json.JSONDecodeError) as error:
        raise ContextError('已保存封包记录损坏，请重新保存') from error


def identity(project_id, archive_name, patch_name, domain):
    project, archive, patch = (str(value).strip().lower()
                               for value in (project_id, archive_name, patch_name))
    if not re.fullmatch(r'[0-9a-f]{32}', project):
        raise ContextError('已保存封包记录的项目 ID 无效')
    if not re.fullmatch(r'[0-9a-f]{16}', archive):
        raise ContextError('已保存封包记录的 Archive 无效')
    if not re.fullmatch(re.escape(archive) + r'\.patch_\d+', patch):
        raise ContextError('已保存封包记录的 Patch 与 Archive 不一致')
    if domain not in {'BODY', 'HELMET'}:
        raise ContextError('已保存的身体/头盔存储范围无效')
    return '|'.join((project, archive, patch, domain))


def load(raw):
    if not raw:
        return {}
    data = _decode(raw)
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or not isinstance(data.get('records'), dict):
        raise ContextError('已保存封包记录版本或结构无效')
    for key, record in data['records'].items():
        if not isinstance(record, dict) or not isinstance(record.get('plan'), dict):
            raise ContextError('已保存封包记录缺少部位计划')
        plan = record['plan']
        expected = identity(plan.get('project_id'), plan.get('archive_name'),
                            record.get('patch_name'), plan.get('content_domain'))
        if key != expected:
            raise ContextError('已保存封包记录的身份键不一致')
        revision = record.get('physics_revision')
        if revision is not None and (not isinstance(revision, dict) or any(
                revision.get(field) != plan.get(field)
                for field in ('project_id', 'archive_name', 'content_domain'))):
            raise ContextError('物理更新校验记录与部位计划不一致')
    return data['records']


def store(raw, plan, patch_name, physics_revision):
    records = load(raw)
    key = identity(plan.get('project_id'), plan.get('archive_name'), patch_name,
                   plan.get('content_domain'))
    prefix = key.rsplit('|', 1)[0] + '|'
    if any(candidate.startswith(prefix) and candidate != key for candidate in records):
        raise ContextError('同一 Patch 不能混存身体和头盔，请分别存储头和身体')
    records[key] = dict(plan=deepcopy(plan), patch_name=str(patch_name).lower(),
                        physics_revision=deepcopy(physics_revision))
    result = json.dumps(dict(schema=SCHEMA, records=records), ensure_ascii=False,
                        sort_keys=True, separators=(',', ':'), allow_nan=False)
    load(result)  # Reject a mismatched revision before caller mutates its scene.
    return result


def legacy_record(raw_plan, raw_revision):
    if not raw_plan:
        return None
    plan = _decode(raw_plan)
    if not isinstance(plan, dict) or plan.get('content_domain') not in {'BODY', 'HELMET'}:
        raise ContextError('旧封包计划缺少有效身体/头盔范围，请重新保存')
    revision = _decode(raw_revision) if raw_revision else None
    if revision is not None and (not isinstance(revision, dict) or any(
            revision.get(field) != plan.get(field)
            for field in ('project_id', 'archive_name', 'content_domain'))):
        raise ContextError('旧物理更新记录与部位计划不一致，请重新保存')
    return dict(plan=plan, physics_revision=revision)


def find(raw, project_id, archive_name, patch_name, *, raw_legacy_plan='', raw_legacy_revision=''):
    prefix = identity(project_id, archive_name, patch_name, 'BODY').rsplit('|', 1)[0] + '|'
    matching = [record for key, record in load(raw).items() if key.startswith(prefix)]
    if len(matching) > 1:
        raise ContextError('同一 Patch 同时出现身体和头盔记录，请分开保存')
    if matching:
        return deepcopy(matching[0])
    legacy = legacy_record(raw_legacy_plan, raw_legacy_revision)
    if legacy and (str(legacy['plan'].get('project_id', '')).lower() == str(project_id).lower()
                   and str(legacy['plan'].get('archive_name', '')).lower() == str(archive_name).lower()):
        return legacy
    return None
