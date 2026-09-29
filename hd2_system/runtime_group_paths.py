"""Path rules shared by the runtime companion group UI and its tests."""
import os
import re

SUFFIX = '.hd2irpack.zip'


def absolute_path(value, abspath=os.path.abspath):
    """Caller supplies bpy.path.abspath to resolve Blender // paths."""
    value = str(value).strip()
    return os.path.abspath(abspath(value)) if value else ''


def output_path(value):
    """Normalize the compound suffix idempotently; never alter directories."""
    directory, leaf = os.path.split(value)
    # Also repair names produced by the old repeated-extension callback.
    leaf = re.sub(r'(?:\.hd2irpack)+(?:\.zip)?$', '', leaf, flags=re.I)
    if leaf.lower().endswith('.zip'):
        leaf = leaf[:-4]
    if not leaf:
        leaf = 'Mod 配套组'
    return os.path.join(directory, leaf + SUFFIX)


def validate_inputs(values, *, count=2):
    paths = [os.path.abspath(value) for value in values if value]
    if len(paths) != count:
        raise ValueError(f'请选择 {count} 个独立配套 ZIP 文件')
    if len({os.path.normcase(os.path.realpath(p)) for p in paths}) != len(paths):
        raise ValueError('不能重复选择同一个配套文件')
    for path in paths:
        if not path.lower().endswith(SUFFIX):
            raise ValueError('输入文件须以 .hd2irpack.zip 结尾')
        if not os.path.isfile(path):
            raise ValueError(f'配套文件不存在：{path}')
    return paths
