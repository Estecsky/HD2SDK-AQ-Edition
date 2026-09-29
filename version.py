"""HD2SDK AQ Edition 的唯一版本源。"""

VERSION = (2, 5, 0)
PRERELEASE = ()
PACKAGE_NAME = "HD2SDK-AQ-Edition"


def _format_version():
    base = ".".join(str(part) for part in VERSION)
    return f"{base}-{PRERELEASE[0]}.{PRERELEASE[1]}" if PRERELEASE else base


VERSION_TEXT = _format_version()
