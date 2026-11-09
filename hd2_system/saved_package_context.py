"""Persistent save records scoped to a project, Archive, Patch and content domain.

This stores metadata only. Export must still validate the selected Archive/Patch,
the current semantic plan and the actual Unit payloads in the receiving layer.
"""
from copy import deepcopy
import json
import math
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


def _validate_morph_recipe(recipe):
    """Validate the small persistent recipe, never accept embedded mesh payloads."""
    if (not isinstance(recipe, dict)
            or set(recipe) != {'schema', 'source_sha256', 'auto_lods', 'save_use_auto_smooth', 'units'}
            or recipe.get('schema') != 'HD2MorphRecipe1'
            or not isinstance(recipe.get('source_sha256'), str)
            or re.fullmatch(r'[0-9a-f]{64}', recipe['source_sha256']) is None
            or type(recipe.get('auto_lods')) is not bool
            or type(recipe.get('save_use_auto_smooth')) is not bool):
        raise ContextError('形态键重建记录的版本、摘要、LOD 或平滑设置无效')
    units = recipe['units']
    if not isinstance(units, dict) or not 1 <= len(units) <= 65536:
        raise ContextError('形态键重建记录的 Unit 数量无效')
    mesh_count = 0
    for unit_id, unit in units.items():
        if (not isinstance(unit_id, str) or re.fullmatch(r'[0-9a-f]{16}', unit_id) is None
                or not isinstance(unit, dict) or set(unit) != {'mesh_transform', 'meshes'}):
            raise ContextError('形态键重建记录的 Unit 身份或结构无效')
        matrix = unit['mesh_transform']
        if (not isinstance(matrix, list) or len(matrix) != 4
                or any(not isinstance(row, list) or len(row) != 4 for row in matrix)):
            raise ContextError('形态键重建记录的网格变换必须是 4×4 矩阵')
        for row in matrix:
            for value in row:
                try:
                    valid = type(value) in (int, float) and math.isfinite(value)
                except OverflowError:
                    valid = False
                if not valid:
                    raise ContextError('形态键重建记录的网格变换包含非法数值')
        meshes = unit['meshes']
        if not isinstance(meshes, dict) or not 1 <= len(meshes) <= 65536:
            raise ContextError('形态键重建记录的网格数量无效')
        mesh_count += len(meshes)
        if mesh_count > 65536:
            raise ContextError('形态键重建记录的总网格数量超过上限')
        for index, digest in meshes.items():
            if (not isinstance(index, str) or re.fullmatch(r'0|[1-9][0-9]{0,9}', index) is None
                    or int(index) > 0xffffffff or not isinstance(digest, str)
                    or re.fullmatch(r'[0-9a-f]{64}', digest) is None):
                raise ContextError('形态键重建记录的网格索引或映射摘要无效')


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
        if 'morph_recipe' in record:
            _validate_morph_recipe(record['morph_recipe'])
        for field in ('archive_path', 'patch_path'):
            if field in record and not isinstance(record[field], str):
                raise ContextError('已保存封包记录的文件路径无效')
        locator = record.get('export_patch')
        if locator is not None:
            if (not isinstance(locator, dict) or not isinstance(locator.get('path'), str)
                    or not locator['path'] or not isinstance(locator.get('member', ''), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', str(locator.get('units_sha256', '')))):
                raise ContextError('已导出 Patch 的路径或校验记录损坏')
        ids = record.get('archive_unit_ids')
        if ids is not None and (not isinstance(ids, list) or not ids
                or any(not isinstance(value, str) or not value.isdecimal() for value in ids)):
            raise ContextError('已保存基础 Archive 的 Unit 列表无效')
    return data['records']


def store(raw, plan, patch_name, physics_revision, *, archive_path='', patch_path='', archive_unit_ids=None,
          morph_recipe=None):
    records = load(raw)
    key = identity(plan.get('project_id'), plan.get('archive_name'), patch_name,
                   plan.get('content_domain'))
    prefix = key.rsplit('|', 1)[0] + '|'
    if any(candidate.startswith(prefix) and candidate != key for candidate in records):
        raise ContextError('同一 Patch 不能混存身体和头盔，请分别存储头和身体')
    previous = records.get(key, {})
    record = dict(plan=deepcopy(plan), patch_name=str(patch_name).lower(),
                  physics_revision=deepcopy(physics_revision),
                  archive_path=str(archive_path), patch_path=str(patch_path))
    if morph_recipe is not None:
        _validate_morph_recipe(morph_recipe)
        record['morph_recipe'] = deepcopy(morph_recipe)
    if archive_unit_ids:
        record['archive_unit_ids'] = sorted({str(int(value)) for value in archive_unit_ids}, key=int)
    # A new model save invalidates a previous disk baseline. Re-saving identical
    # inputs may retain the published locator, including an output ZIP member.
    if previous.get('physics_revision') == physics_revision and previous.get('export_patch'):
        record['export_patch'] = deepcopy(previous['export_patch'])
    records[key] = record
    result = json.dumps(dict(schema=SCHEMA, records=records), ensure_ascii=False,
                        sort_keys=True, separators=(',', ':'), allow_nan=False)
    load(result)  # Reject a mismatched revision before caller mutates its scene.
    return result


def bind_export(raw, plan, patch_name, path, units_sha256, *, member=''):
    """Remember a successfully written model, never replace its source baseline."""
    records = load(raw)
    key = identity(plan['project_id'], plan['archive_name'], patch_name, plan['content_domain'])
    if key not in records:
        raise ContextError('找不到此次模型导出对应的保存记录')
    records[key]['export_patch'] = dict(path=str(path), member=str(member), units_sha256=units_sha256)
    result = json.dumps(dict(schema=SCHEMA, records=records), ensure_ascii=False,
                        sort_keys=True, separators=(',', ':'), allow_nan=False)
    load(result)
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
