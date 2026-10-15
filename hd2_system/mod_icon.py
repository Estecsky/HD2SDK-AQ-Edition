"""Optional cosmetic ZIP metadata; never a game/runtime payload."""
from pathlib import Path
import struct
import zlib

MAX_PNG_DIMENSION = (1 << 31) - 1
MEMBER_NAMES = frozenset(('icon.png', 'icon.jpg', 'icon.jpeg'))
_JPEG_SOF_MARKERS = frozenset(range(0xc0, 0xd0)) - {0xc4, 0xc8, 0xcc}


def _validate_png(data):
    message = 'Mod 图标须为静态 PNG'
    if not isinstance(data, bytes) or len(data) < 57 or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError(message)
    offset, header, image, end = 8, False, False, False
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError('Mod 图标 PNG 数据截断')
        size, kind = struct.unpack_from('>I4s', data, offset)
        if size > len(data) - offset - 12:
            raise ValueError('Mod 图标 PNG 数据截断')
        if (not header and kind != b'IHDR') or kind == b'acTL':
            raise ValueError(message)
        payload = data[offset + 8:offset + 8 + size]
        if zlib.crc32(kind + payload) != struct.unpack_from('>I', data, offset + 8 + size)[0]:
            raise ValueError('Mod 图标 PNG 校验失败')
        if kind == b'IHDR':
            if header or size != 13:
                raise ValueError(message)
            width, height = struct.unpack_from('>II', payload)
            if not 1 <= width <= MAX_PNG_DIMENSION or not 1 <= height <= MAX_PNG_DIMENSION:
                raise ValueError('Mod 图标 PNG 尺寸无效')
            header = True
        if kind == b'IDAT':
            image = True
        if kind == b'IEND':
            if size or not image or offset + 12 != len(data):
                raise ValueError('Mod 图标 PNG 结束标记无效')
            end = True
        offset += size + 12
    if not (header and image and end):
        raise ValueError('Mod 图标 PNG 内容不完整')
    return data


def _validate_jpeg(data):
    """Validate marker structure, not Huffman/arithmetic pixel decoding."""
    offset, components, seen_scan = 2, None, False
    while offset < len(data):
        if data[offset] != 0xff:
            raise ValueError('Mod 图标 JPEG 标记无效')
        while offset < len(data) and data[offset] == 0xff:
            offset += 1
        if offset == len(data):
            raise ValueError('Mod 图标 JPEG 数据截断')
        marker = data[offset]
        offset += 1
        if marker == 0xd9:
            if components is None or not seen_scan or offset != len(data):
                raise ValueError('Mod 图标 JPEG 结束标记无效')
            return data
        if marker in (0x00, 0xd8) or 0xd0 <= marker <= 0xd7:
            raise ValueError('Mod 图标 JPEG 标记无效')
        if marker == 0x01:  # TEM is the other standalone marker.
            continue
        if offset + 2 > len(data):
            raise ValueError('Mod 图标 JPEG 数据截断')
        size = struct.unpack_from('>H', data, offset)[0]
        if size < 2 or offset + size > len(data):
            raise ValueError('Mod 图标 JPEG 分段长度无效')
        payload = data[offset + 2:offset + size]
        offset += size
        if marker in _JPEG_SOF_MARKERS:
            if components is not None or len(payload) < 6:
                raise ValueError('Mod 图标 JPEG 帧头无效')
            height, width = struct.unpack_from('>HH', payload, 1)
            count = payload[5]
            if not width or not height or not count or len(payload) != 6 + 3 * count:
                raise ValueError('Mod 图标 JPEG 尺寸或帧组件无效')
            components = {payload[6 + 3 * index] for index in range(count)}
            if len(components) != count:
                raise ValueError('Mod 图标 JPEG 帧组件重复')
            for index in range(count):
                sampling, table = payload[7 + 3 * index:9 + 3 * index]
                if not 1 <= sampling >> 4 <= 4 or not 1 <= (sampling & 0x0f) <= 4 or table > 3:
                    raise ValueError('Mod 图标 JPEG 帧组件无效')
        if marker != 0xda:
            continue
        if components is None or not payload:
            raise ValueError('Mod 图标 JPEG 扫描缺少有效帧头')
        count = payload[0]
        if not 1 <= count <= 4 or len(payload) != 4 + 2 * count:
            raise ValueError('Mod 图标 JPEG 扫描组件无效')
        selectors = {payload[1 + 2 * index] for index in range(count)}
        if len(selectors) != count or not selectors.issubset(components):
            raise ValueError('Mod 图标 JPEG 扫描组件无效')
        for index in range(count):
            table = payload[2 + 2 * index]
            if table >> 4 > 3 or table & 0x0f > 3:
                raise ValueError('Mod 图标 JPEG 扫描表选择无效')
        has_entropy = False
        while offset < len(data):
            if data[offset] != 0xff:
                has_entropy = True
                offset += 1
                continue
            marker_offset = offset
            while offset < len(data) and data[offset] == 0xff:
                offset += 1
            if offset == len(data):
                raise ValueError('Mod 图标 JPEG 扫描数据截断')
            marker = data[offset]
            offset += 1
            if marker == 0x00:
                has_entropy = True
                continue
            if 0xd0 <= marker <= 0xd7:
                continue
            if not has_entropy:
                raise ValueError('Mod 图标 JPEG 扫描数据为空')
            # Reparse the marker itself, including SOI/EOI, outside entropy.
            offset = marker_offset
            seen_scan = True
            break
        else:
            raise ValueError('Mod 图标 JPEG 内容不完整')
    raise ValueError('Mod 图标 JPEG 内容不完整')


def validate(data):
    """Keep the original bytes after format-specific structural validation."""
    if not isinstance(data, bytes):
        raise ValueError('Mod 图标须为静态 PNG 或 JPG/JPEG')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return _validate_png(data)
    if data.startswith(b'\xff\xd8'):
        return _validate_jpeg(data)
    raise ValueError('Mod 图标须为静态 PNG 或 JPG/JPEG')


def member_name(data):
    """Use a canonical ZIP member name determined by the validated content."""
    validate(data)
    return 'icon.png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'icon.jpg'


def validate_member(name, data):
    """Accept exact archive names only, and require their format to agree."""
    if name not in MEMBER_NAMES:
        raise ValueError('Mod 图标成员名无效')
    actual_name = member_name(data)
    if (name == 'icon.png') != (actual_name == 'icon.png'):
        raise ValueError('Mod 图标扩展名与内容格式不符')
    return data


def read(path):
    """Empty setting preserves the old package format; snapshot once per export."""
    if not path or not str(path).strip():
        return None
    with Path(path).open('rb') as stream:
        data = stream.read()
    return validate(data)
