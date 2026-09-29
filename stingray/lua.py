import os
import re
import struct


# Stingray Lua 资源使用一个非常简单的 8 字节头：
#   uint32：正文长度
#   uint32：资源格式版本（目前游戏使用 2）
LUA_RESOURCE_HEADER = struct.Struct("<II")
LUA_RESOURCE_VERSION = 2
LUAJIT_MAGIC = b"\x1bLJ"

# murmur64("boot")，用于专用的 Boot Patch 重建功能。
BOOT_LUA_FILE_ID = 0xF476DF93691895FA


class LuaResourceError(ValueError):
    """Lua 资源头或正文不合法。"""


def unpack_lua_resource(data):
    """拆分 Stingray Lua 资源，返回 (正文, 版本)。"""
    data = bytes(data)
    if len(data) < LUA_RESOURCE_HEADER.size:
        raise LuaResourceError("Lua 资源小于 8 字节，缺少 Stingray 资源头")

    declared_size, version = LUA_RESOURCE_HEADER.unpack_from(data, 0)
    payload = data[LUA_RESOURCE_HEADER.size:]
    if declared_size != len(payload):
        raise LuaResourceError(
            f"Lua 资源长度不匹配：资源头={declared_size}，实际={len(payload)}"
        )
    return payload, version


def try_unpack_lua_resource(data):
    """若 data 是完整 Stingray Lua 资源则拆分，否则返回 None。"""
    try:
        return unpack_lua_resource(data)
    except (LuaResourceError, struct.error):
        return None


def pack_lua_resource(payload, version=LUA_RESOURCE_VERSION):
    """给 Lua 明文或 LuaJIT 字节码自动补上 Stingray 资源头。"""
    payload = bytes(payload)
    return LUA_RESOURCE_HEADER.pack(len(payload), int(version)) + payload


def normalize_lua_input(data, default_version=LUA_RESOURCE_VERSION):
    """兼容完整 Raw Dump、明文 Lua 和裸 LuaJIT 字节码。

    返回 (正文, 版本, 输入是否原本带资源头)。
    """
    data = bytes(data)
    unpacked = try_unpack_lua_resource(data)
    if unpacked is not None:
        return unpacked[0], unpacked[1], True

    # VS Code 保存的 UTF-8 with BOM 文件不应把 BOM 送进 Lua 编译器。
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data, default_version, False


def is_luajit_bytecode(payload):
    return bytes(payload).startswith(LUAJIT_MAGIC)


def decode_lua_source(payload):
    """严格按 UTF-8 解码明文 Lua；字节码会直接报错。"""
    payload = bytes(payload)
    if is_luajit_bytecode(payload):
        raise LuaResourceError("该资源是 LuaJIT 字节码，不是可直接编辑的明文 Lua")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LuaResourceError(f"Lua 明文不是有效 UTF-8：{exc}") from exc


def _lua_quoted_text(value):
    """生成安全的 Lua 双引号字符串。"""
    return (
        '"'
        + str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        + '"'
    )


def _lua_bytecode_table(payload, bytes_per_line=48):
    """把二进制转换成 Lua 5.1/LuaJIT 可读取的十进制转义字符串表。"""
    payload = bytes(payload)
    rows = []
    for offset in range(0, len(payload), bytes_per_line):
        part = payload[offset:offset + bytes_per_line]
        rows.append('  "' + "".join(f"\\{value:03d}" for value in part) + '"')
    if not rows:
        rows.append('  ""')
    return "{\n" + ",\n".join(rows) + "\n}"


def _lua_long_string(text):
    """选择不会与正文冲突的 Lua 长字符串分隔符。"""
    text = str(text)
    for level in range(0, 32):
        equals = "=" * level
        close_token = "]" + equals + "]"
        if close_token not in text:
            return "[" + equals + "[" + text + "]" + equals + "]"
    raise LuaResourceError("Lua 源码包含过多长字符串结束符，无法安全嵌入 Boot 包装器")


def make_luajit_editable_wrapper(payload, chunk_name="embedded_lua"):
    """为无法反编译的 LuaJIT 字节码生成可在 VS Code 打开的明文包装器。

    保留原字节码并可在其前后继续编写 Lua。
    """
    bytecode_table = _lua_bytecode_table(payload)
    chunk_literal = _lua_quoted_text("@" + str(chunk_name))
    return (
        "--[[\n"
        "  此文件由 AQSDK 自动生成。\n"
        "  原资源是 LuaJIT 字节码，无法无损恢复作者的变量名、注释和源码结构。\n"
        "  下方包装器会原样执行字节码；你可以在执行前后增加自己的明文 Lua。\n"
        "  若要制作 boot 扩展，请优先使用插件的“重建 Boot Patch”，不要手工复制原版 boot。\n"
        "]]\n\n"
        "local __hd2_loader = rawget(_G, \"loadstring\") or rawget(_G, \"load\")\n"
        "assert(type(__hd2_loader) == \"function\", \"Lua loader unavailable\")\n\n"
        "local __hd2_unpack = rawget(_G, \"unpack\") or table.unpack\n"
        "local function __hd2_pack(...)\n"
        "  return { n = select(\"#\", ...), ... }\n"
        "end\n\n"
        "local __hd2_bytecode = table.concat(" + bytecode_table + ")\n"
        "local __hd2_chunk, __hd2_load_error = __hd2_loader(__hd2_bytecode, "
        + chunk_literal
        + ")\n"
        "assert(__hd2_chunk, __hd2_load_error)\n\n"
        "-- 在这里写执行原资源之前的代码。\n\n"
        "local __hd2_results = __hd2_pack(__hd2_chunk(...))\n\n"
        "-- 在这里写执行原资源之后的代码。\n\n"
        "return __hd2_unpack(__hd2_results, 1, __hd2_results.n)\n"
    ).encode("utf-8")


def make_boot_wrapper(original_payload, custom_payload, custom_name="custom_boot.lua"):
    """生成“原版 boot + 用户脚本”的明文 Lua 资源正文。"""
    original_table = _lua_bytecode_table(original_payload)
    loader_name = _lua_quoted_text("@" + str(custom_name))

    if is_luajit_bytecode(custom_payload):
        custom_expr = "table.concat(" + _lua_bytecode_table(custom_payload) + ")"
    else:
        custom_text = decode_lua_source(custom_payload)
        custom_expr = _lua_long_string(custom_text)

    source = (
        "--[[\n"
        "  HD2SDK AQ Modified 自动重建的 boot.lua。\n"
        "  插件先执行当前游戏的原版 boot，再执行用户提供的扩展脚本。\n"
        "  游戏更新后请重新执行“重建 Boot Patch”，不要长期固定旧版 boot。\n"
        "]]\n\n"
        "local __hd2_loader = rawget(_G, \"loadstring\") or rawget(_G, \"load\")\n"
        "assert(type(__hd2_loader) == \"function\", \"Lua loader unavailable\")\n\n"
        "local __hd2_unpack = rawget(_G, \"unpack\") or table.unpack\n"
        "local function __hd2_pack(...)\n"
        "  return { n = select(\"#\", ...), ... }\n"
        "end\n\n"
        "-- 原版 boot 以字节形式保留，避免 LuaJIT 字节码经过文本编码后损坏。\n"
        "local __hd2_original_boot = table.concat(" + original_table + ")\n"
        "local __hd2_boot_chunk, __hd2_boot_load_error = "
        "__hd2_loader(__hd2_original_boot, \"@boot\")\n"
        "assert(__hd2_boot_chunk, __hd2_boot_load_error)\n"
        "local __hd2_boot_results = __hd2_pack(pcall(__hd2_boot_chunk, ...))\n"
        "if not __hd2_boot_results[1] then error(__hd2_boot_results[2]) end\n\n"
        "-- 用户脚本作为独立 chunk 执行，因此脚本内可以安全使用顶层 return。\n"
        "local __hd2_custom_source = " + custom_expr + "\n"
        "local __hd2_custom_chunk, __hd2_custom_load_error = "
        "__hd2_loader(__hd2_custom_source, " + loader_name + ")\n"
        "if not __hd2_custom_chunk then\n"
        "  local __p = rawget(_G, \"print\")\n"
        "  if __p then __p(\"[HD2SDK Lua] 自定义脚本编译失败: \" .. "
        "tostring(__hd2_custom_load_error)) end\n"
        "else\n"
        "  local __hd2_custom_ok, __hd2_custom_error = pcall(__hd2_custom_chunk, ...)\n"
        "  if not __hd2_custom_ok then\n"
        "    local __p = rawget(_G, \"print\")\n"
        "    if __p then __p(\"[HD2SDK Lua] 自定义脚本运行失败: \" .. "
        "tostring(__hd2_custom_error)) end\n"
        "  end\n"
        "end\n\n"
        "return __hd2_unpack(__hd2_boot_results, 2, __hd2_boot_results.n)\n"
    )
    return source.encode("utf-8")


def extract_luajit_strings(payload, minimum_length=4):
    """提取字节码中的可打印字符串，便于在 VS Code 中检索资源路径和 API 名。"""
    payload = bytes(payload)
    pattern = re.compile(rb"[\x20-\x7e]{%d,}" % int(minimum_length))
    lines = [
        "LuaJIT 字节码可打印字符串",
        "说明：这不是反编译源码，只用于检索模块名、资源路径、字段名和报错文本。",
        "",
    ]
    for match in pattern.finditer(payload):
        lines.append(f"0x{match.start():08X}  {match.group().decode('ascii', errors='replace')}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def make_editable_export(toc_data, chunk_name):
    """把完整 Lua 资源转换成适合编辑的文件集合。

    返回 (主 .lua 内容, 裸字节码或 None, 字符串报告或 None, 版本)。
    """
    payload, version = unpack_lua_resource(toc_data)
    if is_luajit_bytecode(payload):
        return (
            make_luajit_editable_wrapper(payload, chunk_name),
            payload,
            extract_luajit_strings(payload),
            version,
        )
    return payload, None, None, version


def convert_lua_export_folder(source_dir, output_dir):
    """批量转换插件旧版 Raw Dump；原始文件始终保留不动。"""
    os.makedirs(output_dir, exist_ok=True)
    converted = []
    for name in sorted(os.listdir(source_dir)):
        source_path = os.path.join(source_dir, name)
        if not os.path.isfile(source_path) or not name.lower().endswith(".lua"):
            continue
        with open(source_path, "rb") as source_file:
            raw_data = source_file.read()
        if try_unpack_lua_resource(raw_data) is None:
            continue

        file_stem = os.path.splitext(name)[0]
        friendly_stem = file_stem + ("_boot" if file_stem == str(BOOT_LUA_FILE_ID) else "")
        editable, bytecode, strings_report, version = make_editable_export(
            raw_data, friendly_stem
        )
        editable_path = os.path.join(output_dir, friendly_stem + ".lua")
        with open(editable_path, "wb") as editable_file:
            editable_file.write(editable)

        if bytecode is not None:
            with open(os.path.join(output_dir, friendly_stem + ".luajit"), "wb") as bytecode_file:
                bytecode_file.write(bytecode)
            with open(os.path.join(output_dir, friendly_stem + ".strings.txt"), "wb") as strings_file:
                strings_file.write(strings_report)

        converted.append((name, editable_path, version, bytecode is not None))
    return converted


class StingrayLua:
    """插件内部使用的 Lua 资源对象。"""

    def __init__(self):
        self.version = LUA_RESOURCE_VERSION
        self.luaData = b""

    def FromResource(self, toc_data):
        self.luaData, self.version = unpack_lua_resource(toc_data)
        return self

    def ToResource(self):
        return pack_lua_resource(self.luaData, self.version)
