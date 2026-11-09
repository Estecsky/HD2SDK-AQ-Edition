"""Per-material authoring labels, independent of the SDK template catalogue.

The JSON lives on the Blender material. It changes presentation only; it never
authorizes GPU identity edits or changes serialized material values.
"""
from functools import lru_cache
import json

PROPERTY = 'HD2SDK_CustomMaterialEditor'
SCHEMA = 'HD2CustomMaterialEditor1'


@lru_cache(maxsize=128)
def _decode(raw):
    if not isinstance(raw, str) or len(raw) > 262144:
        return None
    try:
        data = json.loads(raw)
        if data.get('schema') != SCHEMA:
            return None
        slots = tuple(data['texture_slots'])
        variables = frozenset(tuple(row) for row in data['parameter_schema'])
        textures = tuple((row['index'], str(row['label'])) for row in data['textures'])
        parameters = tuple((row['id'], str(row['name']), str(row.get('zh', row['name'])))
                           for row in data['parameters'])
        if (len({i for i, _ in textures}) != len(textures)
                or any(type(i) is not int or not 0 <= i < len(slots) for i, _ in textures)
                or len({row[0] for row in parameters}) != len(parameters)
                or not {row[0] for row in parameters}.issubset({row[0] for row in variables})):
            return None
        return slots, variables, textures, parameters, str(data.get('name', '自定义材质'))
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def editor_view(material, blend_material):
    """Return optional cached labels and current real indices; no file/GPU reads."""
    if blend_material is None:
        return None
    record = _decode(blend_material.get(PROPERTY, ''))
    if record is None:
        return None
    slots, schema, textures, parameters, name = record
    actual = frozenset((v.ID, v.klass, v.elements, v.elementStride)
                       for v in material.ShaderVariables)
    if tuple(material.TexUnks) != slots or actual != schema:
        return None
    indexed = {v.ID: (i, v) for i, v in enumerate(material.ShaderVariables)}
    return (name, list(textures), [indexed[key] for key, _, _ in parameters],
            {key: (english, chinese) for key, english, chinese in parameters})
