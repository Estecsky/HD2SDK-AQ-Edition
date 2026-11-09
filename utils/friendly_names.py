"""UTF-8 friendly-name tables shared by the SDK's resource-name stores."""
import os
from pathlib import Path
import tempfile


ANIMATION_HEADER = (
    '# Animation friendly names (UTF-8)\n'
    '# <decimal FileID> <friendly name>\n'
    '# Empty names hide legacy labels from friendlynames.txt.\n'
)


def normalize_name(name):
    name = str(name).strip()
    if any(char in name for char in ('\n', '\r', '\x00')):
        raise ValueError('Friendly Name must be a single line without NUL characters')
    return name


def load_names(path, allow_empty=False):
    """Keep the first duplicate, matching the original general-name loader."""
    path = Path(path)
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        return {}
    names = {}
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        parts = line.split(maxsplit=1)
        try:
            file_id = int(parts[0])
            if not 0 <= file_id < 2**64 or (len(parts) != 2 and not allow_empty):
                raise ValueError()
            name = normalize_name(parts[1] if len(parts) == 2 else '')
        except ValueError as exc:
            raise ValueError(f'{path}:{number}: invalid friendly-name record') from exc
        if name or allow_empty:
            names.setdefault(file_id, name)
    return names


def save_names(path, names, header='', keep_empty=False):
    """Replace one complete table; failed writes leave the old file intact."""
    path = Path(path)
    rows = []
    for file_id, value in names.items():
        file_id = int(file_id)
        if not 0 <= file_id < 2**64:
            raise ValueError('Resource FileID must fit in an unsigned 64-bit integer')
        name = normalize_name(value)
        if name or keep_empty:
            rows.append(f'{file_id} {name}\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', newline='\n',
                dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp',
                delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(header)
            stream.writelines(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
