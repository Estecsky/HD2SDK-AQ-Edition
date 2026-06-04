bl_info = {
    "name": "Helldivers 2 Archives",
    "blender": (4, 0, 0),
    "category": "Import-Export",
    "author": "kboykboy2, AQ_Echoo",
    "warning": "此为修改版",
    "version": (2, 4, 0),
    "doc_url": "https://github.com/Estecsky/io_scene_helldivers2_AQ"
}

#region Imports

# System
import ctypes, os, tempfile, subprocess, time, webbrowser, re
import random as r
from copy import deepcopy
import copy
from math import ceil , sqrt # type: ignore
from pathlib import Path

# Blender
import bpy, bmesh, mathutils
from bpy_extras.io_utils import ImportHelper, ExportHelper
from bpy.props import StringProperty, BoolProperty, IntProperty, EnumProperty, PointerProperty
from bpy.types import Panel, Operator, PropertyGroup, Scene, Menu

from .stingray.animation import StingrayAnimation, AnimationException
from .stingray.raw_dump import StingrayRawDump
from .stingray.material import LoadShaderVariables, LoadShaderVariables_CN, StingrayMaterial,Global_ShaderVariables,Global_ShaderVariables_CN, AddMaterialToBlend_EMPTY
from .stingray.composite_unit import StingrayCompositeUnit
from .stingray.bones import LoadBoneHashes, StingrayBones
from .stingray.unit import StingrayMeshFile , CreateModel, GetObjectsMeshData
from .stingray.texture import StingrayTexture
from .stingray.particle import StingrayParticles
from .stingray.state_machine import StingrayStateMachine
from .utils.constants import *


# Local
# NOTE: Not bothering to do importlib reloading shit because these modules are unlikely to be modified frequently enough to warrant testing without Blender restarts
from .utils.math import MakeTenBitUnsigned, TenBitUnsigned
from .utils.memoryStream import MemoryStream
from .utils.logger import PrettyPrint
from .utils.slim import is_slim_version, load_package, get_package_toc, slim_init,reconstruct_package_from_bundles ,get_full_package_list
from .AQ_Prefs_HD2 import AQ_PublicClass, AQ_StaticMeshError

import zipfile
import configparser
import struct
import concurrent.futures
from . import addon_updater_ops
from . import addonPreferences
from . import get_update_archivelistCN
#endregion

#region Global Variables

AddonPath = os.path.dirname(__file__)
BlenderAddonsPath = os.path.dirname(AddonPath)

Global_texconvpath         = f"{AddonPath}/deps/texconv.exe"
Global_palettepath         = f"{AddonPath}/deps/NormalPalette.dat"
Global_materialpath        = f"{AddonPath}/materials"
Global_typehashpath        = f"{AddonPath}/hashlists/typehash.txt"
Global_friendlynamespath   = f"{AddonPath}/hashlists/friendlynames.txt"
Global_variablespath       = f"{AddonPath}/hashlists/shadervariables.txt"
Global_bonehashpath      = f"{AddonPath}/hashlists/bonehash.txt"

Global_variablesCNpath     = f"{AddonPath}/hashlists/shadervariables_combine_CN.txt"
Global_updatearchivelistCNpath = f"{AddonPath}/hashlists/update_archive_listCN.txt"

Global_configpath          = f"{BlenderAddonsPath}/io_scene_helldivers2_AQ.ini"
Global_defaultgamepath     = r"C:\Program Files (x86)\Steam\steamapps\common\Helldivers 2\data\ "
Global_defaultgamepath     = Global_defaultgamepath[:len(Global_defaultgamepath) - 1]
Global_gamepath            = ""
Global_gamepathIsValid = False

Global_BoneNames = {}

Global_AnimationMapping = {}

Global_SectionHeader = "---Helldivers 2 AQ魔改---"


Global_updatearchivelistCN_list = []
Global_PatchBasePath = ""
Global_Foldouts = []
#endregion
#region Common Hashes & Lookups

TextureTypeLookup = {
    "original": ("pbr: ", 
                 "",
                 "", 
                 "alpha mask: ",
                 "",
                 "normal: ",
                 "",
                 "sss color: ",
                 "", 
                 "color: ",
                 "", 
                 "", 
                 ""),
    "basic": ("pbr: ", "color: ", "normal: "),
    "basic+": (
        "PBR: ",
        "Base Color: ",
        "Normal: "
    ),
    "basic+Fixed": (
        "PBR: ",
        "Base Color: ",
        "Normal: "
    ),
    "bloom":  ("normal/ao/cavity: ",
                 "emission: ",
                 "color/metallic: "
    ),
    "glass": (
        "Glass stain: ",""
    ),
    "advanced_default": (
    "",
    "",
    "Normal/AO/Roughness: ",
    "Metallic: ",
    "",
    "Color/Emission Mask: ",
    "",
    "",
    "",
    "",
    ""
    ),
    "advanced_yellow": (
    "",
    "",
    "Normal/AO/Roughness: ",
    "Metallic: ",
    "",
    "Color/Emission Mask: ",
    "",
    "",
    "",
    "",
    ""
    ),
    "advanced_orange": (
    "",
    "",
    "Normal/AO/Roughness: ",
    "Metallic: ",
    "",
    "Color/Emission Mask: ",
    "",
    "",
    "",
    "",
    ""
    ),
    "advanced_red": (
    "",
    "",
    "Normal/AO/Roughness: ",
    "Metallic: ",
    "",
    "Color/Emission Mask: ",
    "",
    "",
    "",
    "",
    ""
    ),
    "advanced_pink": (
    "",
    "",
    "Normal/AO/Roughness: ",
    "Metallic: ",
    "",
    "Color/Emission Mask: ",
    "",
    "",
    "",
    "",
    ""
    ),
    "advanced_purple": (
    "",
    "",
    "Normal/AO/Roughness: ",
    "Metallic: ",
    "",
    "Color/Emission Mask: ",
    "",
    "",
    "",
    "",
    ""
    ),
    "advanced_dark-blue": (
    "",
    "",
    "Normal/AO/Roughness: ",
    "Metallic: ",
    "",
    "Color/Emission Mask: ",
    "",
    "",
    "",
    "",
    ""
    ),
    "advanced_blue": (
    "",
    "",
    "Normal/AO/Roughness: ",
    "Metallic: ",
    "",
    "Color/Emission Mask: ",
    "",
    "",
    "",
    "",
    ""
    ),
    "advanced_light-blue": (
    "",
    "",
    "Normal/AO/Roughness: ",
    "Metallic: ",
    "",
    "Color/Emission Mask: ",
    "",
    "",
    "",
    "",
    ""
    ),
    "advanced_blue-green": (
    "",
    "",
    "Normal/AO/Roughness: ",
    "Metallic: ",
    "",
    "Color/Emission Mask: ",
    "",
    "",
    "",
    "",
    ""
    ),
    "advanced_green": (
    "",
    "",
    "Normal/AO/Roughness: ",
    "Metallic: ",
    "",
    "Color/Emission Mask: ",
    "",
    "",
    "",
    "",
    ""
    ),
    "flowing": (
        "",
        "Normal_1: ",
        "Normal_2: ",
        "Mask: ",
        ""
    ),
    "alphaclip": (
        "Normal/AO/Roughness: ",
        "Alpha Mask: ",
        "Base Color/Metallic: "
    ),
    "alphaclip+": (
        "Normal/AO/Roughness: ",
        "Emission: ",
        "Base Color/Metallic: ",
        "Alpha Mask: ",
    ),

    
    
}

Global_Materials = (
        ("bloom", "Bloom", "A bloom material with two color, normal map which does not render in the UI"),
        ("original", "Original", "The original template used for all mods uploaded to Nexus prior to the addon's public release, which is bloated with additional unnecessary textures. Sourced from a terminid."),
        ("advanced_default", "Advanced 默认无光", "A more comlpicated material, that is color, normal, emission and PBR capable which renders in the UI. Sourced from the Illuminate Overseer."),   
        ("advanced_orange", "Advanced 橙光", "A more comlpicated material, that is color, normal, emission and PBR capable which renders in the UI. Sourced from the Illuminate Overseer."),
        ("advanced_yellow", "Advanced 黄光", "A more comlpicated material, that is color, normal, emission and PBR capable which renders in the UI. Sourced from the Illuminate Overseer."),
        ("advanced_red", "Advanced 红光", "A more comlpicated material, that is color, normal, emission and PBR capable which renders in the UI. Sourced from the Illuminate Overseer."),
        ("advanced_pink", "Advanced 粉光", "A more comlpicated material, that is color, normal, emission and PBR capable which renders in the UI. Sourced from the Illuminate Overseer."),
        ("advanced_purple", "Advanced 紫光", "A more comlpicated material, that is color, normal, emission and PBR capable which renders in the UI. Sourced from the Illuminate Overseer."),
        ("advanced_dark-blue", "Advanced 深蓝光", "A more comlpicated material, that is color, normal, emission and PBR capable which renders in the UI. Sourced from the Illuminate Overseer."),
        ("advanced_blue", "Advanced 蓝光", "A more comlpicated material, that is color, normal, emission and PBR capable which renders in the UI. Sourced from the Illuminate Overseer."),
        ("advanced_light-blue", "Advanced 浅蓝光", "A more comlpicated material, that is color, normal, emission and PBR capable which renders in the UI. Sourced from the Illuminate Overseer."),
        ("advanced_blue-green", "Advanced 蓝绿光", "A more comlpicated material, that is color, normal, emission and PBR capable which renders in the UI. Sourced from the Illuminate Overseer."),
        ("advanced_green", "Advanced 绿光", "A more comlpicated material, that is color, normal, emission and PBR capable which renders in the UI. Sourced from the Illuminate Overseer."),

        ("flowing","流光","光能电塔的流光材质，材质参数中切换uv空间改成负的就是从上往下，正的是从下往上"),
        ("glass", "透明玻璃", "透明玻璃，不知道能干嘛，自己猜()"),
        ("basic+Fixed", "Basic+", "A basic material with a color, normal, and PBR map which renders in the UI, Sourced from a SEAF NPC"),
        ("basic", "Basic", "A basic material with a color, normal, and PBR map. Sourced from a trash bag prop."),
        ("alphaclip", "Alpha Clip", "金属度在颜色贴图的alpha通道，A material that supports an alpha mask which does not render in the UI. Sourced from a skeleton pile"),
        ("alphaclip+", "Alpha Clip+", "一个支持alpha遮罩的材质，金属度在颜色贴图的alpha通道，带有额外的发光功能，不会在UI中显示，来自机器人生物处理器"),

    )



#endregion

#region Functions: Miscellaneous


def EntriesFromStrings(file_id_string, type_id_string):
    FileIDs = file_id_string.split(',')
    TypeIDs = type_id_string.split(',')
    Entries = []
    for n in range(len(FileIDs)):
        if FileIDs[n] != "":
            Entries.append(Global_TocManager.GetEntry(int(FileIDs[n]), int(TypeIDs[n])))
    return Entries

def EntriesFromString(file_id_string, TypeID):
    FileIDs = file_id_string.split(',')
    Entries = []
    for n in range(len(FileIDs)):
        if FileIDs[n] != "":
            Entries.append(Global_TocManager.GetEntry(int(FileIDs[n]), int(TypeID)))
    return Entries

def IDsFromString(file_id_string):
    FileIDs = file_id_string.split(',')
    Entries = []
    for n in range(len(FileIDs)):
        if FileIDs[n] != "":
            Entries.append(int(FileIDs[n]))
    return Entries

def GetDisplayData():
    # Set display archive TODO: Global_TocManager.LastSelected Draw Index could be wrong if we switch to patch only mode, that should be fixed
    DisplayTocEntries = []
    DisplayTocTypes   = []
    DisplayTocPatchPath = ""
    DisplayTocArchivePath = ""
    DisplayTocPatchPath_Add = ""
    DisplayArchive = Global_TocManager.ActiveArchive
    if bpy.context.scene.Hd2ToolPanelSettings.PatchOnly:
        if Global_TocManager.ActivePatch != None:
            DisplayTocEntries = [[Entry, True] for Entry in Global_TocManager.ActivePatch.TocEntries]
            DisplayTocTypes   = Global_TocManager.ActivePatch.TocTypes
            DisplayTocPatchPath = Global_TocManager.ActivePatch.Path
            DisplayTocPatchPath_Add = Global_TocManager.ActivePatch.Path
    elif Global_TocManager.ActiveArchive != None:
        DisplayTocEntries = [[Entry, False] for Entry in Global_TocManager.ActiveArchive.TocEntries]
        DisplayTocTypes   = [Type for Type in Global_TocManager.ActiveArchive.TocTypes]
        DisplayTocArchivePath = Global_TocManager.ActiveArchive.Path
        AddedTypes   = [Type.TypeID for Type in DisplayTocTypes]
        AddedEntries = [Entry[0].FileID for Entry in DisplayTocEntries]
        if Global_TocManager.ActivePatch != None:
            for Type in Global_TocManager.ActivePatch.TocTypes:
                if Type.TypeID not in AddedTypes:
                    AddedTypes.append(Type.TypeID)
                    DisplayTocTypes.append(Type)
            for Entry in Global_TocManager.ActivePatch.TocEntries:
                if Entry.FileID not in AddedEntries:
                    AddedEntries.append(Entry.FileID)
                    DisplayTocEntries.append([Entry, True])
        try:
            DisplayTocPatchPath_Add = Global_TocManager.ActivePatch.Path
            DisplayTocPatchPath = Global_TocManager.ActivePatch.Path
        except:
            DisplayTocPatchPath = ""
            DisplayTocPatchPath_Add = ""
    elif Global_TocManager.ActivePatch != None:
        # DisplayTocEntries_Add = [[Entry, True] for Entry in Global_TocManager.ActivePatch.TocEntries]
        # DisplayTocTypes_Add   = Global_TocManager.ActivePatch.TocTypes
        DisplayTocPatchPath_Add = Global_TocManager.ActivePatch.Path
        
    return [DisplayTocEntries, DisplayTocTypes, DisplayTocArchivePath, DisplayTocPatchPath,
            DisplayTocPatchPath_Add]

#endregion

#region Functions: Blender

# 检查材质名称是否以'.001'结尾
def CheckValidMaterial(obj, slot_index):
    material = obj.material_slots[slot_index].material
    if material.name.find(".0") != -1:
        original_material_name = material.name[:-4]
        
        target_material = bpy.data.materials.get(original_material_name)
        
        if target_material:
            obj.material_slots[slot_index].material = target_material
            
            material_users = material.users
            if material_users <= 0:
                bpy.data.materials.remove(material)
        else:
            PrettyPrint("尝试替换为正确的材质名: " + material.name + "该材质不存在，正在新建")
            AddMaterialToBlend_EMPTY(original_material_name)
            new_material = bpy.data.materials.get(original_material_name)
            obj.material_slots[slot_index].material = new_material
            
            material_users = material.users
            if material_users <= 0:
                bpy.data.materials.remove(material)
                
        PrettyPrint("材质清理完成")



            
#endregion

#region Functions: Stingray Hashing

def GetTypeNameFromID(ID):
    for hash_info in Global_TypeHashes:
        if int(ID) == hash_info[0]:
            return hash_info[1]
    return "unknown"

def GetIDFromTypeName(Name):
    for hash_info in Global_TypeHashes:
        if hash_info[1] == Name:
            return int(hash_info[0])
    return None

def GetFriendlyNameFromID(ID):
    try:
        hash_info_name = Global_NameHashes[int(ID)]
        if hash_info_name != "":
            return str(hash_info_name)
    except KeyError:
        pass
    return str(ID)
    

def GetArchiveNameFromID(EntryID):
    global Global_updatearchivelistCN_list
    for hash in Global_updatearchivelistCN_list:
        if hash["ArchiveID"].find(EntryID) != -1:
            ArchiveNameCN = str(hash["Classify"]).replace("'", "").replace("\n", "") + " - " + hash["Description"]
            return ArchiveNameCN
    return ""

def HasFriendlyName(ID):
    return (int(ID) in Global_NameHashes)

def AddFriendlyName(ID, Name):
    Global_TocManager.SavedFriendlyNames = []
    Global_TocManager.SavedFriendlyNameIDs = []

    Global_NameHashes[int(ID)] = str(Name)
    SaveFriendlyNames()


def SaveFriendlyNames():
    with open(Global_friendlynamespath, 'w') as f:
        for hash_key in Global_NameHashes.keys():
            hash_value = Global_NameHashes[hash_key]
            if hash_value != "":
                string = str(hash_key) + " " + str(hash_value)
                f.writelines(string+"\n")


def bytes_to_long(bytes):
    assert len(bytes) == 8
    return sum((b << (k * 8) for k, b in enumerate(bytes)))

def murmur64_hash(data, seed: int = 0):

    m = 0xc6a4a7935bd1e995
    r = 47

    MASK = 2 ** 64 - 1

    data_as_bytes = bytearray(data)

    h = seed ^ ((m * len(data_as_bytes)) & MASK)

    off = int(len(data_as_bytes)/8)*8
    for ll in range(0, off, 8):
        k = bytes_to_long(data_as_bytes[ll:ll + 8])
        k = (k * m) & MASK
        k = k ^ ((k >> r) & MASK)
        k = (k * m) & MASK
        h = (h ^ k)
        h = (h * m) & MASK

    l = len(data_as_bytes) & 7

    if l >= 7:
        h = (h ^ (data_as_bytes[off+6] << 48))

    if l >= 6:
        h = (h ^ (data_as_bytes[off+5] << 40))

    if l >= 5:
        h = (h ^ (data_as_bytes[off+4] << 32))

    if l >= 4:
        h = (h ^ (data_as_bytes[off+3] << 24))

    if l >= 3:
        h = (h ^ (data_as_bytes[off+2] << 16))

    if l >= 2:
        h = (h ^ (data_as_bytes[off+1] << 8))

    if l >= 1:
        h = (h ^ data_as_bytes[off])
        h = (h * m) & MASK

    h = h ^ ((h >> r) & MASK)
    h = (h * m) & MASK
    h = h ^ ((h >> r) & MASK)

    return h
    
def murmur32_hash(data, seed: int = 0):
    return murmur64_hash(data, seed) >> 32

#endregion

#region Functions: Initialization


Global_TypeHashes = []
def LoadTypeHashes():
    with open(Global_typehashpath, 'r') as f:
        for line in f.readlines():
            parts = line.split(" ")
            Global_TypeHashes.append([int(parts[0], 16), parts[1].replace("\n", "")])

Global_NameHashes = {}
def LoadNameHashes():
    Loaded = []
    with open(Global_friendlynamespath, 'r') as f:
        for line in f.readlines():
            parts = line.split(" ")
            if int(parts[0]) not in Loaded:
                Global_NameHashes[int(parts[0])] = " ".join(parts[1:]).replace("\n", "")
                Loaded.append(int(parts[0]))


# 载入Archive收集表条目
def LoadUpdateArchiveList_CN():
    global Global_updatearchivelistCN_list
    Global_updatearchivelistCN_list = []
    with open(Global_updatearchivelistCNpath, 'r', encoding='utf-8') as f:
        for line in f.readlines():
            if "本地更新时间" in line or line.find("None#None#None") != -1 or "以下是2025.3.18更新后" in line:
                continue
            Global_updatearchivelistCN_list.append({"ArchiveID": str(line.split("#")[0]),
                                                    "Classify": line.split("#")[1].split(","),
                                                    "Description": line.split("#")[2] })
            
def GetEntryParentMaterialID(entry):
    if entry.TypeID == MaterialID:
        f = MemoryStream(entry.TocData)
        for i in range(6):
            f.uint32(0)
        parentID = f.uint64(0)
        return parentID
    else:
        raise Exception(f"Entry: {entry.FileID} is not a material")
#endregion

#region Configuration

def InitializeConfig():
    global Global_gamepath, Global_configpath, Global_gamepathIsValid
    if os.path.exists(Global_configpath):
        config = configparser.ConfigParser()
        config.read(Global_configpath, encoding='utf-8')
        try:
            Global_gamepath = config['DEFAULT']['filepath']
        except:
            UpdateConfig()
            
        if os.path.exists(Global_gamepath):
            PrettyPrint(f"Loaded Data Folder: {Global_gamepath}")
            slim_init(Global_gamepath)
            Global_gamepathIsValid = True
        else:
            PrettyPrint(f"Game path: {Global_gamepath} is not a valid directory", 'ERROR')
            Global_gamepathIsValid = False

    else:
        UpdateConfig()

def get_helldivers2_path():
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
        steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
        winreg.CloseKey(key)
    except Exception:
        return None

    steam_path = os.path.normpath(steam_path)
    library_vdf = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")

    if not os.path.exists(library_vdf):
        return None

    libraries = [os.path.join(steam_path, "steamapps")]
    with open(library_vdf, "r", encoding="utf-8") as f:
        content = f.read()

    for match in re.finditer(r'"\d+"\s+"([^"]+)"', content):
        lib_path = match.group(1).replace("\\\\", "\\")
        libraries.append(os.path.join(lib_path, "steamapps"))

    for lib in libraries:
        manifest = os.path.join(lib, "appmanifest_553850.acf")
        if os.path.exists(manifest):
            with open(manifest, "r", encoding="utf-8") as f:
                data = f.read()
            m = re.search(r'"installdir"\s+"([^"]+)"', data)
            if m:
                game_folder = m.group(1)
                return os.path.join(lib, "common", game_folder, "data")

    return None
    
def UpdateConfig():
    global Global_gamepath, Global_defaultgamepath, Global_gamepathIsValid
    if Global_gamepath == "":
        if get_helldivers2_path():
            Global_gamepath = get_helldivers2_path()
        else:
            Global_gamepath = Global_defaultgamepath
    if Global_gamepathIsValid: 
        slim_init(Global_gamepath)
    config = configparser.ConfigParser()
    config['DEFAULT'] = {'filepath' : Global_gamepath}
    with open(Global_configpath, 'w') as configfile:
        config.write(configfile)
    
#endregion

#region Classes and Functions: Stingray Archives

class TocEntry:

    def __init__(self):
        self.FileID = self.TypeID = self.TocDataOffset = self.Unknown1 = self.GpuResourceOffset = self.Unknown2 = self.TocDataSize = self.GpuResourceSize = self.EntryIndex = self.StreamSize = self.StreamOffset = 0
        self.Unknown3 = 16
        self.Unknown4 = 64

        self.TocData =  self.TocData_OLD = b""
        self.GpuData =  self.GpuData_OLD = b""
        self.StreamData =  self.StreamData_OLD = b""

        # Custom Dev stuff
        self.LoadedData = None
        self.IsLoaded   = False
        self.IsModified = False
        self.IsCreated  = False # custom created, can be removed from archive
        self.IsSelected = False
        self.MaterialTemplate = None # for determining tuple to use for labeling textures in the material editor
        self.DEV_DrawIndex = -1

    # -- Serialize TocEntry -- #
    def Serialize(self, TocFile: MemoryStream, Index=0):
        self.FileID             = TocFile.uint64(self.FileID)
        self.TypeID             = TocFile.uint64(self.TypeID)
        self.TocDataOffset      = TocFile.uint64(self.TocDataOffset)
        self.StreamOffset       = TocFile.uint64(self.StreamOffset)
        self.GpuResourceOffset  = TocFile.uint64(self.GpuResourceOffset)
        self.Unknown1           = TocFile.uint64(self.Unknown1)
        self.Unknown2           = TocFile.uint64(self.Unknown2)
        self.TocDataSize        = TocFile.uint32(len(self.TocData))
        self.StreamSize         = TocFile.uint32(len(self.StreamData))
        self.GpuResourceSize    = TocFile.uint32(len(self.GpuData))
        self.Unknown3           = TocFile.uint32(self.Unknown3)
        self.Unknown4           = TocFile.uint32(self.Unknown4)
        self.EntryIndex         = TocFile.uint32(Index)
        return self

    # -- Write TocEntry Data -- #
    def SerializeData(self, TocFile: MemoryStream, GpuFile, StreamFile):
        if TocFile.IsReading():
            TocFile.seek(self.TocDataOffset)
            self.TocData = bytearray(self.TocDataSize)
        elif TocFile.IsWriting():
            self.TocDataOffset = TocFile.tell()
        self.TocData = TocFile.bytes(self.TocData)

        if GpuFile.IsWriting(): self.GpuResourceOffset = ceil(float(GpuFile.tell())/64)*64
        if self.GpuResourceSize > 0:
            GpuFile.seek(self.GpuResourceOffset)
            if GpuFile.IsReading(): self.GpuData = bytearray(self.GpuResourceSize)
            self.GpuData = GpuFile.bytes(self.GpuData)

        if StreamFile.IsWriting(): self.StreamOffset = ceil(float(StreamFile.tell())/64)*64
        if self.StreamSize > 0:
            StreamFile.seek(self.StreamOffset)
            if StreamFile.IsReading(): self.StreamData = bytearray(self.StreamSize)
            self.StreamData = StreamFile.bytes(self.StreamData)
        if GpuFile.IsReading():
            self.TocData_OLD    = bytearray(self.TocData)
            self.GpuData_OLD    = bytearray(self.GpuData)
            self.StreamData_OLD = bytearray(self.StreamData)

    # -- Get Data -- #
    def GetData(self):
        return [self.TocData, self.GpuData, self.StreamData]
    # -- Set Data -- #
    def SetData(self, TocData, GpuData, StreamData, IsModified=True):
        self.TocData = TocData
        self.GpuData = GpuData
        self.StreamData = StreamData
        self.TocDataSize     = len(self.TocData)
        self.GpuResourceSize = len(self.GpuData)
        self.StreamSize      = len(self.StreamData)
        self.IsModified = IsModified
    # -- Undo Modified Data -- #
    def UndoModifiedData(self):
        self.TocData = bytearray(self.TocData_OLD)
        self.GpuData = bytearray(self.GpuData_OLD)
        self.StreamData = bytearray(self.StreamData_OLD)
        self.TocDataSize     = len(self.TocData)
        self.GpuResourceSize = len(self.GpuData)
        self.StreamSize      = len(self.StreamData)
        self.IsModified = False
        if self.IsLoaded:
            self.Load(True, False)
    # -- Load Data -- #
    def Load(self, Reload=False, MakeBlendObject=True, LoadMaterialSlotNames=False):
        callback = None
        if self.TypeID == UnitID: callback = LoadStingrayUnit
        if self.TypeID == TexID: callback = LoadStingrayTexture
        if self.TypeID == MaterialID: callback = LoadStingrayMaterial
        if self.TypeID == ParticleID: callback = LoadStingrayDump
        if self.TypeID == CompositeUnitID: callback = LoadStingrayCompositeUnit
        if self.TypeID == BoneID: callback = LoadStingrayBones
        if self.TypeID == AnimationID: callback = LoadStingrayAnimation
        if self.TypeID == StateMachineID: callback = LoadStingrayStateMachine
        # if callback == None: callback = LoadStingrayDump

        if callback != None:
            if self.TypeID == UnitID:
                PrettyPrint(f"LoadMaterialSlotNames: {LoadMaterialSlotNames}")
                self.LoadedData = callback(self.FileID, self.TocData, self.GpuData, self.StreamData, Reload, MakeBlendObject, LoadMaterialSlotNames)
            else:
                self.LoadedData = callback(self.FileID, self.TocData, self.GpuData, self.StreamData, Reload, MakeBlendObject)
            if self.LoadedData == None: raise Exception("Archive Entry Load Failed")
            self.IsLoaded = True

    # -- Write Data -- #
    def Save(self, **kwargs):
        if not self.IsLoaded: self.Load(True, False)
        if self.TypeID == UnitID: callback = SaveStingrayMesh
        if self.TypeID == TexID: callback = SaveStingrayTexture
        if self.TypeID == MaterialID: callback = SaveStingrayMaterial
        if self.TypeID == ParticleID: callback = SaveStingrayDump
        if self.TypeID == AnimationID: callback = SaveStingrayAnimation
        if self.TypeID == BoneID: callback = SaveStingrayBones
        if self.TypeID == StateMachineID: callback = SaveStingrayStateMachine
        # if callback == None: callback = SaveStingrayDump

        if self.IsLoaded:
            data = callback(self, self.FileID, self.TocData, self.GpuData, self.StreamData, self.LoadedData)
            self.SetData(data[0], data[1], data[2])
        return True

class TocFileType:
    def __init__(self, ID=0, NumFiles=0):
        self.unk1     = 0
        self.TypeID   = ID
        self.NumFiles = NumFiles
        self.unk2     = 16
        self.unk3     = 64
    def Serialize(self, TocFile):
        self.unk1     = TocFile.uint64(self.unk1)
        self.TypeID   = TocFile.uint64(self.TypeID)
        self.NumFiles = TocFile.uint64(self.NumFiles)
        self.unk2     = TocFile.uint32(self.unk2)
        self.unk3     = TocFile.uint32(self.unk3)
        return self

class SearchToc:
    def __init__(self):
        self.TocEntries = {}
        self.fileIDs = []
        self.Path = ""
        self.Name = ""

    def HasEntry(self, file_id, type_id):
        try:
            return file_id in self.TocEntries[type_id]
        except KeyError:
            return False

    def FromPackage(self, package_data, package_name):
        self.UpdatePath(os.path.join(Global_gamepath, package_name))
        num_entries = int.from_bytes(package_data[8:12], "little")
        for i in range(num_entries):
            offset = 0x10+i*0x10
            type_id = int.from_bytes(package_data[offset:offset+8], "little")
            file_id = int.from_bytes(package_data[offset+8:offset+16], "little")
            self.fileIDs.append(file_id)
            try:
                self.TocEntries[type_id].append(file_id)
            except KeyError:
                self.TocEntries[type_id] = [file_id]
        return True
        
    def FromSlimFile(self, path):
        self.UpdatePath(path)
        data = get_package_toc(path)
        if not data:
            PrettyPrint(f"unable to get package {os.path.basename(path)}", 'warn')
            return False
        magic, numTypes, numFiles = struct.unpack_from("<III", data, offset=0)
        if magic != 4026531857:
            PrettyPrint(f"Incorrect magic in package {os.path.basename(path)}: {magic}", 'error')
            return False
        # maybe could save files for later?
        offset = 72 + (numTypes << 5)
        
        for _ in range(numFiles):
            file_id, type_id, toc_data_offset = struct.unpack_from("<QQQ", data, offset=offset)
            self.fileIDs.append(int(file_id))
            try:
                self.TocEntries[type_id].append(file_id)
            except KeyError:
                self.TocEntries[type_id] = [file_id]
            offset += 80
            
        return True

    def FromFile(self, path):
        self.UpdatePath(path)
        bin_data = b""
        file = open(path, 'r+b')
        bin_data = file.read(12)
        magic, numTypes, numFiles = struct.unpack("<III", bin_data)
        if magic != 4026531857:
            file.close()
            return False

        offset = 60 + (numTypes << 5)
        bin_data = file.read(offset + 80 * numFiles)
        file.close()
        for _ in range(numFiles):
            file_id, type_id = struct.unpack_from("<QQ", bin_data, offset=offset)
            try:
                self.TocEntries[type_id].append(file_id)
            except KeyError:
                self.TocEntries[type_id] = [file_id]
            offset += 80
        return True

    def UpdatePath(self, path):
        self.Path = path
        self.Name = Path(path).name

class StreamToc:
    def __init__(self):
        self.magic      = self.numTypes = self.numFiles = self.unknown = 0
        self.unk4Data   = bytearray(56)
        self.TocTypes   = []
        self.TocEntries = []
        self.TocDict = {}
        self.Path = ""
        self.Name = ""
        self.LocalName = ""

    def Serialize(self, SerializeData=True):
        # Create Toc Types Structs
        if self.TocFile.IsWriting():
            self.UpdateTypes()
        # Begin Serializing file
        if len(self.TocFile.Data) == 0 and self.TocFile.IsReading(): return False
        self.magic      = self.TocFile.uint32(self.magic)
        if self.magic != 4026531857: return False

        self.numTypes   = self.TocFile.uint32(len(self.TocTypes))
        self.numFiles   = self.TocFile.uint32(len(self.TocEntries))
        self.unknown    = self.TocFile.uint32(self.unknown)
        self.unk4Data   = self.TocFile.bytes(self.unk4Data, 56)

        if self.TocFile.IsReading():
            self.TocTypes   = [TocFileType() for n in range(self.numTypes)]
            self.TocEntries = [TocEntry() for n in range(self.numFiles)]
        # serialize Entries in correct order
        self.TocTypes   = [Entry.Serialize(self.TocFile) for Entry in self.TocTypes]
        TocEntryStart   = self.TocFile.tell()
        if self.TocFile.IsReading(): 
            self.TocEntries = [Entry.Serialize(self.TocFile) for Entry in self.TocEntries]
            for entry in self.TocEntries:
                try:
                    self.TocDict[entry.TypeID][entry.FileID] = entry
                except KeyError:
                    self.TocDict[entry.TypeID] = {}
                    self.TocDict[entry.TypeID][entry.FileID] = entry
        else:
            Index = 1
            for Type in self.TocTypes:
                for Entry in self.TocEntries:
                    if Entry.TypeID == Type.TypeID:
                        Entry.Serialize(self.TocFile, Index)
                        Index += 1

        # Serialize Data
        if SerializeData:
            for FileEntry in self.TocEntries:
                FileEntry.SerializeData(self.TocFile, self.GpuFile, self.StreamFile)

        # re-write toc entry info with updated offsets
        if self.TocFile.IsWriting():
            self.TocFile.seek(TocEntryStart)
            Index = 1
            for Type in self.TocTypes:
                for Entry in self.TocEntries:
                    if Entry.TypeID == Type.TypeID:
                        Entry.Serialize(self.TocFile, Index)
                        Index += 1
        return True

    def UpdateTypes(self):
        self.TocTypes = []
        for Entry in self.TocEntries:
            exists = False
            for Type in self.TocTypes:
                if Type.TypeID == Entry.TypeID:
                    Type.NumFiles += 1; exists = True
                    break
            if not exists:
                self.TocTypes.append(TocFileType(Entry.TypeID, 1))

    def UpdatePath(self, path):
        self.Path = path
        self.Name = Path(path).name

    def FromFile(self, path, SerializeData=True):
        self.UpdatePath(path)
        toc_data, gpu_data, stream_data = load_package(path)
        self.TocFile = MemoryStream(toc_data)
        self.GpuFile = MemoryStream(gpu_data)
        self.StreamFile = MemoryStream(stream_data)
        return self.Serialize(SerializeData)

    def ToFile(self, path=None):
        global Global_PatchBasePath
        Global_PatchBasePath = ""
        self.TocFile = MemoryStream(IOMode = "write")
        self.GpuFile = MemoryStream(IOMode = "write")
        self.StreamFile = MemoryStream(IOMode = "write")
        self.Serialize()
        if path == None: path = self.Path
        
        Global_PatchBasePath = path
        
        with open(path, 'w+b') as f:
            f.write(bytes(self.TocFile.Data))
        with open(path+".gpu_resources", 'w+b') as f:
            f.write(bytes(self.GpuFile.Data))
        with open(path+".stream", 'w+b') as f:
            f.write(bytes(self.StreamFile.Data))

    def GetFileData(self, FileID, TypeID):
        for FileEntry in self.TocEntries:
            if FileEntry.FileID == FileID and FileEntry.TypeID == TypeID:
                return FileEntry.GetData()
        return None
    def GetEntry(self, FileID, TypeID):
        for Entry in self.TocEntries:
            if Entry.FileID == int(FileID) and Entry.TypeID == TypeID:
                return Entry
        return None
    def AddEntry(self, NewEntry):
        if self.GetEntry(NewEntry.FileID, NewEntry.TypeID) != None:
            raise Exception("Entry with same ID already exists")
        try:
            self.TocDict[NewEntry.TypeID][NewEntry.FileID] = NewEntry
        except KeyError:
            self.TocDict[NewEntry.TypeID] = {}
            self.TocDict[NewEntry.TypeID][NewEntry.FileID] = NewEntry
        self.TocEntries.append(NewEntry)
        self.UpdateTypes()
    def RemoveEntry(self, FileID, TypeID):
        Entry = self.GetEntry(FileID, TypeID)
        if Entry != None:
            try:
                del self.TocDict[TypeID][FileID]
            except KeyError:
                pass
            self.TocEntries.remove(Entry)
            self.UpdateTypes()

class TocManager():
    def __init__(self):
        self.SearchArchives  = []
        self.LoadedArchives  = []
        self.ActiveArchive   = None
        self.Patches         = []
        self.ActivePatch     = None

        self.CopyBuffer      = []
        self.SelectedEntries = []
        self.DrawChain       = []
        self.LastSelected = None # Last Entry Manually Selected
        self.SavedFriendlyNames   = []
        self.SavedFriendlyNameIDs = []
    #________________________________#
    # ---- Entry Selection Code ---- #
    def SelectEntries(self, Entries, Append=False):
        if not Append: self.DeselectAll()
        if len(Entries) == 1:
            Global_TocManager.LastSelected = Entries[0]

        for Entry in Entries:
            if Entry not in self.SelectedEntries:
                Entry.IsSelected = True
                self.SelectedEntries.append(Entry)
    def DeselectEntries(self, Entries):
        for Entry in Entries:
            Entry.IsSelected = False
            if Entry in self.SelectedEntries:
                self.SelectedEntries.remove(Entry)
    def DeselectAll(self):
        for Entry in self.SelectedEntries:
            Entry.IsSelected = False
        self.SelectedEntries = []
        self.LastSelected = None

    #________________________#
    # ---- Archive Code ---- #
    def LoadArchive(self, path, SetActive=True, IsPatch=False):
        # TODO: Add error if IsPatch is true but the path is not to a patch
        global Global_MaterialParentIDs
        
        for Archive in self.LoadedArchives:
            if Archive.Path == path:
                return Archive
        archiveID = path.replace(Global_gamepath, '')
        archiveName = GetArchiveNameFromID(archiveID)
        PrettyPrint(f"Loading Archive: {archiveID} {archiveName}")
        toc = StreamToc()
        toc.FromFile(path)
    
        # add to global animation mapping:
        # global Global_AnimationMapping
        # if toc.TocDict.get(StateMachineID, None):
        #     for state_machine in toc.TocDict[StateMachineID].values():
        #         if not state_machine.IsLoaded:
        #             state_machine.Load(False, False)
        #         for animation_id in state_machine.LoadedData.animation_ids:
        #             try:
        #                 Global_AnimationMapping[animation_id].add(state_machine.FileID)
        #             except KeyError:
        #                 Global_AnimationMapping[animation_id] = set()
        #                 Global_AnimationMapping[animation_id].add(state_machine.FileID)


        if SetActive and not IsPatch:
            self.LoadedArchives.append(toc)
            self.ActiveArchive = toc
            # bpy.context.scene.Hd2ToolPanelSettings.LoadedArchives = archiveID 
        elif SetActive and IsPatch:
            self.Patches.append(toc)
            self.ActivePatch = toc
            
            # 预载材质模板
            for entry in self.ActivePatch.TocEntries:
                if entry.TypeID == MaterialID:
                    ID = GetEntryParentMaterialID(entry)
                    if ID in Global_MaterialParentIDs:
                        entry.MaterialTemplate = Global_MaterialParentIDs[ID]
                        # entry.Load()
                        PrettyPrint(f"Find Custom material, Template: {entry.MaterialTemplate}")
                    else:
                        PrettyPrint(f"Material: {entry.FileID} Parent ID: {ID} is not an custom material, skipping.")


        # Get search archives
        if len(self.SearchArchives) == 0:
            if is_slim_version():
                futures = []
                tocs = []
                executor = concurrent.futures.ThreadPoolExecutor()
                bundle_database = open(os.path.join(Global_gamepath, "bundle_database.data"), 'rb')
                bundle_database_data = bundle_database.read()
                num_packages = int.from_bytes(bundle_database_data[4:8], "little")
                for i in range(num_packages):
                    offset = 0x10 + 0x33 * i
                    name = bundle_database_data[offset:offset+0x33].decode().split("\x17")[0]
                    search_toc = SearchToc()
                    tocs.append(search_toc)
                    futures.append(executor.submit(search_toc.FromSlimFile, os.path.join(Global_gamepath, name)))
                for index, future in enumerate(futures):
                    if future.result():
                        self.SearchArchives.append(tocs[index])
                executor.shutdown()
            else:
                futures = []
                tocs = []
                executor = concurrent.futures.ThreadPoolExecutor()
                for root, dirs, files in os.walk(Path(path).parent):
                    for name in files:
                        if Path(name).suffix == "":
                            search_toc = SearchToc()
                            tocs.append(search_toc)
                            futures.append(executor.submit(search_toc.FromFile, os.path.join(root, name)))
                for index, future in enumerate(futures):
                    if future.result():
                        self.SearchArchives.append(tocs[index])
                executor.shutdown()


        return toc

    def GetEntryByLoadArchive(self, FileID: int, TypeID: int):
        return self.GetEntry(FileID, TypeID, SearchAll=False, IgnorePatch=True)
    # def GetEntryByPatch(self, FileID: int, TypeID: int):
    #     return self.GetEntry(FileID, TypeID)

    def UnloadArchives(self):
        # TODO: Make sure all data gets unloaded...
        # some how memory can still be too high after calling this
        self.LoadedArchives = []
        self.ActiveArchive  = None
        self.SearchArchives = []

    def UnloadPatches(self):
        self.Patches = []
        self.ActivePatch = None
        
        all_archives_UndoModified()
        
    def SetActive(self, Archive):
        if Archive != self.ActiveArchive:
            self.ActiveArchive = Archive
            self.DeselectAll()

    def SetActiveByName(self, Name):
        for Archive in self.LoadedArchives:
            if Archive.Name == Name:
                self.SetActive(Archive)

    #______________________#
    # ---- Entry Code ---- #
    def GetEntry(self, FileID, TypeID, SearchAll=False, IgnorePatch=False):
        # Check Active Patch
        if not IgnorePatch and self.ActivePatch != None:
            Entry = self.ActivePatch.GetEntry(FileID, TypeID)
            if Entry != None:
                return Entry
        # Check Active Archive
        if self.ActiveArchive != None:
            Entry = self.ActiveArchive.GetEntry(FileID, TypeID)
            if Entry != None:
                return Entry
        # Check All Loaded Archives
        for Archive in self.LoadedArchives:
            Entry = Archive.GetEntry(FileID, TypeID)
            if Entry != None:
                return Entry
        # Check All Search Archives
        if SearchAll:
            for Archive in self.SearchArchives:
                if Archive.HasEntry(FileID, TypeID):
                    return self.LoadArchive(Archive.Path, False).GetEntry(FileID, TypeID)
            PrettyPrint(f"Could not find entry of FileID: {FileID} TypeID: {TypeID}")
        return None

    def Load(self, FileID, TypeID, Reload=False, SearchAll=False):
        Entry = self.GetEntry(FileID, TypeID, SearchAll)
        if Entry != None: Entry.Load(Reload)
        
    def Save(self, FileID, TypeID):
        Entry = self.GetEntry(FileID, TypeID)
        if not Global_TocManager.IsInPatch(Entry):
            Entry = self.AddEntryToPatch(FileID, TypeID)

        if Entry != None: Entry.Save()

    def CopyPaste(self, Entry, GenID = False, NewID = None):
        if self.ActivePatch == None:
            raise Exception("No patch exists, please create one first")
        if self.ActivePatch:
            dup = deepcopy(Entry)
            dup.IsCreated = True
            # if self.ActivePatch.GetEntry(dup.FileID, dup.TypeID) != None and NewID == None:
            #     GenID = True
            if GenID and NewID == None: dup.FileID = r.randint(1, 0xffffffffffffffff)
            if NewID != None:
                dup.FileID = NewID

            self.ActivePatch.AddEntry(dup)
    def Copy(self, Entries):
        self.CopyBuffer = []
        for Entry in Entries:
            if Entry != None: self.CopyBuffer.append(Entry)
    def Paste(self, GenID = False, NewID = None):
        if self.ActivePatch == None:
            raise Exception("No patch exists, please create one first")
        if self.ActivePatch:
            for ToCopy in self.CopyBuffer:
                self.CopyPaste(ToCopy, GenID, NewID)
            self.CopyBuffer = []

    def ClearClipboard(self):
        self.CopyBuffer = []

    #______________________#
    # ---- Patch Code ---- #
    def PatchActiveArchive(self,path=None):
        self.ActivePatch.ToFile(path= path)

    def CreatePatchFromActive(self,NewPatchIndex):
        if self.ActiveArchive == None:
            raise Exception("没有激活的Archive，无法创建Patch，请先载入一个Archive。")

        self.ActivePatch = deepcopy(self.ActiveArchive)
        self.ActivePatch.TocEntries  = []
        self.ActivePatch.TocTypes    = []
        # TODO: ask for which patch index
        path = self.ActiveArchive.Path
        if path.find(".patch_") != -1:
            path = path[:path.find(".patch_")] + ".patch_" + str(NewPatchIndex)
        else:
            path = path + ".patch_" + str(NewPatchIndex)
        self.ActivePatch.UpdatePath(path)
        self.Patches.append(self.ActivePatch)
        
    def RenameActivePatch(self, NewPath):
        if self.ActivePatch == None:
            raise Exception("没有激活的Patch，无法重命名，请先创建一个Patch。")
        path = self.ActivePatch.Path
        
        fileNamelist = path.split("\\")[:-1]
        fileNamelist.append(NewPath)
        Rename_path = "\\".join(fileNamelist)
        return Rename_path
        

    def SetActivePatch(self, Patch):
        self.ActivePatch = Patch

    def SetActivePatchByName(self, Name):
        for Patch in self.Patches:
            if Patch.Name == Name:
                self.SetActivePatch(Patch)
    def CheckActivePatch(self):
        if self.ActivePatch == None:
            return False
        else:
            return True

    def AddNewEntryToPatch(self, Entry):
        if self.ActivePatch == None:
            raise Exception("没有激活的Patch，无法添加Entry，请先创建一个Patch。")
        self.ActivePatch.AddEntry(Entry)

    def AddEntryToPatch(self, FileID, TypeID):
        if self.ActivePatch == None:
            raise Exception("没有激活的Patch，无法添加Entry，请先创建一个Patch。")

        Entry = self.GetEntry(FileID, TypeID)
        if Entry != None:
            PatchEntry = deepcopy(Entry)
            if PatchEntry.IsSelected:
                self.SelectEntries([PatchEntry], True)
            self.ActivePatch.AddEntry(PatchEntry)
            return PatchEntry
        return None

    def RemoveEntryFromPatch(self, FileID, TypeID):
        if self.ActivePatch != None:
            self.ActivePatch.RemoveEntry(FileID, TypeID)
        return None

    def GetPatchEntry(self, Entry):
        if self.ActivePatch != None:
            return self.ActivePatch.GetEntry(Entry.FileID, Entry.TypeID)
        return None
    def GetPatchEntry_B(self, FileID, TypeID):
        if self.ActivePatch != None:
            return self.ActivePatch.GetEntry(FileID, TypeID)
        return None

    def IsInPatch(self, Entry):
        if self.ActivePatch != None:
            PatchEntry = self.ActivePatch.GetEntry(Entry.FileID, Entry.TypeID)
            if PatchEntry != None: return True
            else: return False
        return False

    def DuplicateEntry(self, FileID, TypeID, NewID):
        Entry = self.GetEntry(FileID, TypeID)
        if Entry != None:
            self.CopyPaste(Entry, False, NewID)

#endregion

#region Classes and Functions: Stingray Animation
def LoadStingrayAnimation(ID, TocData, GpuData, StreamData, Reload, MakeBlendObject):
    toc = MemoryStream(TocData)
    PrettyPrint("Loading Animation")
    animation = StingrayAnimation()
    animation.Serialize(toc)
    PrettyPrint("Finished Loading Animation")
    if MakeBlendObject: # To-do: create action for armature
        context = bpy.context
        armature = context.active_object
        try:
            bones_id = int(armature['BonesID'])
        except ValueError:
            raise Exception(f"\n\nCould not obtain custom property: BonesID from armature: {armature.name}. Please make sure this is a valid value")
        try:
            state_machine_id = int(armature['StateMachineID'])
        except ValueError:
            raise Exception(f"\n\nCould not obtain custom property: StateMachineID from armature: {armature.name}. Please make sure this is a valid value")
        state_machine_entry = Global_TocManager.GetEntryByLoadArchive(int(state_machine_id), StateMachineID)
        if not state_machine_entry:
            raise AnimationException("This animation is not for this armature")
        if not state_machine_entry.IsLoaded:
            state_machine_entry.Load()
        if int(ID) not in state_machine_entry.LoadedData.animation_ids:
            raise AnimationException("This animation is not for this armature")
        bones_entry = Global_TocManager.GetEntry(int(bones_id), BoneID, SearchAll=True, IgnorePatch=False)
        if not bones_entry.IsLoaded:
            bones_entry.Load()
        bones_data = bones_entry.TocData
        state_machine_entry = Global_TocManager.GetEntry(int(state_machine_id), StateMachineID, SearchAll=True, IgnorePatch=False)
        if not state_machine_entry.IsLoaded:
            state_machine_entry.Load()
        state_machine_data = state_machine_entry.LoadedData
        animation.to_action(context, armature, bones_data, state_machine_data, ID)
    return animation
    
def SaveStingrayAnimation(self, ID, TocData, GpuData, StreamData, Animation):
    toc = MemoryStream(IOMode = "write")
    Animation.Serialize(toc)
    return [toc.Data, b"", b""]

#endregion

#region Classes and Functions: Stingray State Machines
def LoadStingrayStateMachine(ID, TocData, GpuData, StreamData, Reload, MakeBlendObject):
    toc = MemoryStream(TocData)
    state_machine = StingrayStateMachine()
    state_machine.Serialize(toc)
    return state_machine
    
def SaveStingrayStateMachine(self, ID, TocData, GpuData, StreamData, StateMachine):
    toc = MemoryStream(IOMode = "write")
    StateMachine.Serialize(toc)
    return [toc.Data, b"", b""]
#endregion

#region Classes and Functions: Stingray Materials


def LoadStingrayMaterial(ID, TocData, GpuData, StreamData, Reload, MakeBlendObject):
    exists = True
    force_reload = False
    try:
        mat = bpy.data.materials[str(ID)]
        if not mat.use_nodes: force_reload = True
    except: exists = False


    f = MemoryStream(TocData)
    Material = StingrayMaterial()
    Material.Serialize(f)
    if MakeBlendObject and not (exists and not Reload): AddMaterialToBlend(ID, Material)
    elif force_reload: AddMaterialToBlend(ID, Material, True)
    return Material

def SaveStingrayMaterial(self, ID, TocData, GpuData, StreamData, LoadedData):
    mat = LoadedData
    for TexIdx in range(len(mat.TexIDs)):
        oldTexID = mat.TexIDs[TexIdx]
        if mat.DEV_DDSPaths[TexIdx] != None:
            
            if CheckTextureExtension(mat.DEV_DDSPaths[TexIdx]):
                # get texture data
                StingrayTex = StingrayTexture()
                with open(mat.DEV_DDSPaths[TexIdx], 'r+b') as f:
                    StingrayTex.FromDDS(f.read())
            else: # 如果是tga,就使用texconv转换dds并保存到软件缓存路径
                tempdir = tempfile.gettempdir()
                if "[-_彩色_-]" in mat.DEV_DDSPaths[TexIdx]:
                    mat.DEV_DDSPaths[TexIdx] = mat.DEV_DDSPaths[TexIdx].replace("[-_彩色_-]", "")
                    DDS_Export_SRGB(tempdir=tempdir,input_path=mat.DEV_DDSPaths[TexIdx])
                    
                elif "[-_线性_-] " in mat.DEV_DDSPaths[TexIdx]:
                    mat.DEV_DDSPaths[TexIdx] = mat.DEV_DDSPaths[TexIdx].replace("[-_线性_-] ", "") 
                    DDS_Export_Linear(tempdir=tempdir,input_path=mat.DEV_DDSPaths[TexIdx])
                    
                else:
                    DDS_Export_SRGB(tempdir=tempdir,input_path=mat.DEV_DDSPaths[TexIdx])
                
                tga2dds_path = os.path.join(tempdir, CheckTextureName(mat.DEV_DDSPaths[TexIdx])+".dds")
                StingrayTex = StingrayTexture()
                with open(tga2dds_path, 'r+b') as f:
                    StingrayTex.FromDDS(f.read())
                
            Toc = MemoryStream(IOMode="write")
            Gpu = MemoryStream(IOMode="write")
            Stream = MemoryStream(IOMode="write")
            StingrayTex.Serialize(Toc, Gpu, Stream)
            # add texture entry to archive
            Entry = TocEntry()
            
            TextureID = oldTexID
            if bpy.context.scene.Hd2ToolPanelSettings.GenerateRandomTextureIDs:
               TextureID = r.randint(1, 0xffffffffffffffff)
            
            Entry.FileID = TextureID 
            Entry.TypeID = TexID
            Entry.IsCreated = True
            Entry.SetData(Toc.Data, Gpu.Data, Stream.Data, False)
            Global_TocManager.AddNewEntryToPatch(Entry)
            mat.TexIDs[TexIdx] = TextureID
                
        else:
            Global_TocManager.Load(int(mat.TexIDs[TexIdx]), TexID, False, True)
            Entry = Global_TocManager.GetEntry(int(mat.TexIDs[TexIdx]), TexID, True)
            if Entry != None:
                Entry = deepcopy(Entry)
                
                TextureID = oldTexID
                if bpy.context.scene.Hd2ToolPanelSettings.GenerateRandomTextureIDs:
                    TextureID = r.randint(1, 0xffffffffffffffff)
                
                Entry.FileID = TextureID 
                Entry.IsCreated = True
                
                ExistingEntry = Global_TocManager.GetEntry(Entry.FileID, Entry.TypeID)
                if ExistingEntry:
                    Global_TocManager.RemoveEntryFromPatch(ExistingEntry.FileID, ExistingEntry.TypeID)

                Global_TocManager.AddNewEntryToPatch(Entry)
                mat.TexIDs[TexIdx] = TextureID
                
        if bpy.context.scene.Hd2ToolPanelSettings.GenerateRandomTextureIDs:
            Global_TocManager.RemoveEntryFromPatch(oldTexID, TexID)
    f = MemoryStream(IOMode="write")
    LoadedData.Serialize(f)
    return [f.Data, b"", b""]

def AddMaterialToBlend(ID, StringrayMat, EmptyMatExists=False):
    if EmptyMatExists:
        mat = bpy.data.materials[str(ID)]
    else:
        mat = bpy.data.materials.new(str(ID)); mat.name = str(ID)

    mat.diffuse_color = (r.random(), r.random(), r.random(), 1)
    mat.use_nodes = True
    # bsdf = mat.node_tree.nodes["Principled BSDF"]
    idx = 0
    for TextureID in StringrayMat.TexIDs:
        # Create Node
        texImage = mat.node_tree.nodes.new('ShaderNodeTexImage')
        texImage.location = (-450, 850 - 300*idx)

        # Load Texture
        try:    bpy.data.images[str(TextureID)]
        except: Global_TocManager.Load(TextureID, TexID, False, True)
        # Apply Texture
        try: texImage.image = bpy.data.images[str(TextureID)]
        except:
            PrettyPrint(f"Failed to load texture {TextureID}. This is not fatal, but does mean that the materials in Blender will have empty image texture nodes", "warn")
            pass
        idx +=1



# 检查导入纹理的格式
def CheckTextureExtension(TexPath):
    file_name,file_extension = os.path.splitext(os.path.basename(TexPath))
    if file_extension.lower() == ".dds":
        return True
    else:
        return False
    
def CheckTextureName(TexPath):
    file_name,file_extension = os.path.splitext(os.path.basename(TexPath))
    return file_name


def DDS_Export_SRGB(tempdir,input_path):
    subprocess.run([Global_texconvpath, "-y", "-o", tempdir, "-ft", "dds", "-dx10", "-f", "R8G8B8A8_UNORM_SRGB","-m","1","-srgb","-alpha","-sepalpha", "--tga-zero-alpha",input_path ], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    PrettyPrint("DDS_Export_SRGB", "info")
    
def DDS_Export_Linear(tempdir,input_path):
    subprocess.run([Global_texconvpath, "-y", "-o", tempdir, "-ft", "dds", "-dx10", "-f", "R8G8B8A8_UNORM","-m","1","--ignore-srgb","-alpha","-sepalpha", "--tga-zero-alpha",input_path ], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    PrettyPrint("DDS_Export_Linear", "info")
#endregion

#region Classes and Functions: Stingray Textures



def LoadStingrayTexture(ID, TocData, GpuData, StreamData, Reload, MakeBlendObject):
    exists = True
    try: bpy.data.images[str(ID)]
    except: exists = False

    StingrayTex = StingrayTexture()
    StingrayTex.Serialize(MemoryStream(TocData), MemoryStream(GpuData), MemoryStream(StreamData))
    dds = StingrayTex.ToDDS()

    if MakeBlendObject and not (exists and not Reload):
        tempdir = tempfile.gettempdir()
        dds_path = f"{tempdir}/{ID}.dds"
        tga_path = f"{tempdir}/{ID}.tga"

        with open(dds_path, 'w+b') as f:
            f.write(dds)
        
        subprocess.run([Global_texconvpath, "-y", "-o", tempdir, "-ft", "tga", "-f", "R8G8B8A8_UNORM","-alpha","-sepalpha", "--tga-zero-alpha", dds_path], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

        if os.path.isfile(tga_path):
            image = bpy.data.images.load(tga_path)
            image.name = str(ID)
            image.pack()
        else:
            raise Exception(f"Failed to convert texture {ID} to TGA, or DDS failed to export")
    
    return StingrayTex

def BlendImageToStingrayTexture(image, StingrayTex):
    tempdir  = tempfile.gettempdir()
    dds_path = f"{tempdir}/blender_img.dds"
    tga_path = f"{tempdir}/blender_img.tga"

    image.file_format = 'TARGA_RAW'
    image.filepath_raw = tga_path
    image.save()

    subprocess.run([Global_texconvpath, "-y", "-o", tempdir, "-ft", "dds", "-dx10", "-f", "R8G8B8A8_UNORM","-m","1","--ignore-srgb","-alpha","-sepalpha", "--tga-zero-alpha", tga_path], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    
    if os.path.isfile(dds_path):
        with open(dds_path, 'r+b') as f:
            StingrayTex.FromDDS(f.read())
    else:
        raise Exception("Failed to convert TGA to DDS")

def SaveStingrayTexture(self,ID, TocData, GpuData, StreamData, LoadedData):
    exists = True
    try: bpy.data.images[str(ID)]
    except: exists = False

    Toc = MemoryStream(IOMode="write")
    Gpu = MemoryStream(IOMode="write")
    Stream = MemoryStream(IOMode="write")

    LoadedData.Serialize(Toc, Gpu, Stream)

    return [Toc.Data, Gpu.Data, Stream.Data]

#endregion

#region Classes and Functions: Stingray Bones

def LoadStingrayBones(ID, TocData, GpuData, StreamData, Reload, MakeBlendObject):
    StingrayBonesData = StingrayBones(Global_BoneNames)
    StingrayBonesData.Serialize(MemoryStream(TocData))
    return StingrayBonesData

def SaveStingrayBones(self, ID, TocData, GpuData, StreamData, LoadedData):
    f = MemoryStream(TocData, IOMode="write") # Load in original TocData before overwriting it
    LoadedData.Serialize(f)
    return [f.Data, b"", b""]

#endregion

#region Classes and Functions: Stingray Composite Meshes


def LoadStingrayCompositeUnit(ID, TocData, GpuData, StreamData, Reload, MakeBlendObject):
    StingrayCompositeMeshData = StingrayCompositeUnit()
    StingrayCompositeMeshData.Serialize(MemoryStream(TocData), MemoryStream(GpuData))
    return StingrayCompositeMeshData

#endregion

#region Classes and Functions: Stingray Meshes

def LoadStingrayUnit(ID, TocData, GpuData, StreamData, Reload, MakeBlendObject, LoadMaterialSlotNames=False):
    toc  = MemoryStream(TocData)
    gpu  = MemoryStream(GpuData)
        
    
    StingrayMesh = StingrayMeshFile()
    StingrayMesh.NameHash = int(ID)
    StingrayMesh.LoadMaterialSlotNames = LoadMaterialSlotNames
    StingrayMesh.Serialize(toc, gpu, Global_TocManager)
    bones_entry = Global_TocManager.GetEntry(StingrayMesh.BonesRef, BoneID, SearchAll=True, IgnorePatch=False)
    if bones_entry and not bones_entry.IsLoaded:
        bones_entry.Load(False, False)
    state_machine_entry = Global_TocManager.GetEntry(StingrayMesh.StateMachineRef, StateMachineID, SearchAll=True, IgnorePatch=False)
    if state_machine_entry and not state_machine_entry.IsLoaded:
        state_machine_entry.Load(False, False)
    if MakeBlendObject and bones_entry and state_machine_entry: 
        CreateModel(StingrayMesh, str(ID), Global_BoneNames,Global_NameHashes, bones_entry.LoadedData, state_machine_entry.LoadedData)
    elif MakeBlendObject: 
        CreateModel(StingrayMesh, str(ID), Global_BoneNames,Global_NameHashes, None, None)
    return StingrayMesh

def SaveStingrayMesh(self,ID, TocData, GpuData, StreamData, StingrayMesh):
    # model = GetObjectsMeshData(Global_TocManager, Global_BoneNames)
    # # print(f"模型：{model}")
    # # raise Exception("停止")
    # FinalMeshes = [mesh for mesh in StingrayMesh.RawMeshes]
    # for mesh in model:
    #     for n in range(len(StingrayMesh.RawMeshes)):
    #         if StingrayMesh.RawMeshes[n].MeshInfoIndex == mesh.MeshInfoIndex:
    #             FinalMeshes[n] = mesh
    if bpy.context.scene.Hd2ToolPanelSettings.AutoLods:
        lod0 = None
        for mesh in StingrayMesh.RawMeshes:
            if mesh.LodIndex == 0:
                lod0 = mesh
                break
        # print(lod0)
        if lod0 != None:
            for n in range(len(StingrayMesh.RawMeshes)):
                if StingrayMesh.RawMeshes[n].IsLod():
                    newmesh = copy.copy(lod0)
                    newmesh.MeshInfoIndex = StingrayMesh.RawMeshes[n].MeshInfoIndex
                    StingrayMesh.RawMeshes[n] = newmesh
    # StingrayMesh.RawMeshes = FinalMeshes
    toc  = MemoryStream(IOMode = "write")
    gpu  = MemoryStream(IOMode = "write")
    StingrayMesh.Serialize(toc, gpu, Global_TocManager)
    return [toc.Data, gpu.Data, b""]

#endregion

#region Classes and Functions: Stingray Particles
def LoadStingrayParticle(ID, TocData, GpuData, StreamData, Reload, MakeBlendObject):
    f = MemoryStream(TocData)
    Particle = StingrayParticles()
    Particle.Serialize(f)
    return Particle

def SaveStingrayParticle(self, ID, TocData, GpuData, StreamData, LoadedData):
    f = MemoryStream(TocData, IOMode="write") # Load in original TocData before overwriting it
    LoadedData.Serialize(f)
    return [f.Data, b"", b""]

def LoadStingrayDump(ID, TocData, GpuData, StreamData, Reload, MakeBlendObject):
    StingrayDumpData = StingrayRawDump()
    return StingrayDumpData

def SaveStingrayDump(self, ID, TocData, GpuData, StreamData, LoadedData):
    return [TocData, GpuData, StreamData]
#endregion



#region Operators: Panel Settings
class ButtonOpenCacheDirectory(Operator):
    bl_idname ="helldiver2.open_cache_directory"
    bl_label = "Open Cache Directory"
    bl_description = "打开缓存目录"
    
    def execute(self, context):
        tempdir = tempfile.gettempdir()
        os.startfile(tempdir)
    
        return {"FINISHED"}
    
    
class ButtonOpenPatchOutDirectory(Operator):
    bl_idname ="helldiver2.open_patch_out_directory"
    bl_label = "Open Patch Out Directory"
    bl_description = "打开输出目录"
    
    @classmethod
    def poll(cls, context):
        return os.path.exists(Global_PatchBasePath)
    
    
    def execute(self, context):
        tempdir = tempfile.gettempdir()
        if os.path.dirname(Global_PatchBasePath) != tempdir:
            os.startfile(os.path.dirname(Global_PatchBasePath))

        
        return {"FINISHED"}

class ButtonAutoRenamePatch(Operator):
    bl_idname ="helldiver2.button_auto_rename_patch"
    bl_label = "auto_rename_patch"
    bl_description = "将patch重命名为基础资产的patch"
    
    NewRenamePatchIndex : IntProperty(name="序号", default=0)
    def draw(self, context):
        layout = self.layout; row = layout.row()
        row.label(text="Patch末尾序号:")
        row.prop(self, "NewRenamePatchIndex", icon='COPY_ID')
    
    def execute(self, context):
        bpy.context.scene.Hd2ToolPanelSettings.NewPatchName = "9ba626afa44a3aa3.patch_" + str(self.NewRenamePatchIndex)
        
            # Redraw
        for area in context.screen.areas:
            if area.type == "VIEW_3D": area.tag_redraw()
        
        return {"FINISHED"}
    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)

class ButtonUpdateArchivelistCN(Operator):
    bl_label = "更新已知资产列表"
    bl_idname = "helldiver2.update_archivelist_cn"
    bl_description = "从中文共享ID收集表更新已知资产列表"
    
    def execute(self, context):
        if get_update_archivelistCN.GetAndUpdateArchivelistCN():
            LoadUpdateArchiveList_CN()
            self.report({'INFO'}, "更新完成")
        else:
            with open(get_update_archivelistCN.Global_ErrorLog, 'r', encoding='utf-8') as f:
                self.report({'ERROR'}, f"更新失败，存在错误: {f.read()}. 错误日志见:{get_update_archivelistCN.Global_ErrorLog}")
        return {"FINISHED"}
#endregion

#region Operators: Archives & Patches
def ArchivesNotLoaded(self):
    if len(Global_TocManager.LoadedArchives) <= 0:
        self.report({'ERROR'}, "No Archives Currently Loaded")
        return True
    else: 
        return False
    
def PatchesNotLoaded(self):
    if len(Global_TocManager.Patches) <= 0:
        self.report({'ERROR'}, "No Patches Currently Loaded")
        return True
    else:
        return False

def hex_to_decimal(hex_string):
    try:
        decimal_value = int(hex_string, 16)
        return decimal_value
    except ValueError:
        PrettyPrint(f"Invalid hexadecimal string: {hex_string}")

class DefaultLoadArchiveOperator(Operator):
    bl_label = "Default Archive"
    bl_description = "载入默认基础资产"
    bl_idname = "helldiver2.archive_import_default"

    def execute(self, context):
        path = Global_gamepath + BaseArchiveHexID
        if not os.path.exists(Global_gamepath):
            self.report({'ERROR'}, "Current Filepath is Invalid. Change this in the Settings")
            context.scene.Hd2ToolPanelSettings.MenuExpanded = True
            return{'CANCELLED'}
        Global_TocManager.LoadArchive(path, True, False)

        # Redraw
        for area in context.screen.areas:
            if area.type == "VIEW_3D": area.tag_redraw()
        
        return{'FINISHED'}

class LoadArchiveOperator(Operator, ImportHelper):
    bl_label = "Load Archive"
    bl_idname = "helldiver2.archive_import"
    
    filter_glob: StringProperty(default='*', options={'HIDDEN'})

    is_patch: BoolProperty(name="is_patch", default=False, options={'HIDDEN'})
    
    filepath: StringProperty(name="File Path", subtype='FILE_PATH', default="")
    
    def execute(self, context):
        # Sanitize path by removing any provided extension, so the correct TOC file is loaded
        if not self.filepath:
            self.report({'ERROR'}, "文件路径不能为空。")
            return {'CANCELLED'}
            
        path = Path(self.filepath)
        if not path.suffix.startswith(".patch_"): path = path.with_suffix("")

        Global_TocManager.LoadArchive(str(path), True, self.is_patch)

        # Redraw
        for area in context.screen.areas:
            if area.type == "VIEW_3D": area.tag_redraw()
            
        self.filepath = ""
        return{'FINISHED'}
        
    def invoke(self, context, event):
        wm = context.window_manager

        if self.filepath:
            return {'FINISHED'}
        else:
            self.filepath = bpy.path.abspath(Global_gamepath)
            wm.fileselect_add(self)
            return {'RUNNING_MODAL'} 

class UnloadArchivesOperator(Operator):
    bl_label = "Unload Archives"
    bl_idname = "helldiver2.archive_unloadall"

    def execute(self, context):
        Global_TocManager.UnloadArchives()
        return{'FINISHED'}

class UnloadPatchesOperator(Operator):
    bl_label = "Unload Patches"
    bl_idname = "helldiver2.patches_unloadall"
    bl_description = "Unloads All Current Loaded Patches"

    def execute(self, context):
        Global_TocManager.UnloadPatches()
        return{'FINISHED'}

class CreatePatchFromActiveOperator(Operator):
    bl_label = "Create Patch"
    bl_idname = "helldiver2.archive_createpatch"

    NewPatchIndex : IntProperty(name="新Patch序号", default=0)
    def draw(self, context):
        layout = self.layout; row = layout.row()
        row.prop(self, "NewPatchIndex", icon='COPY_ID')
        # print("NewPatchIndex:", self.NewPatchIndex)
    
    def execute(self, context):
        Global_TocManager.CreatePatchFromActive(NewPatchIndex = self.NewPatchIndex)

        # Redraw
        for area in context.screen.areas:
            if area.type == "VIEW_3D": area.tag_redraw()
        return{'FINISHED'}
        
    def invoke(self, context, event):
        wm = context.window_manager
        if self.NewPatchIndex == 0:
            return wm.invoke_props_dialog(self)
        else:
            return {'FINISHED'}
    
    def execute(self, context):
        Global_TocManager.CreatePatchFromActive(NewPatchIndex = self.NewPatchIndex)

        # Redraw
        for area in context.screen.areas:
            if area.type == "VIEW_3D": area.tag_redraw()
        
        return{'FINISHED'}
    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)

class PatchArchiveOperator(Operator):
    bl_label = "Patch Archive"
    bl_idname = "helldiver2.archive_export"

    def execute(self, context):
        global Global_TocManager
        global Global_PatchBasePath
        
        if bpy.context.scene.Hd2ToolPanelSettings.IsChangeOutPath and bpy.context.scene.Hd2ToolPanelSettings.NewPatchOutPath:
            if bpy.context.scene.Hd2ToolPanelSettings.IsRenamePatch and bpy.context.scene.Hd2ToolPanelSettings.NewPatchName:
                check_name = bpy.context.scene.Hd2ToolPanelSettings.NewPatchName
                if check_name.find(".patch_") == -1:
                    self.report({'ERROR'}, "文件名没有加上.patch_，自行检查")
                    return {'CANCELLED'}
                if check_name.find("9ba626afa44a3aa3.patch_") == -1:
                    self.report({'WARNING'}, "没有重命名为基础资产的patch")
                # New_path = Global_TocManager.RenameActivePatch(NewPath = bpy.context.scene.Hd2ToolPanelSettings.NewPatchName)
                New_path = os.path.join(bpy.context.scene.Hd2ToolPanelSettings.NewPatchOutPath, check_name)
                Global_TocManager.PatchActiveArchive(path= New_path)
            else:
                New_path = os.path.join(bpy.context.scene.Hd2ToolPanelSettings.NewPatchOutPath, Global_TocManager.ActivePatch.Name)
                Global_TocManager.PatchActiveArchive(path= New_path)
        else:
            if bpy.context.scene.Hd2ToolPanelSettings.IsRenamePatch and bpy.context.scene.Hd2ToolPanelSettings.NewPatchName:
                check_name = bpy.context.scene.Hd2ToolPanelSettings.NewPatchName
                if check_name.find(".patch_") == -1:
                    self.report({'ERROR'}, "文件名没有加上.patch_，自行检查")
                    return {'CANCELLED'}
                if check_name.find("9ba626afa44a3aa3.patch_") == -1:
                    self.report({'WARNING'}, "没有重命名为基础资产的patch")
                New_path = Global_TocManager.RenameActivePatch(NewPath = bpy.context.scene.Hd2ToolPanelSettings.NewPatchName)
                Global_TocManager.PatchActiveArchive(path= New_path)
            else:
                Global_TocManager.PatchActiveArchive()

        self.report({'INFO'}, f"写入patch完成,文件保存在{Global_PatchBasePath}")
        bpy.context.scene.Hd2ToolPanelSettings.IsZipPatch = False
        return{'FINISHED'}
    
class ZipPatchArchiveOperator(Operator,ExportHelper):
    bl_label = "Zip Patch Export"
    bl_idname = "helldiver2.archive_zippatch_export"
    bl_description = "将patch导出为zip文件"
    
    filename_ext = ".zip"
    
    filter_glob: StringProperty(
        default="*.zip",
        options={"HIDDEN"},

    )
    filepath: StringProperty(
        default="patch.zip",
        subtype="FILE_PATH",
    )
    
    
    @classmethod
    def poll(cls, context):
        return Global_TocManager.ActivePatch != None
    
    
    def execute(self, context):
        tempdir = tempfile.gettempdir()
        global Global_TocManager
        global Global_PatchBasePath
        

        if bpy.context.scene.Hd2ToolPanelSettings.IsRenamePatch and bpy.context.scene.Hd2ToolPanelSettings.NewPatchName:
            check_name = bpy.context.scene.Hd2ToolPanelSettings.NewPatchName
            if check_name.find(".patch_") == -1:
                self.report({'ERROR'}, "文件名没有加上.patch_，自行检查")
                return {'CANCELLED'}
            if check_name.find("9ba626afa44a3aa3.patch_") == -1:
                self.report({'WARNING'}, "没有重命名为基础资产的patch")
            # New_path = Global_TocManager.RenameActivePatch(NewPath = bpy.context.scene.Hd2ToolPanelSettings.NewPatchName)
            New_path = os.path.join(tempdir, check_name)
            Global_TocManager.PatchActiveArchive(path= New_path)
        else:
            New_path = os.path.join(tempdir, Global_TocManager.ActivePatch.Name)
            Global_TocManager.PatchActiveArchive(path= New_path)

        # 打包文件
        zipfileOutPath = self.filepath
        # if 
        with zipfile.ZipFile(zipfileOutPath, 'w',zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(New_path , arcname= os.path.basename(New_path))
            zipf.write(New_path + ".gpu_resources",arcname= os.path.basename(New_path) + ".gpu_resources")
            zipf.write(New_path + ".stream",arcname= os.path.basename(New_path) + ".stream")

        bpy.context.scene.Hd2ToolPanelSettings.IsZipPatch = True
        self.report({'INFO'}, f"patch打包完成,文件保存在{self.filepath}")
        return {'FINISHED'}
    def invoke(self, context, event):
        # self.filepath = ""
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

class ChangeFilepathOperator(Operator, ImportHelper):
    bl_label = "Change Filepath"
    bl_idname = "helldiver2.change_filepath"
    bl_description = "更改游戏data目录文件夹路径"
    #filename_ext = "."
    use_filter_folder = True

    filter_glob: StringProperty(options={'HIDDEN'}, default='')

    def __init__(self):
        global Global_gamepath
        self.filepath = bpy.path.abspath(Global_gamepath)
        
    def execute(self, context):
        global Global_gamepath
        addon_prefs = AQ_PublicClass.get_addon_prefs()
        filepath = self.filepath
        if not addon_prefs.CustomGamePath: # 自定义游戏文件目录，不检查steamapp目录
            steamapps = "steamapps"
            if steamapps in filepath:
                filepath = f"{filepath.partition(steamapps)[0]}steamapps/common/Helldivers 2/data/ "[:-1]
            else:
                self.report({'ERROR'}, f"无法在此目录下找到steamapps文件夹: {filepath}，如果你单独复制了游戏文件目录，请在设置中启用自定义游戏文件目录选项")
                return{'CANCELLED'}
        Global_gamepath = filepath
        UpdateConfig()
        PrettyPrint(f"Changed Game File Path: {Global_gamepath}")
        return{'FINISHED'}
    
class AddSwapsID_property(Operator):
    bl_label =  "Add_Swaps_ID"
    bl_idname = "helldiver2.add_swaps_id_prop"
    bl_description = "一键添加转移自定义ID属性(可多选物体), 只有先前存在HD2自定义属性的物体才会被添加"
    
    
    SwapsID_amount : IntProperty(
        name="需要添加的转移ID数量",
        description="需要添加的转移ID数量，不推荐过多添加",
        default=1,
        min=1,
        max=200,
    )
    
    def draw(self, context):
        layout = self.layout; row = layout.row()
        row.prop(self, "SwapsID_amount", icon='COPY_ID')
    
    @classmethod
    def poll(cls, context):
        active_obj = context.active_object
        if active_obj:
            return active_obj.type == "MESH"
        return False

    def execute(self, context):
        active_obj = context.active_object
        if active_obj == None: 
            self.report({'ERROR'}, "请至少选择一个物体")
            return{'CANCELLED'}
        select_objs = context.selected_objects
        add_count = 0
        
        for obj in select_objs:
            has_swap_id = False
            ori_swap = ""
            if obj.type != "MESH":continue
            try:ObjectID = obj["Z_ObjectID"]
            except:continue
            
            SwapID_keys_id = [int(key.split("_")[-1]) for key in obj.keys() if key.startswith("Z_SwapID_")]
            
            
            
            try:
                obj["Z_SwapID"]
            except:
                pass
            else:
                if obj["Z_SwapID"] != "":
                    has_swap_id = True
                    ori_swap = obj["Z_SwapID"]
                    
                    del obj["Z_SwapID"]
                else:
                    del obj["Z_SwapID"]
                    obj["Z_SwapID_0"] = ""
            
            if ObjectID  !=  None:
                if len(SwapID_keys_id) != 0:
                    SwapID_keys_id.sort()
                    new_id = SwapID_keys_id[-1] + 1
                    for i in range(self.SwapsID_amount):
                        obj[f"Z_SwapID_{new_id + i}"] = ""
                else:
                    try:
                        obj["Z_SwapID_0"]
                    except:
                        for i in range(self.SwapsID_amount):
                            obj[f"Z_SwapID_{i}"] = ""
                    else:
                        for i in range(self.SwapsID_amount):
                            obj[f"Z_SwapID_{i + 1}"] = ""
                if has_swap_id:
                    obj["Z_SwapID_0"] = ori_swap
                
                add_count += 1
                
        self.report({'INFO'}, f"总共为选择的物体添加{add_count * self.SwapsID_amount}个空交换ID属性")        
        
        return {'FINISHED'}
    
    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    
    
    

    
#endregion

#region Operators: Entries

class ArchiveEntryOperator(Operator):
    bl_label  = "Archive Entry"
    bl_idname = "helldiver2.archive_entry"

    object_id: StringProperty()
    object_typeid: StringProperty()
    def execute(self, context):
        return{'FINISHED'}

    def invoke(self, context, event):
        Entry = Global_TocManager.GetEntry(int(self.object_id), int(self.object_typeid))
        if event.ctrl:
            if Entry.IsSelected:
                Global_TocManager.DeselectEntries([Entry])
            else:
                Global_TocManager.SelectEntries([Entry], True)
            return {'FINISHED'}
        if event.shift:
            if Global_TocManager.LastSelected != None:
                LastSelected = Global_TocManager.LastSelected
                StartIndex   = LastSelected.DEV_DrawIndex
                EndIndex     = Entry.DEV_DrawIndex
                Global_TocManager.DeselectAll()
                Global_TocManager.LastSelected = LastSelected
                if StartIndex > EndIndex:
                    Global_TocManager.SelectEntries(Global_TocManager.DrawChain[EndIndex:StartIndex+1], True)
                else:
                    Global_TocManager.SelectEntries(Global_TocManager.DrawChain[StartIndex:EndIndex+1], True)
            else:
                Global_TocManager.SelectEntries([Entry], True)
            return {'FINISHED'}

        Global_TocManager.SelectEntries([Entry])
        return {'FINISHED'}
    
class MaterialShaderVariableEntryOperator(Operator):
    bl_label = "Shader Variable"
    bl_idname = "helldiver2.material_shader_variable"
    bl_description = "Material Shader Variable"

    object_id: StringProperty()
    variable_index: bpy.props.IntProperty()
    value_index: bpy.props.IntProperty()
    value: bpy.props.FloatProperty(
        name="Variable Value",
        description="Enter a floating point number"
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "value")

    def execute(self, context):
        Entry = Global_TocManager.GetEntry(self.object_id, MaterialID)
        if Entry:
            Entry.LoadedData.ShaderVariables[self.variable_index].values[self.value_index] = self.value
            PrettyPrint(f"Set value to: {self.value} at variable: {self.variable_index} value: {self.value_index} for material ID: {self.object_id}")
        else:
            self.report({'ERROR'}, f"Could not find entry for ID: {self.object_id}")
            return {'CANCELLED'}
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)
    
class MaterialShaderVariableColorEntryOperator(Operator):
    bl_label = "Color Picker"
    bl_idname = "helldiver2.material_shader_variable_color"
    bl_description = "Material Shader Variable Color"

    object_id: StringProperty()
    variable_index: bpy.props.IntProperty()
    color: bpy.props.FloatVectorProperty(
                name=f"Color",
                subtype="COLOR",
                size=3,
                min=0.0,
                max=1.0,
                default=(1.0, 1.0, 1.0)
            )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "color")

    def execute(self, context):
        Entry = Global_TocManager.GetEntry(self.object_id, MaterialID)
        if Entry:
            for idx in range(3):
                Entry.LoadedData.ShaderVariables[self.variable_index].values[idx] = self.color[idx]
            PrettyPrint(f"Set color to: {self.color}for material ID: {self.object_id}")
        else:
            self.report({'ERROR'}, f"Could not find entry for ID: {self.object_id}")
            return {'CANCELLED'}
        
        # Redraw
        for area in context.screen.areas:
            if area.type == "VIEW_3D": area.tag_redraw()

        return {'FINISHED'}
    
    def invoke(self, context, event):
        Entry = Global_TocManager.GetEntry(self.object_id, MaterialID)
        if Entry:
            for idx in range(3):
                self.color[idx] = Entry.LoadedData.ShaderVariables[self.variable_index].values[idx]
        else:
            self.report({'ERROR'}, f"Could not find entry for ID: {self.object_id}")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self)

class AddEntryToPatchOperator(Operator):
    bl_label = "Add Entry To Patch"
    bl_idname = "helldiver2.archive_addtopatch"

    object_id: StringProperty()
    object_typeid: StringProperty()
    def execute(self, context):
        Entries = EntriesFromStrings(self.object_id, self.object_typeid)
        for Entry in Entries:
            Global_TocManager.AddEntryToPatch(Entry.FileID, Entry.TypeID)
        return{'FINISHED'}

class RemoveEntryFromPatchOperator(Operator):
    bl_label = "Remove Entry From Patch"
    bl_idname = "helldiver2.archive_removefrompatch"

    object_id: StringProperty()
    object_typeid: StringProperty()
    def execute(self, context):
        Entries = EntriesFromStrings(self.object_id, self.object_typeid)
        for Entry in Entries:
            Global_TocManager.RemoveEntryFromPatch(Entry.FileID, Entry.TypeID)
        return{'FINISHED'}

class UndoArchiveEntryModOperator(Operator):
    bl_label = "Remove Modifications"
    bl_idname = "helldiver2.archive_undo_mod"

    object_id: StringProperty()
    object_typeid: StringProperty()
    def execute(self, context):
        Entries = EntriesFromStrings(self.object_id, self.object_typeid)
        for Entry in Entries:
            if Entry != None:
                Entry.UndoModifiedData()
        return{'FINISHED'}

class DuplicateEntryOperator(Operator):
    bl_label = "Duplicate Entry"
    bl_idname = "helldiver2.archive_duplicate"

    NewFileID : StringProperty(name="NewFileID", default="")
    def draw(self, context):
        layout = self.layout; row = layout.row()
        row.prop(self, "NewFileID", icon='COPY_ID')

    object_id: StringProperty()
    object_typeid: StringProperty()
    def execute(self, context):
        if self.NewFileID == "":
            # 为空则随机生成一个ID，避免重复
            self.NewFileID = str(r.randint(1, 0xffffffffffffffff))
        Global_TocManager.DuplicateEntry(int(self.object_id), int(self.object_typeid), int(self.NewFileID))
        return{'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)

class DuplicateEntriesOperator(Operator):
    bl_label = "Duplicate Entries"
    bl_idname = "helldiver2.archive_duplicate_entries"
    bl_description = "先选择场景中的多个物体，再选择一个条目来批量复制条目，条目名称会从选中的物体中获取材质名"
    
    @classmethod
    def poll(cls, context):
        if context.selected_objects != None:
            for obj in context.selected_objects:
                if obj.type != "MESH":
                    return False
            return True
        else:
            return False
        
    object_id: StringProperty()
    object_typeid: StringProperty()
    def execute(self, context):
        NewFileID_list = []
        selected_objs = context.selected_objects
        if selected_objs:
            for obj in selected_objs:
                if obj.type == "MESH":
                    if "Z_ObjectID" in obj.keys():
                        if len(obj.material_slots) > 0  and obj["Z_ObjectID"] != None:
                            for slot in obj.material_slots.keys():
                                if slot.isnumeric():
                                    NewFileID_list.append(slot)
                                else:
                                    self.report({"ERROR"}, f"物体{obj.name}的材质槽{slot}不是数字")
                                    return {'CANCELLED'}
                            
                        else:
                            self.report({'WARNING'}, f"物体{obj.name}没有材质槽，已跳过")
                            continue
                    else:
                        self.report({'WARNING'}, f"物体{obj.name}没有Z_ObjectID，已跳过")
                        continue
            
            New_set = set(NewFileID_list) # 去重
            for NewFileID in New_set:
                Global_TocManager.DuplicateEntry(int(self.object_id), int(self.object_typeid), int(NewFileID))
            self.report({'INFO'}, f"已复制生成{len(New_set)}个条目")
            
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "没有选中任何物体")
            return {'CANCELLED'}


class RenamePatchEntryOperator(Operator):
    bl_label = "Rename Entry"
    bl_idname = "helldiver2.archive_entryrename"

    NewFileID : StringProperty(name="NewFileID", default="")
    def draw(self, context):
        layout = self.layout; row = layout.row()
        row.prop(self, "NewFileID", icon='COPY_ID')

    object_id: StringProperty()
    object_typeid: StringProperty()
    def execute(self, context):
        Entry = Global_TocManager.GetPatchEntry_B(int(self.object_id), int(self.object_typeid))
        if Entry == None:
            raise Exception("Entry does not exist in patch (cannot rename non patch entries)")
        if Entry != None and self.NewFileID != "":
            Entry.FileID = int(self.NewFileID)
        return{'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)

class DumpArchiveObjectOperator(Operator):
    bl_label = "Dump Archive Object"
    bl_idname = "helldiver2.archive_object_dump_export"

    directory: StringProperty(name="Outdir Path",description="dump output dir")
    filter_folder: BoolProperty(default=True,options={"HIDDEN"})

    object_id: StringProperty(options={"HIDDEN"})
    object_typeid: StringProperty(options={"HIDDEN"})
    def execute(self, context):
        Entries = EntriesFromStrings(self.object_id, self.object_typeid)
        for Entry in Entries:
            if Entry != None:
                data = Entry.GetData()
                FileName = str(Entry.FileID)+"."+GetTypeNameFromID(Entry.TypeID)
                with open(self.directory + FileName, 'w+b') as f:
                    f.write(data[0])
                if data[1] != b"":
                    with open(self.directory + FileName+".gpu", 'w+b') as f:
                        f.write(data[1])
                if data[2] != b"":
                    with open(self.directory + FileName+".stream", 'w+b') as f:
                        f.write(data[2])
        return{'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class ImportDumpOperator(Operator, ImportHelper):
    bl_label = "Import Dump"
    bl_idname = "helldiver2.archive_object_dump_import"

    object_id: StringProperty(options={"HIDDEN"})
    object_typeid: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        if PatchesNotLoaded(self):
            return {'CANCELLED'}

        print(f"object_id:{self.object_id},type_id:{self.object_typeid}")
        Entries = EntriesFromStrings(self.object_id, self.object_typeid)
        for Entry in Entries:
            ImportDump(self, Entry, self.filepath)

        return{'FINISHED'}

def ImportDump(self: Operator, Entry: TocEntry, filepath: str):
    if Entry != None:
        if not Entry.IsLoaded: Entry.Load(False, False)
        path = filepath
        GpuResourchesPath = f"{path}.gpu"
        StreamPath = f"{path}.stream"

        with open(path, 'r+b') as f:
            Entry.TocData = f.read()

        if os.path.isfile(GpuResourchesPath):
            with open(GpuResourchesPath, 'r+b') as f:
                Entry.GpuData = f.read()
        else:
            Entry.GpuData = b""

        if os.path.isfile(StreamPath):
            with open(StreamPath, 'r+b') as f:
                Entry.StreamData = f.read()
        else:
            Entry.StreamData = b""

        Entry.IsModified = True
        if not Global_TocManager.IsInPatch(Entry):
            Global_TocManager.AddEntryToPatch(Entry.FileID, Entry.TypeID)
            
        self.report({'INFO'}, f"Imported Raw Dump: {path}")

#endregion

#region Operators: Meshes

class ImportStingrayMeshOperator(Operator):
    bl_label = "Import Archive Mesh"
    bl_idname = "helldiver2.archive_mesh_import"

    object_id: StringProperty()
    def execute(self, context):
        EntriesIDs = IDsFromString(self.object_id)
        Errors = []
        StaticMeshError = []
        StaticMeshFriendlyName = []
        for EntryID in EntriesIDs:
            if len(EntriesIDs) == 1:
                try:
                    Global_TocManager.Load(EntryID, UnitID)
                except AQ_StaticMeshError as error:
                    self.report({'ERROR'}, "网格全部为静态网格，开启导入静态网格后再导入！")
            else:
                try:
                    Global_TocManager.Load(EntryID, UnitID)
                    
                except AQ_StaticMeshError as error: # 单独捕获静态网格错误，其他错误另外归类
                    StaticMeshError.append(EntryID)
                except Exception as error:
                    Errors.append([EntryID, error])

        # 单独报告静态网格警告，其他错误另外报告
        if len(StaticMeshError) > 0:
            for entry_id in StaticMeshError:
                StaticMeshFriendlyName.append(GetFriendlyNameFromID(entry_id))
            self.report({'WARNING'}, f"导入的多个网格条目中存在全静态网格，ID名称包括： {StaticMeshFriendlyName} 无法导入，请开启导入静态网格后再导入")
        
        if len(Errors) > 0:
            PrettyPrint("\nThese errors occurred while attempting to load meshes...", "error")
            idx = 0
            for error in Errors:
                PrettyPrint(f"  Error {idx}: for mesh {error[0]}", "error")
                PrettyPrint(f"    {error[1]}\n", "error")
                idx += 1
            raise Exception("One or more meshes failed to load")
        return{'FINISHED'}

class SaveStingrayMeshOperator(Operator):
    bl_label  = "Save Mesh"
    bl_idname = "helldiver2.archive_mesh_save"
    bl_options = {'REGISTER', 'UNDO'} 
    bl_description = "保存网格，必须选择一个网格物体"
    
    @classmethod
    def poll(cls, context):
        object = context.active_object
        return object is not None and object.type == "MESH"

    object_id: StringProperty()
    def execute(self, context):
        global Global_BoneNames
        addon_prefs = AQ_PublicClass.get_addon_prefs()
        object = bpy.context.active_object
        # 检查Patch
        has_patch =  Global_TocManager.CheckActivePatch()
        if not has_patch:
            self.report({'ERROR'}, f"没有激活的Patch，无法保存，请先创建一个Patch。")
            return{'CANCELLED'}
        if context.mode != 'OBJECT':
            self.report({'ERROR'}, f"不在物体模式下，当前模式: {context.mode}，保存取消")
            return {'CANCELLED'}
        if addon_prefs.SaveUseAutoSmooth:
            # 4.3 compatibility change
            if bpy.app.version[0] >= 4 and bpy.app.version[1] >= 1:
                bpy.ops.object.shade_auto_smooth(angle=3.14159)
                
            else:
                bpy.ops.object.use_auto_smooth = True
                bpy.context.object.data.auto_smooth_angle = 3.14159
        if object == None:
            self.report({"ERROR"}, "没有物体被选中，必须先选择一个物体再点击保存")
            return {'CANCELLED'}
        
        
        try:
            ID = object["Z_ObjectID"]
        except:
            self.report({'ERROR'}, f"{object.name} 没有HD2自定义属性")
            return{'CANCELLED'}
        # 材质名称检查与修正
        for idx in range(len(object.material_slots)):
            CheckValidMaterial(object,idx)
        
        # SwapID = ""
        SwapID_list = []
        try:
            object["Z_SwapID"]
        except:
            pass
        else:
            if object["Z_SwapID"] != "":
                object["Z_SwapID_0"] = object["Z_SwapID"]
                del object["Z_SwapID"]
        
        SwapID_keys = [key for key in object.keys() if key.startswith("Z_SwapID_")]
        
        if SwapID_keys:
            for key in SwapID_keys:
                if object[key] != "" :
                    if object[key].isnumeric():
                        SwapID_list.append(object[key])
                    else:
                        self.report({"ERROR"}, f"Object: {object.name} 的转换ID: {object[key]} 不是纯数字.")
                        return {'CANCELLED'}
                    
        model = GetObjectsMeshData(Global_TocManager, Global_BoneNames)
        Entry = Global_TocManager.GetEntryByLoadArchive(int(ID), UnitID)
        if Entry is None:
            self.report({'ERROR'},
                f"存档中需要保存的条目没有载入. 找不到物体ID: {ID}")
            return{'CANCELLED'}
        if not Entry.IsLoaded: Entry.Load(True, False)
        meshes = model[ID]
        for mesh_index, mesh in meshes.items():
            try:
                if Entry.LoadedData.RawMeshes[mesh_index].DEV_BoneInfoIndex == -1 and mesh.DEV_BoneInfoIndex > -1:
                    self.report({'ERROR'},
                                f"尝试用有权重网格覆盖静态网格。请检查网格是否正确。")
                    return{'CANCELLED'}
                Entry.LoadedData.RawMeshes[mesh_index] = mesh
            except IndexError:
                excpectedLength = len(Entry.LoadedData.RawMeshes) - 1
                self.report({'ERROR'}, f"MeshInfoIndex of {mesh_index} for {object.name} 超过了网格数量。预期最大 MeshInfoIndex 为: {excpectedLength}。请检查自定义属性是否匹配此值并重新保存网格。")
                return{'CANCELLED'}

            
        
        have_self_id = False
        # 转换ID检查与修正
        if SwapID_list:
            if ID in SwapID_list:
                have_self_id = True
                if SwapID_list.count(ID) > 1:
                    self.report({"ERROR"}, f"Object: {object.name} 的转换ID栏最多只能填一次自身ID.")
                    return {'CANCELLED'}

                SwapID_list.remove(ID)
                #将其放到末尾
                SwapID_list.append(ID)

            wasSaved = Entry.Save()
            
            if not wasSaved:
                self.report({"ERROR"}, f"保存失败 unit {bpy.context.selected_objects[0].name}.")
                return{'CANCELLED'}
            
            for SwapID in SwapID_list:

                if not Global_TocManager.IsInPatch(Entry):
                    Global_TocManager.AddEntryToPatch(int(ID), UnitID)
                        # bpy.ops.helldiver2.archive_addtopatch(object_id = str(ID),object_typeid = str(UnitID))

                Entry_In_patch = Global_TocManager.GetPatchEntry(Entry)
                if SwapID != ID:
                    self.report({'INFO'}, f"转移 Entry ID: {Entry.FileID} to: {SwapID}")
                    Global_TocManager.RemoveEntryFromPatch(int(SwapID), UnitID)
                    Entry_In_patch.FileID = int(SwapID)
                else:
                    self.report({'INFO'}, f"Entry ID: {Entry.FileID} 保持自身")

            if not have_self_id:
                # 快速手动回滚，遗留修改的原因暂未知，目前拿这个应付一下
                Ori_Entry = Global_TocManager.GetEntry(int(ID), UnitID)
                if Ori_Entry and Ori_Entry.IsModified:
                    Ori_Entry.UndoModifiedData()
                # print(f"Ori_Entry IsModified: {Ori_Entry.IsModified}")
                
        
        else:
            # Global_TocManager.Save(int(self.object_id), UnitID)
            wasSaved = Entry.Save()
            if wasSaved:
                if not Global_TocManager.IsInPatch(Entry):
                    Entry = Global_TocManager.AddEntryToPatch(int(ID), UnitID)
            else:
                self.report({"ERROR"}, f"保存失败 unit {bpy.context.selected_objects[0].name}.")
                return{'CANCELLED'}
            self.report({'INFO'}, f"已保存 Unit Object ID: {self.object_id}")

        
        return{'FINISHED'}

class BatchSaveStingrayMeshOperator(Operator):
    bl_label  = "Save Meshes"
    bl_idname = "helldiver2.archive_mesh_batchsave"
    bl_description = "批量保存选中的网格"

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0


    def execute(self, context):
        start = time.time()
        errors = False
        objects = bpy.context.selected_objects
        addon_prefs = AQ_PublicClass.get_addon_prefs()
        
        # 检查Patch
        has_patch =  Global_TocManager.CheckActivePatch()
        if not has_patch:
            self.report({'ERROR'}, f"没有激活的Patch，无法保存，请先创建一个Patch。")
            return{'CANCELLED'}
        if context.mode != 'OBJECT':
            self.report({'ERROR'}, f"不在物体模式下，当前模式: {context.mode}，保存取消")
            return {'CANCELLED'}
        
        mesh_obj = []
        for i in objects:
            if i.type == "MESH":
                mesh_obj.append(i)
        num_initially_selected = len(mesh_obj)
        num_meshes = num_initially_selected
        
        if addon_prefs.SaveUseAutoSmooth:
            for i in objects:
                if i.type == "MESH":
                    # 4.3 compatibility change
                    if bpy.app.version[0] >= 4 and bpy.app.version[1] >= 1:
                        i.data.shade_smooth()
                    else:
                        i.data.use_auto_smooth = True
                        i.data.auto_smooth_angle = 3.14159
        # bpy.ops.object.select_all(action='DESELECT')
        
        IDs = []
        for object in objects:
            try:
                object["Z_SwapID"]
            except:
                pass
            else:
                if object["Z_SwapID"] != "":
                    object["Z_SwapID_0"] = object["Z_SwapID"]
            SwapID_list = []
            SwapID_keys = [key for key in object.keys() if key.startswith("Z_SwapID_")]
            try:
                ID = object["Z_ObjectID"]
                
                # 材质名称检查与修正
                for idx in range(len(object.material_slots)):
                    CheckValidMaterial(object,idx)
                
                if SwapID_keys:
                    for key in SwapID_keys:
                        if object[key] != "" :
                            if object[key].isnumeric():
                                SwapID_list.append(object[key])
                            else:
                                self.report({"ERROR"}, f"Object: {object.name} 的转换ID: {object[key]} 不是纯数字.")
                                return {'CANCELLED'}
                

                IDitem = [ID, SwapID_list]
                if IDitem not in IDs:
                    IDs.append(IDitem)
                    
            except:
                pass


        entries = []
        for IDitem in IDs:
            ID = IDitem[0]
            # SwapID_list = IDitem[1]
            Entry = Global_TocManager.GetEntryByLoadArchive(int(ID), UnitID)
            if Entry is None:
                self.report({'ERROR'}, f"保存的网格对应的档案未加载。无法找到 ID: {ID} 的自定义属性对象。")
                errors = True
                num_meshes -= len(MeshData[ID])
                entries.append(None)
                continue
            if not Entry.IsLoaded: Entry.Load(True, False, True)
            entries.append(Entry)

        MeshData = GetObjectsMeshData(Global_TocManager, Global_BoneNames)    
        for i, IDitem in enumerate(IDs):
            ID = IDitem[0]
            SwapID_list = IDitem[1]
            Entry = entries[i]
            if Entry is None:
                continue
            
            MeshList = MeshData[ID]
            
            have_self_id = False
            if SwapID_list:
                # 预先检查是否有自身ID,有就放到末尾
                if ID in SwapID_list:
                    if SwapID_list.count(ID) > 1:
                        self.report({"ERROR"}, f"Object ID 为 {ID} 的转换ID栏最多只能填一次自身ID.")
                        return {'CANCELLED'}

                    have_self_id = True
                    SwapID_list.remove(ID)
                    #将其放到末尾
                    SwapID_list.append(ID)
                
                for mesh_index, mesh in MeshList.items():
                    try:
                        if Entry.LoadedData.RawMeshes[mesh_index].DEV_BoneInfoIndex == -1 and mesh.DEV_BoneInfoIndex > -1:
                            self.report({'ERROR'},
                                        f"尝试用有权重网格覆盖静态网格，请检查网格是否正确。")
                            return{'CANCELLED'}
                        Entry.LoadedData.RawMeshes[mesh_index] = mesh
                    except IndexError:
                        excpectedLength = len(Entry.LoadedData.RawMeshes) - 1
                        self.report({'ERROR'},f"MeshInfoIndex of {mesh_index} 超过了网格数量。预期最大 MeshInfoIndex 为: {excpectedLength}。请检查自定义属性是否匹配此值并重新保存网格。")
                        errors = True
                        num_meshes -= 1
    
                wasSaved = Entry.Save()
                if not wasSaved:
                    self.report({"ERROR"}, f"Object ID 为 {ID} 的物体保存失败 ")
                    num_meshes -= len(MeshData[ID])
                    # return{'CANCELLED'}
                
                for SwapID in SwapID_list:
                    
                    if not Global_TocManager.IsInPatch(Entry):
                        Global_TocManager.AddEntryToPatch(int(ID), UnitID)
                        print(f"Add Entry ID: {Entry.FileID} to Patch")

                    Entry_In_patch = Global_TocManager.GetPatchEntry_B(int(ID), UnitID)
                    
                    if SwapID != ID:
                        self.report({'INFO'}, f"转移 Entry ID: {Entry_In_patch.FileID} to: {SwapID}")
                        
                        Global_TocManager.RemoveEntryFromPatch(int(SwapID), UnitID)
                        Entry_In_patch.FileID = int(SwapID)
                        # print(f"is in patch: {Global_TocManager.IsInPatch(Entry)}")
                    else:
                        self.report({'INFO'}, f"Entry ID: {Entry.FileID} 保持自身")
                        
                if not have_self_id:
                    # 快速手动回滚，遗留修改的原因暂未知，拿这个应付一下
                    Ori_Entry = Global_TocManager.GetEntryByLoadArchive(int(ID), UnitID)
                    if Ori_Entry and Ori_Entry.IsModified:
                        Ori_Entry.UndoModifiedData()
            
            else:
                for mesh_index, mesh in MeshList.items():
                    try:
                        if Entry.LoadedData.RawMeshes[mesh_index].DEV_BoneInfoIndex == -1 and mesh.DEV_BoneInfoIndex > -1:
                            self.report({'ERROR'},
                                        f"尝试用有权重网格覆盖静态网格，请检查网格是否正确。")
                            return{'CANCELLED'}
                        Entry.LoadedData.RawMeshes[mesh_index] = mesh
                    except IndexError:
                        excpectedLength = len(Entry.LoadedData.RawMeshes) - 1
                        self.report({'ERROR'},f"MeshInfoIndex of {mesh_index} 超过了网格数量。预期最大 MeshInfoIndex 为: {excpectedLength}。请检查自定义属性是否匹配此值并重新保存网格。")
                        errors = True
                        num_meshes -= 1
                        
                wasSaved = Entry.Save()
                if wasSaved:
                    if not Global_TocManager.IsInPatch(Entry):
                        Entry = Global_TocManager.AddEntryToPatch(int(ID), UnitID)
                else:
                    self.report({"ERROR"}, f"Object ID 为 {ID} 的物体保存失败 ")
                    num_meshes -= len(MeshData[ID])
                # for valid_obj in valid_object_list:
                #     valid_obj.select_set(True)
                    
                # Global_TocManager.Save(int(ID), UnitID)
        self.report({'INFO'}, f"成功保存 {num_meshes}/{num_initially_selected} 个网格。")
        if errors:
            self.report({'ERROR'}, f"保存网格时发生错误。请点击这里查看。")
        PrettyPrint(f"保存网格耗时: {time.time()-start}")
                
        return{'FINISHED'}

#endregion

#region Operators: Textures

# save texture from blender to archive button
# TODO: allow the user to choose an image, instead of looking for one of the same name
class SaveTextureFromBlendImageOperator(Operator):
    bl_label = "Save Texture"
    bl_idname = "helldiver2.texture_saveblendimage"

    object_id: StringProperty()
    def execute(self, context):
        Entries = EntriesFromString(self.object_id, TexID)
        for Entry in Entries:
            if Entry != None:
                if not Entry.IsLoaded: Entry.Load()
                try:
                    BlendImageToStingrayTexture(bpy.data.images[str(self.object_id)], Entry.LoadedData)
                except:
                    PrettyPrint("No blend texture was found for saving, using original", "warn"); pass
            Global_TocManager.Save(Entry.FileID, TexID)
        return{'FINISHED'}

# import texture from archive button
class ImportTextureOperator(Operator):
    bl_label = "Import Texture"
    bl_idname = "helldiver2.texture_import"

    object_id: StringProperty()
    def execute(self, context):
        EntriesIDs = IDsFromString(self.object_id)
        for EntryID in EntriesIDs:
            Global_TocManager.Load(int(EntryID), TexID)
        return{'FINISHED'}

# export texture to file
class ExportTextureOperator(Operator, ExportHelper):
    bl_label = "Export Texture"
    bl_idname = "helldiver2.texture_export"
    filename_ext = ".dds"

    object_id: StringProperty(options={"HIDDEN"})
    def execute(self, context):
        Entry = Global_TocManager.GetEntry(int(self.object_id), TexID)
        if Entry != None:
            data = Entry.Load(False, False)
            with open(self.filepath, 'w+b') as f:
                f.write(Entry.LoadedData.ToDDS())
        return{'FINISHED'}
    
    def invoke(self, context, _event):
        if not self.filepath:
            blend_filepath = context.blend_data.filepath
            if not blend_filepath:
                blend_filepath = self.object_id
            else:
                blend_filepath = os.path.splitext(blend_filepath)[0]

            self.filepath = blend_filepath + self.filename_ext

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

# batch export texture to file
class BatchExportTextureOperator(Operator):
    bl_label = "Export Textures"
    bl_idname = "helldiver2.texture_batchexport"
    filename_ext = ".dds"

    directory: StringProperty(name="Outdir Path",description="dds output dir")
    filter_folder: BoolProperty(default=True,options={"HIDDEN"})

    object_id: StringProperty(options={"HIDDEN"})
    def execute(self, context):
        EntriesIDs = IDsFromString(self.object_id)
        for EntryID in EntriesIDs:
            Entry = Global_TocManager.GetEntry(EntryID, TexID)
            if Entry != None:
                data = Entry.Load(False, False)
                with open(self.directory + str(Entry.FileID)+".dds", 'w+b') as f:
                    f.write(Entry.LoadedData.ToDDS())
        return{'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

# import texture from archive button
class SaveTextureFromDDSOperator(Operator, ImportHelper):
    bl_label = "Save Texture"
    bl_idname = "helldiver2.texture_savefromdds"

    object_id: StringProperty(options={"HIDDEN"})
    def execute(self, context):
        Entry = Global_TocManager.GetEntry(int(self.object_id), TexID)
        if Entry != None:
            if len(self.filepath) > 1:
                # get texture data
                Entry.Load()
                StingrayTex = Entry.LoadedData
                with open(self.filepath, 'r+b') as f:
                    StingrayTex.FromDDS(f.read())
                Toc = MemoryStream(IOMode="write")
                Gpu = MemoryStream(IOMode="write")
                Stream = MemoryStream(IOMode="write")
                StingrayTex.Serialize(Toc, Gpu, Stream)
                # add texture to entry
                Entry.SetData(Toc.Data, Gpu.Data, Stream.Data, False)

                Global_TocManager.Save(int(self.object_id), TexID)
        
        # Redraw
        for area in context.screen.areas:
            if area.type == "VIEW_3D": area.tag_redraw()

        return{'FINISHED'}

#endregion

#region Operators: Materials

class SaveMaterialOperator(Operator):
    bl_label = "Save Material"
    bl_idname = "helldiver2.material_save"
    bl_description = "保存材质"

    object_id: StringProperty()
    def execute(self, context):
        # 检查Patch
        has_patch =  Global_TocManager.CheckActivePatch()
        if not has_patch:
            self.report({'ERROR'}, f"没有激活的Patch，无法保存，请先创建一个Patch。")
            return{'CANCELLED'}
        
        EntriesIDs = IDsFromString(self.object_id)
        for EntryID in EntriesIDs:
            Global_TocManager.Save(int(EntryID), MaterialID)
        return{'FINISHED'}

class ImportMaterialOperator(Operator):
    bl_label = "Import Material"
    bl_idname = "helldiver2.material_import"

    object_id: StringProperty()
    def execute(self, context):
        EntriesIDs = IDsFromString(self.object_id)
        for EntryID in EntriesIDs:
            Global_TocManager.Load(int(EntryID), MaterialID)
        return{'FINISHED'}

class AddMaterialOperator(Operator):
    bl_label = "Add Material"
    bl_idname = "helldiver2.material_add"
    bl_description = "添加材质,需要激活一个Patch"
    
    @classmethod
    def poll(cls, context):
        has_patch =  Global_TocManager.CheckActivePatch()
        return has_patch

    global Global_Materials
    selected_material: EnumProperty(items=Global_Materials, name="Template", default=1)

    def execute(self, context):
                # 检查Patch
        has_patch =  Global_TocManager.CheckActivePatch()
        if not has_patch:
            self.report({'ERROR'}, f"没有激活的Patch，无法创建，请先创建一个Patch。")
            return{'CANCELLED'}
        
        Entry = TocEntry()
        Entry.FileID = r.randint(1, 0xffffffffffffffff)
        Entry.TypeID = MaterialID
        Entry.MaterialTemplate = self.selected_material
        Entry.IsCreated = True
        with open(f"{Global_materialpath}/{self.selected_material}.material", 'r+b') as f:
            data = f.read()
        Entry.TocData_OLD   = data
        Entry.TocData       = data

        Global_TocManager.AddNewEntryToPatch(Entry)

        # debug_print_id
        # mat_id = GetEntryParentMaterialID(Entry)
        # self.report({"INFO"}, f"Material {self.selected_material} Parent ID: {mat_id}")
        # Redraw
        for area in context.screen.areas:
            if area.type == "VIEW_3D": area.tag_redraw()
        
        return{'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

class ShowMaterialEditorOperator(Operator):
    bl_label = "Show Material Editor"
    bl_idname = "helldiver2.material_showeditor"

    object_id: StringProperty()
    def execute(self, context):
        Entry = Global_TocManager.GetEntry(int(self.object_id), MaterialID)
        if Entry != None:
            if not Entry.IsLoaded: Entry.Load(False, False)
            mat = Entry.LoadedData
            if mat.DEV_ShowEditor:
                mat.DEV_ShowEditor = False
            else:
                mat.DEV_ShowEditor = True
        return{'FINISHED'}

class SetMaterialTexture(Operator, ImportHelper):
    bl_label = "Set Material Texture"
    bl_idname = "helldiver2.material_settex"


    filename_ext = ".dds"

    filter_glob: StringProperty(default="*.dds;*.tga", options={'HIDDEN'})



    object_id: StringProperty(options={"HIDDEN"})
    tex_idx: IntProperty(options={"HIDDEN"})
    
    def ColorSpaceEnum():
        return [("sRGB", "sRGB", ""), ("Linear", "Linear", "")]

    SaveColorSpace: EnumProperty(name="色彩空间", items=[("Linear", "Linear", ""),("sRGB", "sRGB", "")])
   
    
    
    def draw(self, context):
        addon_prefs = AQ_PublicClass.get_addon_prefs()
        is_tga_on = addon_prefs.tga_Tex_Import_Switch
        is_png_on = addon_prefs.png_Tex_Import_Switch
        if is_tga_on or is_png_on:
            layout = self.layout
            layout.label(text="颜色贴图使用sRGB,法向金属糙度等使用线性",icon="INFO")
            layout.label(text="导入的纹理在保存时将会转换为下列的色彩空间",icon="INFO")
            layout.label(text="纹理色彩空间为：")
            layout.prop(self, "SaveColorSpace")
            
    
    def addprefix(self, path, prefix):
        Ori_filename = os.path.basename(path)
        
        add_prefix_in_path = os.path.join(os.path.dirname(path), f"{prefix}"+ Ori_filename)
        return add_prefix_in_path
    
    def execute(self, context):
        Entry = Global_TocManager.GetEntry(int(self.object_id), MaterialID)
        if Entry != None:
            if Entry.IsLoaded:
                if self.SaveColorSpace == "sRGB" and ".tga" in os.path.basename(self.filepath):
                    
                    Entry.LoadedData.DEV_DDSPaths[self.tex_idx] = self.addprefix(path=self.filepath,prefix="[-_彩色_-]")
                if self.SaveColorSpace == "Linear" and ".tga" in os.path.basename(self.filepath):
                    
                    Entry.LoadedData.DEV_DDSPaths[self.tex_idx] = self.addprefix(path=self.filepath,prefix="[-_线性_-] ")
                else:
                    
                    Entry.LoadedData.DEV_DDSPaths[self.tex_idx] = self.filepath
        # Redraw
        for area in context.screen.areas:
            if area.type == "VIEW_3D": area.tag_redraw()
        
        return{'FINISHED'}
    def invoke(self, context, event):

        addon_prefs = AQ_PublicClass.get_addon_prefs()
        is_tga_on = addon_prefs.tga_Tex_Import_Switch
        is_png_on = addon_prefs.png_Tex_Import_Switch

        if not is_tga_on and not is_png_on:
            self.filter_glob = "*.dds"
        elif is_tga_on and is_png_on:
            self.filter_glob = "*.tga;*.png"
        elif is_tga_on and not is_png_on:
            self.filter_glob = "*.tga"
        elif is_png_on and not is_tga_on:
            self.filter_glob = "*.png"
            
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
            
        
        

#endregion

#region Operators : Animation
class ImportStingrayAnimationOperator(Operator):
    bl_label = "Import Animation"
    bl_idname = "helldiver2.archive_animation_import"
    bl_description = "导入动画到场景中，必须选择一个骨架"
    
    @classmethod
    def poll(cls, context):
        object = context.active_object
        return object is not None and object.type == "ARMATURE"
    
    
    object_id: StringProperty()
    def execute(self, context):
        # check if armature selected
        armature = context.active_object
        if armature.type != "ARMATURE":
            self.report({'ERROR'}, "Please select an armature to import the animation to")
            return {'CANCELLED'}
        animation_id = self.object_id
        try:
            Global_TocManager.Load(int(animation_id), AnimationID)
        except AnimationException as e:
            self.report({'ERROR'}, f"{e}")
            return {'CANCELLED'}
        except Exception as error:
            PrettyPrint(f"Encountered unknown animation error: {error}", 'error')
            self.report({'ERROR'}, f"Encountered an error whilst importing animation. See Console for more info.")
            return {'CANCELLED'}
        return{'FINISHED'}
        
class SaveStingrayAnimationOperator(Operator):
    bl_label  = "Save Animation"
    bl_idname = "helldiver2.archive_animation_save"
    bl_description = "保存动画，必须选择一个骨架"
    
    @classmethod
    def poll(cls, context):
        object = context.active_object
        return object is not None and object.type == "ARMATURE"
    
    
    def execute(self, context):
        if PatchesNotLoaded(self):
            return{'CANCELLED'}
        object = bpy.context.active_object
        if object.animation_data is None or object.animation_data.action is None:
            self.report({'ERROR'}, "Armature has no active action!")
            return {'CANCELLED'}
        if object == None or object.type != "ARMATURE":
            self.report({'ERROR'}, "Please select an armature")
            return {'CANCELLED'}
        action_name = object.animation_data.action.name
        if len(object.animation_data.action.fcurves) == 0:
            self.report({'ERROR'}, f"Action: {action_name} has no keyframe data! Make sure your animation has at least an initial keyframe with a recorded pose.")
            return {'CANCELLED'}
        entry_id = action_name.split(" ")[0].split("_")[0].split(".")[0]
        if entry_id.startswith("0x"):
            entry_id = hex_to_decimal(entry_id)
        try:
            bones_id = object['BonesID']
        except Exception as e:
            PrettyPrint(f"Encountered animation error: {e}", 'error')
            self.report({'ERROR'}, f"Armature: {object.name} is missing HD2 custom property: BonesID")
            return{'CANCELLED'}
        PrettyPrint(f"Getting Animation Entry: {entry_id}")
        animation_entry = Global_TocManager.GetEntryByLoadArchive(int(entry_id), AnimationID)
        if not animation_entry:
            self.report({'ERROR'}, f"Could not find animation entry for Action: {action_name} as EntryID: {entry_id}. Assure your action name starts with a valid ID for the animation entry.")
            return{'CANCELLED'}
        if not animation_entry.IsLoaded: animation_entry.Load(True, False)
        bones_entry = Global_TocManager.GetEntryByLoadArchive(int(bones_id), BoneID)
        bones_data = bones_entry.TocData
        try:
            animation_entry.LoadedData.load_from_armature(context, object, bones_data)
        except AnimationException as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        wasSaved = animation_entry.Save()
        if wasSaved:
            if not Global_TocManager.IsInPatch(animation_entry):
                animation_entry = Global_TocManager.AddEntryToPatch(int(entry_id), AnimationID)
            else:
                Global_TocManager.RemoveEntryFromPatch(int(entry_id), AnimationID)
                animation_entry = Global_TocManager.AddEntryToPatch(int(entry_id), AnimationID)
        else:
            self.report({"ERROR"}, f"Failed to save animation for armature {bpy.context.selected_objects[0].name}.")
            return{'CANCELLED'}
        self.report({'INFO'}, f"Saved Animation")
        return {'FINISHED'}


#region Operators: Clipboard Functionality

class CopyArchiveEntryOperator(Operator):
    bl_label = "Copy Entry"
    bl_idname = "helldiver2.archive_copy"

    object_id: StringProperty()
    object_typeid: StringProperty()
    def execute(self, context):
        Entries = EntriesFromStrings(self.object_id, self.object_typeid)
        Global_TocManager.Copy(Entries)
        return{'FINISHED'}

class PasteArchiveEntryOperator(Operator):
    bl_label = "Paste Entry"
    bl_idname = "helldiver2.archive_paste"

    def execute(self, context):
        Global_TocManager.Paste()
        return{'FINISHED'}

class ClearClipboardOperator(Operator):
    bl_label = "Clear Clipboard"
    bl_idname = "helldiver2.archive_clearclipboard"

    def execute(self, context):
        Global_TocManager.ClearClipboard()
        return{'FINISHED'}

class CopyTextOperator(Operator):
    bl_label  = "Copy ID"
    bl_idname = "helldiver2.copytest"

    text: StringProperty()
    def execute(self, context):
        cmd='echo '+str(self.text).strip()+'|clip'
        subprocess.check_call(cmd, shell=True)
        return{'FINISHED'}

#endregion

#region Operators: UI/UX

class LoadArchivesOperator(Operator):
    bl_label = "Load Archives"
    bl_idname = "helldiver2.archives_import"

    paths_str: StringProperty(name="paths_str")
    def execute(self, context):
        global Global_TocManager
        # paths = self.paths_str.split(',')
        # for path in paths:
        if self.paths_str != "" and (os.path.exists(self.paths_str) or is_slim_version()):
            Global_TocManager.LoadArchive(self.paths_str)
            id = self.paths_str.replace(Global_gamepath, "")
            name = f"{GetArchiveNameFromID(id)} {id}"
            self.report({'INFO'}, f"载入 {name}")
        # self.paths = []
            return{'FINISHED'}
        else:
            message = "Archive 载入失败"
            if not os.path.exists(self.paths_str):
                message = "当前文件路径无效，请在设置中更改"
            self.report({'ERROR'}, message )
            return{'CANCELLED'}
        
    
class ManuallyLoadArchivesOperator(Operator):
    bl_label = "Load Archive By ID"
    bl_idname = "helldiver2.archives_import_manual"
    bl_description = "通过Archive ID手动加载Archive"

    archive_id: StringProperty(name="Archive ID")
    

    
    def execute(self, context):
        global Global_TocManager

        if self.archive_id == "":
            return{'CANCELLED'}


        ID = self.archive_id
        if ID.startswith("0x"):
            ID = hex_to_decimal(self.archive_id)

        path = os.path.join(Global_gamepath, ID)

        if path != "" and (os.path.exists(path) or is_slim_version()):
            Global_TocManager.LoadArchive(path)
            name = f"{GetArchiveNameFromID(ID)} {ID}"
            self.report({'INFO'}, f"载入 {name}")
            return{'FINISHED'}
        else:
            message = "Archive Failed to Load"
            if not os.path.exists(self.paths_str):
                message = "Current Filepath is Invalid. Change This in Settings"
            self.report({'ERROR'}, message )
            return{'CANCELLED'}
    
    def invoke(self, context, event):
        self.archive_id = ""
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "archive_id")


class DecompressSlimPackage(Operator):
    bl_label = "Decompress Slim Package"
    bl_idname = "helldiver2.decompress_slim_archive"
    bl_description = "通过Archive ID从精简版中解压导出Archive"

    archive_id: StringProperty(name="Archive ID")
    
    def execute(self, context):
        global Global_TocManager
        addon_prefs = AQ_PublicClass.get_addon_prefs()

        if self.archive_id == "":
            return{'CANCELLED'}

        ID = self.archive_id
        if ID.startswith("0x"):
            ID = hex_to_decimal(self.archive_id)

        path = os.path.join(Global_gamepath, ID)

        package_name = ID

        output_folder = addon_prefs.adv_decompress_path
        if not output_folder:
            tempdir = tempfile.gettempdir()
            output_folder = tempdir
        
        if path != "" and (os.path.exists(path) or is_slim_version()):
            # slim_init(game_data_folder)
            content = reconstruct_package_from_bundles(package_name)
            if content:
                with open(os.path.join(output_folder, package_name), 'wb') as f:
                    f.write(content)

            content = reconstruct_package_from_bundles(f"{package_name}.gpu_resources")
            if content:
                with open(os.path.join(output_folder, f"{package_name}.gpu_resources"), 'wb') as f:
                    f.write(content)

            content = reconstruct_package_from_bundles(f"{package_name}.stream")
            if content:
                with open(os.path.join(output_folder, f"{package_name}.stream"), 'wb') as f:
                    f.write(content)
                    
            name = f"{GetArchiveNameFromID(ID)} {ID}"
            self.report({'INFO'}, f"解压导出 {name} 到 {output_folder}")
            
            
            return{'FINISHED'}
        
        
        else:
            message = "Archive Failed to Decompress"
            if not os.path.exists(self.paths_str):
                message = "Current Filepath is Invalid. Change This in Settings"
            self.report({'ERROR'}, message )
            return{'CANCELLED'}
    
    
    
    def invoke(self, context, event):
        self.archive_id = ""
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "archive_id")

class ExportArchiveListOperator(Operator):
    bl_idname = "helldiver2.export_archive_list"
    bl_label = "Export Archive List"
    bl_description = "从精简版游戏数据中导出完整的Archive列表到文本文件"
    
    @classmethod
    def poll(cls, context):
        addon_prefs = AQ_PublicClass.get_addon_prefs()
        return is_slim_version() and addon_prefs.adv_full_package_list_export_path
    
    def execute(self, context):
        addon_prefs = AQ_PublicClass.get_addon_prefs()
        
        output_folder = addon_prefs.adv_full_package_list_export_path
        
        file_name = "full_archive_list.txt"
        
        if os.path.exists(os.path.join(output_folder, file_name)):
            # 存在则删除原文件
            os.remove(os.path.join(output_folder, file_name))
        
        with open(os.path.join(output_folder, file_name), 'w', encoding='utf-8') as log_file:
            for key in get_full_package_list():
                log_file.write(f"{key}\n")

        self.report({'INFO'}, f"完整Archive列表已导出到 {os.path.join(output_folder, file_name)}")

        return{'FINISHED'}

class SearchArchivesOperator(Operator):
    bl_label = "搜索已知Archive"
    bl_idname = "helldiver2.search_archives"
    bl_description = "搜索已知Archive "

    SearchField : StringProperty(name="SearchField", default="")
    #======不应该显示的分类资产=========
    hide_classify = [
        "大概环境物体",
        "未知",
        "无网格",
        "已无法搜索",
        "导入失败",
        "None",
        "没有导入确认",
        "音频",
        "视频",
    ]
    
    
    def draw(self, context):
        global Global_updatearchivelistCN_list
        layout = self.layout
        row = layout.row()
        row.scale_y = 1.2
        row.prop(self, "SearchField", icon='VIEWZOOM',text="搜索Archive")
        # Update displayed archives
        if self.PrevSearch != self.SearchField:
            self.PrevSearch = self.SearchField

            self.ArchivesToDisplay = []
            for Entry in Global_updatearchivelistCN_list:
                if Entry["Description"].lower().find(self.SearchField.lower()) != -1 or self.SearchField.lower() in Entry["Classify"]:
                    if not set(Entry["Classify"]).issubset(set(self.hide_classify)) and self.hide_classify[3] not in Entry["Classify"] and Entry["Classify"] and Entry["Description"].find("None") == -1:

                        self.ArchivesToDisplay.append([Entry["Classify"], Entry["Description"], Entry["ArchiveID"]])
    
        if self.SearchField != "" and len(self.ArchivesToDisplay) == 0:
            row = layout.row(); row.label(text="没有找到匹配的Archive")
            row = layout.row(); row.label(text="收集表中有的条目却不在这里？")
            row = layout.row(); row.label(text="尝试手动更新本地Archive列表")
            row = layout.row(); row.label(text="或打开Archive中文收集表链接：")
            row = layout.row(); row.operator("helldiver2.archive_spreadsheet", icon= 'URL')

        else:
            for Archive in self.ArchivesToDisplay:
                box = layout.box()
                row = box.row()
                row.label(text=str(Archive[0]).strip("[").strip("]")+": "+ Archive[1], icon='GROUP')
                row.scale_x = 1.5
                row.operator("helldiver2.archives_import", icon= 'FILE_NEW', text="").paths_str = os.path.join(Global_gamepath, Archive[2])

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        self.PrevSearch = "NONE"
        self.ArchivesToDisplay = []

        wm = context.window_manager
        return wm.invoke_props_dialog(self,width=400)

class SelectAllOfTypeOperator(Operator):
    bl_label  = "Select All Of Type"
    bl_idname = "helldiver2.select_type"

    object_typeid: StringProperty()
    def execute(self, context):
        Entries = GetDisplayData()[0]
        for EntryInfo in Entries:
            Entry = EntryInfo[0]
            if Entry.TypeID == int(self.object_typeid):
                DisplayEntry = Global_TocManager.GetEntry(Entry.FileID, Entry.TypeID)
                if DisplayEntry.IsSelected:
                    #Global_TocManager.DeselectEntries([Entry])
                    pass
                else:
                    Global_TocManager.SelectEntries([Entry], True)
        return{'FINISHED'}

class SetEntryFriendlyNameOperator(Operator):
    bl_label = "Set Friendly Name"
    bl_idname = "helldiver2.archive_setfriendlyname"

    NewFriendlyName : StringProperty(name="NewFriendlyName", default="")
    def draw(self, context):
        layout = self.layout; row = layout.row()
        row.prop(self, "NewFriendlyName", icon='COPY_ID')
        row = layout.row()
        if murmur64_hash(str(self.NewFriendlyName).encode()) == int(self.object_id):
            row.label(text="Hash is correct")
        else:
            row.label(text="Hash is incorrect")
        row.label(text=str(murmur64_hash(str(self.NewFriendlyName).encode())))

    object_id: StringProperty()
    def execute(self, context):
        AddFriendlyName(int(self.object_id), str(self.NewFriendlyName))
        return{'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)

#endregion

#region Operators: Help

class HelpOperator(Operator):
    bl_label  = "Help"
    bl_idname = "helldiver2.help"

    def execute(self, context):
        url = "https://docs.google.com/document/d/1SF7iEekmxoDdf0EsJu1ww9u2Cr8vzHyn2ycZS7JlWl0"
        webbrowser.open(url, new=0, autoraise=True)
        return{'FINISHED'}
    
class ButtonAQGitHub(bpy.types.Operator):
    bl_idname = "helldiver2.aq_githubweb"
    bl_label = "GitHub"
    bl_description = "打开AQ魔改版插件的 GitHub页面"

    def execute(self, context):
        webbrowser.open("https://github.com/Estecsky/io_scene_helldivers2_AQ")
        return {"FINISHED"}

class ArchiveSpreadsheetOperator(Operator):
    bl_label  = "Archive CN Spreadsheet"
    bl_idname = "helldiver2.archive_spreadsheet"
    bl_description = "打开绝地潜兵2中文Archive 收集表"

    def execute(self, context):
        url = "https://www.kdocs.cn/l/csRnAs7QlZvQ"
        webbrowser.open(url, new=0, autoraise=True)
        return{'FINISHED'}

#endregion

#region Operators: Context Menu

def CopyToClipboard(txt):
    cmd='echo '+txt.strip()+'|clip'
    return subprocess.check_call(cmd, shell=True)

class CopyArchiveIDOperator(Operator):
    bl_label = "Copy Archive ID"
    bl_idname = "helldiver2.copy_archive_id"
    bl_description = "将活跃的 Archive ID 复制到剪贴板"

    def execute(self, context):
        if ArchivesNotLoaded(self):
            return {'CANCELLED'}
        archiveID = str(Global_TocManager.ActiveArchive.Name)
        CopyToClipboard(archiveID)
        self.report({'INFO'}, f"复制 Archive ID: {archiveID}")

        return {'FINISHED'}

class EntrySectionOperator(Operator):
    bl_label = "Collapse Section"
    bl_idname = "helldiver2.collapse_section"
    bl_description = "折叠该区域"

    type: StringProperty(default = "")

    def execute(self, context):
        global Global_Foldouts
        for i in range(len(Global_Foldouts)):
            if Global_Foldouts[i][0] == str(self.type):
                Global_Foldouts[i][1] = not Global_Foldouts[i][1]
                # PrettyPrint(f"Folding foldout: {Global_Foldouts[i]}")
        return {'FINISHED'}

stored_custom_properties = {}
class CopyCustomPropertyOperator(Operator):
    bl_label = "Copy HD2 Properties"
    bl_idname = "helldiver2.copy_custom_properties"
    bl_description = "复制绝地潜兵2网格的自定义属性"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        global stored_custom_properties
        
        selectedObjects = context.selected_objects
        if len(selectedObjects) == 0:
            self.report({'WARNING'}, "没有选中的网格")
            return {'CANCELLED'}
        PrettyPrint(selectedObjects)

        obj = context.active_object
        stored_custom_properties.clear()
        for key, value in obj.items():
            if key not in obj.bl_rna.properties:  # Skip built-in properties
                stored_custom_properties[key] = value

        self.report({'INFO'}, f"复制了 {len(stored_custom_properties)} 个自定义属性")
        return {'FINISHED'}

class PasteCustomPropertyOperator(Operator):
    bl_label = "Paste HD2 Properties"
    bl_idname = "helldiver2.paste_custom_properties"
    bl_description = "粘贴绝地潜兵2网格的自定义属性"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        global stored_custom_properties

        selectedObjects = context.selected_objects
        if len(selectedObjects) == 0:
            self.report({'WARNING'}, "没有选中的网格")
            return {'CANCELLED'}

        obj = context.active_object
        if not stored_custom_properties:
            self.report({'WARNING'}, "没有可粘贴的自定义属性")
            return {'CANCELLED'}

        for key, value in stored_custom_properties.items():
            obj[key] = value

        for area in bpy.context.screen.areas:
            area.tag_redraw()

        self.report({'INFO'}, f"粘贴了 {len(stored_custom_properties)} 个自定义属性")
        return {'FINISHED'}
    
class CopyDecimalIDOperator(Operator):
    bl_label = "Copy ID"
    bl_idname = "helldiver2.copy_decimal_id"
    bl_description = "复制所选网格的物体ID"

    def execute(self, context):
        object = context.active_object
        if not object:
            self.report({"ERROR"}, "没有选中的网格")
        try:
            ID = str(object["Z_ObjectID"])
        except:
            self.report({'ERROR'}, f"网格: {object.name} 没有 Helldivers 属性 ID")
            return {'CANCELLED'}
        
        CopyToClipboard(ID)
        self.report({'INFO'}, f"复制了 {object.name} 的物体 {ID}")
        return {'FINISHED'}
    
    
class ButtonAQSDKGitHub(bpy.types.Operator):
    bl_idname = "aqsdk_web.githubweb"
    bl_label = "aqsdk_GitHub"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        webbrowser.open("https://github.com/Estecsky/io_scene_helldivers2_AQ")
        return {"FINISHED"}


class ButtonAQSDKBilibili(bpy.types.Operator):
    bl_idname = "aqsdk_web.bilibiliweb"
    bl_label = "aqsdk_Bilibili"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        webbrowser.open("https://space.bilibili.com/3493298962434150")
        return {"FINISHED"}
    
def CustomPropertyContext(self, context):
    layout = self.layout
    layout.separator()
    layout.label(text=Global_SectionHeader)
    layout.separator()
    # layout.operator("helldiver2.copy_hex_id", icon='COPY_ID')
    layout.operator("helldiver2.copy_decimal_id", icon='COPY_ID')
    layout.separator()
    layout.operator("helldiver2.copy_custom_properties", icon= 'COPYDOWN')
    layout.operator("helldiver2.paste_custom_properties", icon= 'PASTEDOWN')
    layout.separator()
    layout.operator("helldiver2.archive_animation_save", icon='ARMATURE_DATA')
    # if bpy.context.object.type == "ARMATURE":
    #     if bpy.context.object.get("StateMachineID", None) is not None:
    #         layout.operator("helldiver2.search_animations", text="查找该骨架的动画", icon='VIEWZOOM').state_machine_id = bpy.context.object.get("StateMachineID")

    layout.operator("helldiver2.archive_mesh_batchsave", icon= 'FILE_BLEND')
       
# class SearchArmatureAnimationsOperator(Operator):
#     bl_label = "Search Animations"
#     bl_idname = "helldiver2.search_animations"
#     bl_description = "查找该骨架的动画"
    
#     state_machine_id: StringProperty(default="0")
    
#     def execute(self, context):
#         context.scene.Hd2ToolPanelSettings.SearchField = self.state_machine_id
#         global Global_Foldouts
#         # print(Global_Foldouts)
#         for key in Global_Foldouts:
#             key[1] = (key[0] == str(AnimationID))
#         return {"FINISHED"} 
#endregion


#region Menus and Panels
def all_archives_UndoModified():
    if Global_TocManager.LoadedArchives:
        for Archive in Global_TocManager.LoadedArchives:
            for Entry in Archive.TocEntries:
                if Entry != None and Entry.IsModified:
                    Entry.UndoModifiedData()


def update_abs_path(self, context):
    """更新时强制转换为绝对路径并覆盖存储"""
    if self.NewPatchOutPath:
        # 转换为绝对路径
        abs_path = bpy.path.abspath(self.NewPatchOutPath)
        # 覆盖原值
        self["NewPatchOutPath"] = abs_path

def LoadedArchives_callback(scene, context):
    items = [(Archive.Name,Archive.Name , "") for Archive in Global_TocManager.LoadedArchives]
    return items

def Patches_callback(scene, context):
    return [(Archive.Name, Archive.Name, Archive.Name ) for Archive in Global_TocManager.Patches]

class Hd2ToolPanelSettings(PropertyGroup):
    # Patches
    Patches   : EnumProperty(name="Patches", items=Patches_callback)
    PatchOnly : BoolProperty(name="Show Patch Entries Only", description = "仅显示当前补丁中存在的条目", default = False)
    # Archive
    ContentsExpanded : BoolProperty(default = True)
    LoadedArchives   : EnumProperty(name="LoadedArchives", items=LoadedArchives_callback)
    # Settings
    MenuExpanded     : BoolProperty(default = False)
    ShowMeshes       : BoolProperty(name="Meshes", description = "Show Meshes", default = True)
    ShowTextures     : BoolProperty(name="Textures", description = "Show Textures", default = True)
    ShowMaterials    : BoolProperty(name="Materials", description = "Show Materials", default = True)
    # ShowAnimations   : BoolProperty(name="Animations", description = "Show Animations", default = False)
    ShowOthers       : BoolProperty(name="Other", description = "Show All Else", default = False)
    ImportMaterials  : BoolProperty(name="Import Materials", description = "导入材质,会同时导入引用的纹理并打包进Blend文件,否则创建ID占位符空材质。\n默认关闭，因为会增加导入耗时和文件体积，建议在需要研究材质贴图位时开启", default = False)
    ImportLods       : BoolProperty(name="Import LODs", description = "导入LODs", default = False)
    ImportGroup0     : BoolProperty(name="Import Group 0 Only", description = "仅导入第一个顶点组,忽略其他组", default = True)
    ImportCulling    : BoolProperty(name="Import Culling", description = "导入剔除网格", default = False)
    # ImportStatic     : BoolProperty(name="Import Static Meshes", description = "导入静态网格", default = False)
    # MakeCollections  : BoolProperty(name="Make Collections", description = "Make new collection when importing meshes", default = True)
    Force2UVs        : BoolProperty(name="Force 2 UV Sets", description = "强制至少2个UV集,某些材质需要此设置", default = True)
    Force1Group      : BoolProperty(name="Force 1 Group", description = "强制网格仅包含1个顶点组", default = True)
    AutoLods         : BoolProperty(name="Auto LODs", description = "根据LOD0自动生成LOD条目,不会实际减少网格质量", default = True)
    SaveBonePositions: BoolProperty(name="Save Animation Bone Positions", description = "保存动画中的骨骼位置 (可能会与添加动画冲突)", default = True)
    ImportArmature   : BoolProperty(name="Import Armatures", description = "导入网格的Armature", default = True)
    MergeArmatures   : BoolProperty(name="Merge Armatures", description = "将导入的Armature合并到选中的Armature", default = True)
    ParentArmature   : BoolProperty(name="Parent Armatures", description = "将导入的Armature设置为导入的网格的父对象", default = True)
    RemoveGoreMeshes : BoolProperty(name="Remove Gore Meshes", description = "自动删除所有使用断肢材质的顶点", default = True)
    shadervariablesUI : BoolProperty(name="Shader Variables UI", description = "显示着色器变量参数UI", default = True)
    LegacyWeightNames     : BoolProperty(name="Legacy Weight Names", description="使用旧版顶点组权重名称", default = False)
    GenerateRandomTextureIDs : BoolProperty(name="Generate Random Texture IDs", description = "保存材质时为导入的纹理生成随机ID，关闭将不改变ID。\n对于保存多个引用贴图相同只是材质参数不同的材质，可以关闭生成随机ID", default = True)

    # ShadeSmooth      : BoolProperty(name="Shade Smooth", description = "导入模型时平滑着色,开启此项将关闭自动平滑", default = True)
    # Search
    SearchField : StringProperty(default = "")
    
    # add
    IsRenamePatch : BoolProperty(name="RenamePatch",default = False,description = "重命名patch")
    IsChangeOutPath : BoolProperty(name="ChangeOutPath",default = False,description = "修改输出路径")
    NewPatchName : StringProperty(name="NewPatchName",default = "")
    NewPatchOutPath : StringProperty(name="NewOutPath",default = "",subtype='DIR_PATH',update=update_abs_path)
    IsZipPatch : BoolProperty(name="ZipPatch",default = False,description = "压缩patch")

class HellDivers2ToolsPanel(Panel):
    bl_label = f"Helldivers 2 AQ Modified {bl_info['version'][0]}.{bl_info['version'][1]}.{bl_info['version'][2]}"
    bl_idname = "SF_PT_Tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Modding"
    bl_order = 0

    def draw_material_editor(self, Entry, layout, row):
        addon_prefs = AQ_PublicClass.get_addon_prefs()
        scene = bpy.context.scene
        filepath = ""
        if Entry.IsLoaded:
            mat = Entry.LoadedData
            if mat.DEV_ShowEditor:
                for i, t in enumerate(mat.TexIDs):
                    row = layout.row(); row.separator(factor=2.0)
                    ddsPath = mat.DEV_DDSPaths[i]
                    if ddsPath != None: filepath = ddsPath
                    prefix = TextureTypeLookup[Entry.MaterialTemplate][i] if Entry.MaterialTemplate != None else ""
                    label = os.path.basename(filepath) if ddsPath != None else str(t)
                    row.label(text=prefix+label, icon='FILE_IMAGE')
                    props = row.operator("helldiver2.material_settex", icon='FILEBROWSER', text="")
                    props.object_id = str(Entry.FileID)
                    props.tex_idx = i
                #======
                row = layout.row(); row.separator(factor=2.0)
                # row.label(text="材质参数栏",icon='OPTIONS')
                row.prop(scene.Hd2ToolPanelSettings, "shadervariablesUI",
                icon="DOWNARROW_HLT" if scene.Hd2ToolPanelSettings.shadervariablesUI else "RIGHTARROW",
                icon_only=True, emboss=False, text=f"着色器变量参数栏 | 材质模板：{Entry.MaterialTemplate if Entry.MaterialTemplate != None else '无'}")
                if scene.Hd2ToolPanelSettings.shadervariablesUI:
                    row.prop(addon_prefs, "ShowshaderVariables_CN",text="")
                #=====
                if scene.Hd2ToolPanelSettings.shadervariablesUI:
                    for i, variable in enumerate(mat.ShaderVariables):
                        row = layout.row(); row.separator(factor=2.0)
                        split = row.split(factor=0.5)
                        row = split.column()
                        row.alignment = 'RIGHT'
                        name = variable.ID
                        if variable.name != "":
                            global Global_ShaderVariables_CN
                            name = variable.name
                            if addon_prefs.ShowshaderVariables_CN:
                                if Global_ShaderVariables_CN[name]:
                                    name = Global_ShaderVariables_CN[name]
                        row.label(text=f"{variable.klassName}: {name}", icon='OPTIONS')
                        row = split.column()
                        row.alignment = 'LEFT'
                        sections = len(variable.values)
                        if sections == 3: sections = 4 # add an extra for the color picker
                        row = row.split(factor=1/sections)
                        for j, value in enumerate(variable.values):
                            ShaderVariable = row.operator("helldiver2.material_shader_variable", text=str(round(value, 2)))
                            ShaderVariable.value = value
                            ShaderVariable.object_id = str(Entry.FileID)
                            ShaderVariable.variable_index = i
                            ShaderVariable.value_index = j
                        if len(variable.values) == 3:
                            ColorPicker = row.operator("helldiver2.material_shader_variable_color", text="", icon='EYEDROPPER')
                            ColorPicker.object_id = str(Entry.FileID)
                            ColorPicker.variable_index = i

    def draw_entry_buttons(self, box, row, Entry, PatchOnly):
        addon_prefs = AQ_PublicClass.get_addon_prefs()
        if Entry.TypeID == UnitID:
            row.operator("helldiver2.archive_mesh_save", icon='FILE_BLEND', text="").object_id = str(Entry.FileID)
            row.operator("helldiver2.archive_mesh_import", icon='IMPORT', text="").object_id = str(Entry.FileID)
            if Global_TocManager.IsInPatch(Entry) and addon_prefs.DisplayRenameButton:
                props = row.operator("helldiver2.archive_entryrename", icon='TEXT', text="")
                props.object_id     = str(Entry.FileID)
                props.object_typeid = str(Entry.TypeID)
        elif Entry.TypeID == TexID:
            row.operator("helldiver2.texture_saveblendimage", icon='FILE_BLEND', text="").object_id = str(Entry.FileID)
            row.operator("helldiver2.texture_import", icon='IMPORT', text="").object_id = str(Entry.FileID)
        elif Entry.TypeID == MaterialID:
            row.operator("helldiver2.material_save", icon='FILE_BLEND', text="").object_id = str(Entry.FileID)
            row.operator("helldiver2.material_import", icon='IMPORT', text="").object_id = str(Entry.FileID)
            row.operator("helldiver2.material_showeditor", icon='MOD_LINEART', text="").object_id = str(Entry.FileID)
            self.draw_material_editor(Entry, box, row)
        elif Entry.TypeID == AnimationID:
            row.operator("helldiver2.archive_animation_save",icon='FILE_BLEND', text="")
            row.operator("helldiver2.archive_animation_import", icon="IMPORT", text="").object_id = str(Entry.FileID)
            
        if Global_TocManager.IsInPatch(Entry):
            props = row.operator("helldiver2.archive_removefrompatch", icon='FAKE_USER_ON', text="")
            props.object_id     = str(Entry.FileID)
            props.object_typeid = str(Entry.TypeID)
        else:
            props = row.operator("helldiver2.archive_addtopatch", icon='FAKE_USER_OFF', text="")
            props.object_id     = str(Entry.FileID)
            props.object_typeid = str(Entry.TypeID)
        if Entry.IsModified:
            props = row.operator("helldiver2.archive_undo_mod", icon='TRASH', text="")
            props.object_id     = str(Entry.FileID)
            props.object_typeid = str(Entry.TypeID)
        if PatchOnly:
            props = row.operator("helldiver2.archive_removefrompatch", icon='X', text="")
            props.object_id     = str(Entry.FileID)
            props.object_typeid = str(Entry.TypeID)

    def draw(self, context):
        layout = self.layout
        scene = bpy.context.scene
        addon_prefs = AQ_PublicClass.get_addon_prefs()
        # Draw Settings, Documentation and Spreadsheet
        row = layout.row()
        row.prop(scene.Hd2ToolPanelSettings, "MenuExpanded",
            icon="DOWNARROW_HLT" if scene.Hd2ToolPanelSettings.MenuExpanded else "RIGHTARROW",
            icon_only=True, emboss=False, text="Settings")
        row.operator("helldiver2.aq_githubweb", icon='URL', text="")
        row.operator("helldiver2.archive_spreadsheet", icon='INFO', text="")
        if scene.Hd2ToolPanelSettings.MenuExpanded:
            row = layout.row(); row.separator(); row.label(text="显示设置"); box = row.box(); row = box.grid_flow(columns=1)
            row.prop(scene.Hd2ToolPanelSettings, "ShowMeshes",text="显示网格")
            row.prop(scene.Hd2ToolPanelSettings, "ShowTextures",text="显示纹理")
            row.prop(scene.Hd2ToolPanelSettings, "ShowMaterials",text="显示材质")
            row.prop(addon_prefs, "ShowAnimations",text="显示动画")
            row.prop(addon_prefs, "ShowParticles",text="显示粒子")
            row.prop(scene.Hd2ToolPanelSettings, "ShowOthers",text="显示其他")
            row.prop(addon_prefs, "ShowArchivePatchPath",text="实时显示Archive和Patch路径")
            row.prop(addon_prefs,"Layout_search_New",text="显示搜索已知Archive为主的布局")
            row.prop(addon_prefs, "ShowQuickSwitch",text="显示快捷设置按钮")
            row.prop(addon_prefs,"ShowZipPatchButton",text="显示打包Patch为Zip功能")
            row.prop(addon_prefs,"DisplayRenameButton",text="显示重命名按钮")
            row = layout.row(); row.separator(); row.label(text="导入设置"); box = row.box(); row = box.grid_flow(columns=1)
            row.prop(scene.Hd2ToolPanelSettings, "ImportMaterials",text="导入材质")
            row.prop(scene.Hd2ToolPanelSettings, "ImportLods",text="导入Lods")
            row.prop(scene.Hd2ToolPanelSettings, "ImportGroup0",text="只导入Group 0")
            row.prop(addon_prefs,"DisplayFriendlyName_Mesh_Skel", text="在导入网格和骨架时赋予可读名称（如果有）")
            row.prop(addon_prefs, "MakeCollections",text="为每个物体创建集合")
            row.prop(scene.Hd2ToolPanelSettings, "ImportCulling",text="导入剔除网格")
            row.prop(addon_prefs, "ImportStatic",text="导入静态网格（无权重）")
            row.prop(scene.Hd2ToolPanelSettings, "RemoveGoreMeshes",text="删除断肢网格")
            row.prop(addon_prefs, "ShadeSmooth",text="导入模型时平滑着色")
            row.prop(addon_prefs, "tga_Tex_Import_Switch",text="开启TGA纹理导入")
            row.prop(addon_prefs, "png_Tex_Import_Switch",text="开启PNG纹理导入")
            row.prop(scene.Hd2ToolPanelSettings, "ParentArmature",text="父级Armature")
            row.prop(scene.Hd2ToolPanelSettings, "ImportArmature",text="导入Armature")
            row = layout.row(); row.separator(); row.label(text="导出设置"); box = row.box(); row = box.grid_flow(columns=1)
            row.prop(scene.Hd2ToolPanelSettings, "Force2UVs",text="强制使用2个UV通道")
            row.prop(scene.Hd2ToolPanelSettings, "Force1Group",text="强制使用1个权重组")
            row.prop(scene.Hd2ToolPanelSettings, "AutoLods",text="自动Lods")
            row.prop(scene.Hd2ToolPanelSettings, "SaveBonePositions",text="保存动画骨骼位置")
            row.prop(addon_prefs,"SaveUseAutoSmooth",text="保存网格时开启自动平滑")
            row = layout.row(); row.separator(); row.label(text="其他设置"); box = row.box(); row = box.grid_flow(columns=1)
            row.prop(scene.Hd2ToolPanelSettings, "LegacyWeightNames",text="旧权重名称")
            row.prop(scene.Hd2ToolPanelSettings, "GenerateRandomTextureIDs",text="生成随机纹理ID")
            row.prop(scene.Hd2ToolPanelSettings, "MergeArmatures",text="合并Armature")
            row.prop(addon_prefs, "CustomGamePath",text="自定义游戏文件目录")
            row.prop(addon_prefs, "advanced_mode",text="附加功能")
            
            row = layout.row()
            row.separator()
            row.label(text=Global_gamepath)
            row = row.grid_flow(columns=1)
            row.operator("helldiver2.change_filepath", icon='FILEBROWSER',text="更改游戏data目录")
            row = layout.row()
            row.separator()
            row.label(text="缓存目录")
            row = row.grid_flow(columns=1)
            row.operator("helldiver2.open_cache_directory",text="打开缓存目录",icon='FILE_FOLDER')
            row = layout.row(); row.separator(); row.label(text="手动更新本地Archive列表"); box = row.box(); row = box.grid_flow(columns=1)
            row.operator("helldiver2.update_archivelist_cn", text="更新已知资产列表", icon='FILE_REFRESH')
            row = layout.row(); row.separator(); row.label(text="一键添加转移ID属性"); row = row.grid_flow(columns=1)
            row.operator("helldiver2.add_swaps_id_prop", text="添加转移自定义ID属性", icon='ADD')
            if addon_prefs.advanced_mode:
                row = layout.row(); row.alignment = 'CENTER';row.label(text="附加功能")
                row = layout.row(); 
                row.separator(); 
                row.label(text="解压导出Archive"); box = row.box(); row = box.grid_flow(columns=1)
                row.label(text="解压导出路径")
                row.prop(addon_prefs, "adv_decompress_path", text="")
                row.label(text="解压提取单个Archive")
                row.operator("helldiver2.decompress_slim_archive",text="解压导出单个Archive",icon="EXPORT")
                row = layout.row(); row.separator(); row.label(text="Archive列表导出"); box = row.box(); row = box.grid_flow(columns=1)
                row.label(text="Archive列表导出路径")
                row.prop(addon_prefs, "adv_full_package_list_export_path", text="")
                row.label(text="Archive列表导出路径")
                row.operator("helldiver2.export_archive_list",text="导出完整Archive列表",icon="EXPORT")
                
        # Draw Archive Import/Export Buttons
        if addon_prefs.ShowQuickSwitch:
            row = layout.row(); row = layout.row()
            row.alignment = 'CENTER'
            row.label(text="快捷设置")
            row = layout.row()
            row.prop(scene.Hd2ToolPanelSettings, "ImportLods",text="导入Lods")
            row.prop(addon_prefs, "ImportStatic",text="导入静态网格（无权重）")
            row.prop(scene.Hd2ToolPanelSettings, "AutoLods",text="自动Lods")
            
        row = layout.row(); row = layout.row()
        row.operator("helldiver2.archive_import_default", icon= 'SOLO_ON', text="")
        if addon_prefs.Layout_search_New:
            row.operator("helldiver2.search_archives", icon= 'VIEWZOOM', text="搜索已知Archive")
        else:
            row.operator("helldiver2.archive_import", icon= 'IMPORT',text="载入Archive").is_patch = False

        row.operator("helldiver2.archive_unloadall", icon= 'FILE_REFRESH', text="")
        row = layout.row()
        row.prop(scene.Hd2ToolPanelSettings, "LoadedArchives", text="Archives")
        
        row.operator("helldiver2.archives_import_manual", icon= 'VIEWZOOM', text= "")
        if addon_prefs.Layout_search_New:
            row.operator("helldiver2.archive_import", icon= 'FILE_FOLDER',text="").is_patch = False
        else:
            row.operator("helldiver2.search_archives", icon= 'VIEWZOOM', text="")
            
        if len(Global_TocManager.LoadedArchives) > 0:
            Global_TocManager.SetActiveByName(scene.Hd2ToolPanelSettings.LoadedArchives)

        # Draw Patch Stuff
        row = layout.row(); row = layout.row()
        row.operator("helldiver2.archive_createpatch", icon= 'COLLECTION_NEW', text="新建Patch")
        row.operator("helldiver2.archive_export", icon= 'DISC', text="写入Patch")
        if addon_prefs.ShowZipPatchButton:
            row.operator("helldiver2.archive_zippatch_export",icon="EXPORT",text="导出为Zip")
        row.operator("helldiver2.patches_unloadall", icon= 'FILE_REFRESH', text="")
        
        row = layout.row()
        row.prop(scene.Hd2ToolPanelSettings, "Patches", text="Patches")
        if len(Global_TocManager.Patches) > 0:
            Global_TocManager.SetActivePatchByName(scene.Hd2ToolPanelSettings.Patches)
        row.prop(scene.Hd2ToolPanelSettings,"IsRenamePatch",icon = "GREASEPENCIL",text="")
        row.prop(scene.Hd2ToolPanelSettings,"IsChangeOutPath",icon = "FOLDER_REDIRECT",text="")
        row.operator("helldiver2.archive_import", icon= 'IMPORT', text="").is_patch = True
        #-------------add------------
        
        # Get Display Data
        DisplayData = GetDisplayData()
        DisplayTocEntries = DisplayData[0]
        DisplayTocTypes   = DisplayData[1]
        DisplayTocArchivePath = DisplayData[2]
        DisplayTocPatchPath = DisplayData[3]
        
        DisplayTocPatchPath_add = DisplayData[4]
        
        if scene.Hd2ToolPanelSettings.IsRenamePatch:
            row = layout.row()
            row.label(text="重命名已经打开",icon="ERROR")
            row = layout.row()
            row.operator("helldiver2.button_auto_rename_patch",text="自动重命名为基础资产的patch",icon="OUTLINER_DATA_GP_LAYER")
            row = layout.row()
            row.prop(scene.Hd2ToolPanelSettings, "NewPatchName", text="修改为 ")
        if scene.Hd2ToolPanelSettings.IsChangeOutPath:
            # PathSign = "\\" 
            row = layout.row()
            row.label(text="修改Patch写入路径已经打开",icon="ERROR")
            row = layout.row()
            row.prop(scene.Hd2ToolPanelSettings, "NewPatchOutPath", text="修改路径 ")
            
        if DisplayTocPatchPath_add != "": #首先判断是否载入patch
            box = layout.box()
            row = box.row()
            if scene.Hd2ToolPanelSettings.IsChangeOutPath: #判断是否打开自定义输出
                if scene.Hd2ToolPanelSettings.NewPatchOutPath != "": #判断是否有自定义路径输入，如果有
                    if scene.Hd2ToolPanelSettings.NewPatchName != "" and scene.Hd2ToolPanelSettings.IsRenamePatch: #判断是否打开重命名，如果打开
                        row.label(text=f"写入路径预览: {scene.Hd2ToolPanelSettings.NewPatchOutPath + scene.Hd2ToolPanelSettings.NewPatchName}",icon="INFO")
                    else: #没打开或者为空就使用原名称
                        row.label(text=f"写入路径预览: {scene.Hd2ToolPanelSettings.NewPatchOutPath + Global_TocManager.ActivePatch.Name}",icon="INFO")
                elif scene.Hd2ToolPanelSettings.NewPatchOutPath == "": #如果没有
                    if scene.Hd2ToolPanelSettings.NewPatchName != "" and scene.Hd2ToolPanelSettings.IsRenamePatch: #判断是否打开重命名，如果打开
                        row.label(text=f"写入路径预览: {os.path.join(os.path.dirname(DisplayTocPatchPath_add),scene.Hd2ToolPanelSettings.NewPatchName)}",icon="INFO")
                    else: #没自定义路径输入和没有重命名就使用原路径加原名称
                        row.label(text=f"写入路径预览: {DisplayTocPatchPath_add}",icon="INFO")
            else: #没打开自定义输出再判断是否使用重命名
                if scene.Hd2ToolPanelSettings.NewPatchName != "" and scene.Hd2ToolPanelSettings.IsRenamePatch:
                    row.label(text=f"写入路径预览: {os.path.join(os.path.dirname(DisplayTocPatchPath_add),scene.Hd2ToolPanelSettings.NewPatchName)}",icon="INFO")
                else:
                    row.label(text=f"写入路径预览: {DisplayTocPatchPath_add}",icon="INFO")
            if os.path.exists(Global_PatchBasePath) and not scene.Hd2ToolPanelSettings.IsZipPatch:
                box = layout.box()
                box.operator("helldiver2.open_patch_out_directory",text="打开Patch保存文件夹")
        else:
            pass
            # row = layout.row()
            # row.label(text="无Patch载入，无法预览写入路径",icon="ERROR")
        #---------------------------        
        # Draw Archive Contents
        row = layout.row(); row = layout.row()
        title = "无Archive载入"
        if Global_TocManager.ActiveArchive != None:
            ArchiveID = Global_TocManager.ActiveArchive.Name
            name = GetArchiveNameFromID(ArchiveID)
            title = f"{name}    ID: {ArchiveID}"
        if Global_TocManager.ActivePatch != None and scene.Hd2ToolPanelSettings.PatchOnly:
            name = Global_TocManager.ActivePatch.Name
            title = f"Patch: {name}"
        row.prop(scene.Hd2ToolPanelSettings, "ContentsExpanded",
            icon="DOWNARROW_HLT" if scene.Hd2ToolPanelSettings.ContentsExpanded else "RIGHTARROW",
            icon_only=True, emboss=False, text= title)
        row.operator("helldiver2.copy_archive_id", icon='COPY_ID', text="")
        row.prop(scene.Hd2ToolPanelSettings, "PatchOnly", text="")


        # Draw Contents
        NewFriendlyNames = []
        NewFriendlyIDs = []
        if scene.Hd2ToolPanelSettings.ContentsExpanded:
            if len(DisplayTocEntries) == 0: return

            # Draw Toc Archive or Patch Path
            if addon_prefs.ShowArchivePatchPath:
                if scene.Hd2ToolPanelSettings.PatchOnly:
                    row = layout.row()
                    row.label(text=f"Patch路径：{DisplayTocPatchPath}",icon="INFO")
                else:
                    col = layout.column()
                    col.label(text=f"Archive路径：{DisplayTocArchivePath}")
                    if DisplayTocPatchPath != "":
                        col.label(text=f"Patch路径：{DisplayTocPatchPath}",icon="INFO")
                    else:
                        col.label(text="No Patch",icon="INFO")
            # Draw Search Bar
            row = layout.row(); row = layout.row()
            row.prop(scene.Hd2ToolPanelSettings, "SearchField", icon='VIEWZOOM', text="")

            DrawChain = []
            for Type in DisplayTocTypes:
                # check if there is any entry of this type that matches search field
                # TODO: should probably make a better way to do this
                bFound = False
                for EntryInfo in DisplayTocEntries:
                    Entry = EntryInfo[0]
                    if Entry.TypeID == Type.TypeID:
                        if str(Entry.FileID).find(scene.Hd2ToolPanelSettings.SearchField) != -1:
                            bFound = True
                if not bFound: continue

                # Get Type Icon
                type_icon = 'FILE'
                show = None
                global Global_Foldouts
                if Type.TypeID == UnitID:
                    type_icon = 'FILE_3D'
                    if not scene.Hd2ToolPanelSettings.ShowMeshes: continue
                elif Type.TypeID == TexID:
                    type_icon = 'FILE_IMAGE'
                    if not scene.Hd2ToolPanelSettings.ShowTextures: continue
                elif Type.TypeID == MaterialID:
                    type_icon = 'MATERIAL'
                    if not scene.Hd2ToolPanelSettings.ShowMaterials: continue
                elif Type.TypeID == AnimationID:  # 添加显示动画条目
                    type_icon = 'ARMATURE_DATA'
                    if not addon_prefs.ShowAnimations: continue
                elif Type.TypeID == ParticleID:
                    type_icon = 'PARTICLES'
                    if not addon_prefs.ShowParticles: continue
                elif scene.Hd2ToolPanelSettings.ShowOthers: 
                    if Type.TypeID == BoneID: type_icon = 'BONE_DATA'
                    elif Type.TypeID == WwiseBankID:  type_icon = 'OUTLINER_DATA_SPEAKER'
                    elif Type.TypeID == WwiseDepID: type_icon = 'OUTLINER_DATA_SPEAKER'
                    elif Type.TypeID == WwiseStreamID:  type_icon = 'OUTLINER_DATA_SPEAKER'
                    elif Type.TypeID == WwiseMetaDataID: type_icon = 'OUTLINER_DATA_SPEAKER'
                    elif Type.TypeID == StateMachineID: type_icon = 'DRIVER'
                    elif Type.TypeID == StringID: type_icon = 'WORDWRAP_ON'
                    elif Type.TypeID == PhysicsID: type_icon = 'PHYSICS'
                else:
                    continue

                for section in Global_Foldouts:
                    if section[0] == str(Type.TypeID):
                        show = section[1]
                        break
                if show == None:
                    fold = False
                    if Type.TypeID == MaterialID or Type.TypeID == TexID or Type.TypeID == UnitID: fold = True
                    foldout = [str(Type.TypeID), fold]
                    Global_Foldouts.append(foldout)
                    PrettyPrint(f"Adding Foldout ID: {foldout}")
                    
                fold_icon = "DOWNARROW_HLT" if show else "RIGHTARROW"
                # Draw Type Header
                box = layout.box(); row = box.row()
                typeName = GetTypeNameFromID(Type.TypeID)
                split = row.split()
                
                sub = split.row(align=True)
                sub.operator("helldiver2.collapse_section", text=f"{typeName}: {str(Type.TypeID)}", icon=fold_icon, emboss=False).type = str(Type.TypeID)
                # Skip drawling entries if section hidden
                if not show: continue
                
                row.operator("helldiver2.select_type", icon='RESTRICT_SELECT_OFF', text="").object_typeid = str(Type.TypeID)
                # Draw Add Material Button
                if typeName == "material": row.operator("helldiver2.material_add", icon='FILE_NEW', text="")

                # Draw Archive Entries
                col = box.column()
                for EntryInfo in DisplayTocEntries:
                    Entry = EntryInfo[0]
                    PatchOnly = EntryInfo[1]
                    # Exclude entries that should not be drawn
                    if Entry.TypeID != Type.TypeID: continue
                    if str(Entry.FileID).find(scene.Hd2ToolPanelSettings.SearchField) == -1: continue
                    # Deal with friendly names
                    if len(Global_TocManager.SavedFriendlyNameIDs) > len(DrawChain) and Global_TocManager.SavedFriendlyNameIDs[len(DrawChain)] == Entry.FileID:
                        FriendlyName = Global_TocManager.SavedFriendlyNames[len(DrawChain)]
                    else:
                        try:
                            FriendlyName = Global_TocManager.SavedFriendlyNames[Global_TocManager.SavedFriendlyNameIDs.index(Entry.FileID)]
                            NewFriendlyNames.append(FriendlyName)
                            NewFriendlyIDs.append(Entry.FileID)
                        except:
                            FriendlyName = GetFriendlyNameFromID(Entry.FileID)
                            NewFriendlyNames.append(FriendlyName)
                            NewFriendlyIDs.append(Entry.FileID)


                    # Draw Entry
                    PatchEntry = Global_TocManager.GetEntry(int(Entry.FileID), int(Entry.TypeID))
                    PatchEntry.DEV_DrawIndex = len(DrawChain)

                    row = col.row(align=True); row.separator()
                    props = row.operator("helldiver2.archive_entry", icon=type_icon, text=FriendlyName, emboss=PatchEntry.IsSelected, depress=PatchEntry.IsSelected)
                    props.object_id     = str(Entry.FileID)
                    props.object_typeid = str(Entry.TypeID)
                    # Draw Entry Buttons
                    self.draw_entry_buttons(col, row, PatchEntry, PatchOnly)
                    # Update Draw Chain
                    DrawChain.append(PatchEntry)
            Global_TocManager.DrawChain = DrawChain
        Global_TocManager.SavedFriendlyNames = NewFriendlyNames
        Global_TocManager.SavedFriendlyNameIDs = NewFriendlyIDs
       

class AQ_Modified_Credits(bpy.types.Panel):
    bl_order = 99
    bl_label = "作者"
    bl_idname = "AQ_PT_SDK_Credits"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Modding"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="魔改版作者：AQ_Echoo")
        col = box.column()
        col.operator(ButtonAQSDKGitHub.bl_idname, text="GitHub", icon="URL")
        col.operator(ButtonAQSDKBilibili.bl_idname, text="Bilibili", icon="URL")

class WM_MT_button_context(Menu):
    bl_label = "Entry Context Menu"

    def draw_entry_buttons(self, row, Entry):
        if not Entry.IsSelected:
            Global_TocManager.SelectEntries([Entry])

        # Combine entry strings to be passed to operators
        FileIDStr = ""
        TypeIDStr = ""
        for SelectedEntry in Global_TocManager.SelectedEntries:
            FileIDStr += str(SelectedEntry.FileID)+","
            TypeIDStr += str(SelectedEntry.TypeID)+","
        # Get common class
        AreAllMeshes    = True
        AreAllTextures  = True
        AreAllMaterials = True
        AreAllParticles = True
        SingleEntry = True
        NumSelected = len(Global_TocManager.SelectedEntries)
        if len(Global_TocManager.SelectedEntries) > 1:
            SingleEntry = False
        for SelectedEntry in Global_TocManager.SelectedEntries:
            if SelectedEntry.TypeID == UnitID:
                AreAllTextures = False
                AreAllMaterials = False
                AreAllParticles = False
            elif SelectedEntry.TypeID == TexID:
                AreAllMeshes  = False
                AreAllMaterials = False
                AreAllParticles = False
            elif SelectedEntry.TypeID == MaterialID:
                AreAllTextures = False
                AreAllMeshes  = False
                AreAllParticles = False
            elif SelectedEntry.TypeID == ParticleID:
                AreAllTextures = False
                AreAllMeshes  = False
                AreAllMaterials = False
            else:
                AreAllMeshes = False
                AreAllTextures = False
                AreAllMaterials = False
                AreAllParticles = False
        
        RemoveFromPatchName = "Remove From Patch" if SingleEntry else f"Remove {NumSelected} From Patch"
        AddToPatchName = "Add To Patch" if SingleEntry else f"Add {NumSelected} To Patch"
        ImportMeshName = "Import Mesh" if SingleEntry else f"Import {NumSelected} Meshes"
        ImportTextureName = "Import Texture" if SingleEntry else f"Import {NumSelected} Textures"
        ImportMaterialName = "Import Material" if SingleEntry else f"Import {NumSelected} Materials"
        ImportRawDumpName = "Import Raw Dump" if SingleEntry else f"Import {NumSelected} Raw Dumps"
        DumpObjectName = "Dump Object" if SingleEntry else f"Dump {NumSelected} Objects"
        SaveTextureName = "Save Blender Texture" if SingleEntry else f"Save Blender {NumSelected} Textures"
        SaveMaterialName = "Save Material" if SingleEntry else f"Save {NumSelected} Materials"
        UndoName = "Undo Modifications" if SingleEntry else f"Undo {NumSelected} Modifications"
        CopyName = "Copy Entry" if SingleEntry else f"Copy {NumSelected} Entries"
        
        # Draw seperator
        row.separator()
        row.label(text="---------- HellDivers2 ----------")

        # Draw copy button
        row.separator()
        props = row.operator("helldiver2.archive_copy", icon='COPYDOWN', text=CopyName)
        props.object_id     = FileIDStr
        props.object_typeid = TypeIDStr
        if len(Global_TocManager.CopyBuffer) != 0:
            row.operator("helldiver2.archive_paste", icon='PASTEDOWN', text="Paste "+str(len(Global_TocManager.CopyBuffer))+" Entries")
            row.operator("helldiver2.archive_clearclipboard", icon='TRASH', text="Clear Clipboard")
        if SingleEntry:
            props = row.operator("helldiver2.archive_duplicate", icon='DUPLICATE', text="Duplicate Entry")
            props.object_id     = str(Entry.FileID)
            props.object_typeid = str(Entry.TypeID)
            props = row.operator("helldiver2.archive_duplicate_entries", icon='DUPLICATE', text="Duplicate Entries")
            props.object_id     = str(Entry.FileID)
            props.object_typeid = str(Entry.TypeID)
        
        if Global_TocManager.IsInPatch(Entry):
            props = row.operator("helldiver2.archive_removefrompatch", icon='X', text=RemoveFromPatchName)
            props.object_id     = FileIDStr
            props.object_typeid = TypeIDStr
        else:
            props = row.operator("helldiver2.archive_addtopatch", icon='PLUS', text=AddToPatchName)
            props.object_id     = FileIDStr
            props.object_typeid = TypeIDStr

        # Draw import buttons
        # TODO: Add generic import buttons
        row.separator()
        if AreAllMeshes:
            row.operator("helldiver2.archive_mesh_import", icon='IMPORT', text=ImportMeshName).object_id = FileIDStr
        if AreAllTextures:
            row.operator("helldiver2.texture_import", icon='IMPORT', text=ImportTextureName).object_id = FileIDStr
            if SingleEntry:
                row.operator("helldiver2.texture_export", icon='EXPORT', text="Export Texture").object_id = str(Entry.FileID)
            else:
                row.operator("helldiver2.texture_batchexport", icon='EXPORT', text=f"Export {NumSelected} Textures").object_id = FileIDStr
        elif AreAllMaterials:
            row.operator("helldiver2.material_import", icon='IMPORT', text=ImportMaterialName).object_id = FileIDStr
        # Draw export buttons
        row.separator()
        props = row.operator("helldiver2.archive_object_dump_import", icon='PACKAGE', text=ImportRawDumpName)
        props.object_id     = FileIDStr
        props.object_typeid = TypeIDStr
        props = row.operator("helldiver2.archive_object_dump_export", icon='PACKAGE', text=DumpObjectName)
        props.object_id     = FileIDStr
        props.object_typeid = TypeIDStr
        # Draw dump import button
        # if AreAllMaterials and SingleEntry: row.operator("helldiver2.archive_object_dump_import", icon="IMPORT", text="Import Raw Dump").object_id = FileIDStr
        # Draw save buttons
        row.separator()
        if AreAllMeshes:
            if SingleEntry:
                row.operator("helldiver2.archive_mesh_save", icon='FILE_BLEND', text="Save Mesh").object_id = str(Entry.FileID)
            else:
              row.operator("helldiver2.archive_mesh_batchsave", icon='FILE_BLEND', text=f"Save {NumSelected} Meshes")
        elif AreAllTextures:
            row.operator("helldiver2.texture_saveblendimage", icon='FILE_BLEND', text=SaveTextureName).object_id = FileIDStr
            if SingleEntry:
                row.operator("helldiver2.texture_savefromdds", icon='IMAGE_REFERENCE', text="Save Texture From DDS").object_id = str(Entry.FileID)
        elif AreAllMaterials:
            row.operator("helldiver2.material_save", icon='FILE_BLEND', text=SaveMaterialName).object_id = FileIDStr
        # Draw copy ID buttons
        if SingleEntry:
            row.separator()
            row.operator("helldiver2.copytest", icon='COPY_ID', text="Copy Entry ID").text = str(Entry.FileID)
            row.operator("helldiver2.copytest", icon='COPY_ID', text="Copy Type ID").text  = str(Entry.TypeID)
            row.operator("helldiver2.copytest", icon='COPY_ID', text="Copy Friendly Name").text  = GetFriendlyNameFromID(Entry.FileID)
            if Global_TocManager.IsInPatch(Entry):
                props = row.operator("helldiver2.archive_entryrename", icon='TEXT', text="Rename")
                props.object_id     = str(Entry.FileID)
                props.object_typeid = str(Entry.TypeID)
        if Entry.IsModified:
            row.separator()
            props = row.operator("helldiver2.archive_undo_mod", icon='TRASH', text=UndoName)
            props.object_id     = FileIDStr
            props.object_typeid = TypeIDStr

        if SingleEntry:
            row.operator("helldiver2.archive_setfriendlyname", icon='WORDWRAP_ON', text="Set Friendly Name").object_id = str(Entry.FileID)
    
    def draw(self, context):
        value = getattr(context, "button_operator", None)
        if type(value).__name__ == "HELLDIVER2_OT_archive_entry":
            layout = self.layout
            FileID = getattr(value, "object_id")
            TypeID = getattr(value, "object_typeid")
            self.draw_entry_buttons(layout, Global_TocManager.GetEntry(int(FileID), int(TypeID)))

#endregion

classes = (
    LoadArchiveOperator,
    PatchArchiveOperator,
    ImportStingrayMeshOperator,
    SaveStingrayMeshOperator,
    ImportStingrayAnimationOperator,
    SaveStingrayAnimationOperator,
    ImportMaterialOperator,
    ImportTextureOperator,
    ExportTextureOperator,
    DumpArchiveObjectOperator,
    ImportDumpOperator,
    Hd2ToolPanelSettings,
    HellDivers2ToolsPanel,
    UndoArchiveEntryModOperator,
    AddMaterialOperator,
    SaveMaterialOperator,
    SaveTextureFromBlendImageOperator,
    ShowMaterialEditorOperator,
    SetMaterialTexture,
    SearchArchivesOperator,
    LoadArchivesOperator,
    ManuallyLoadArchivesOperator,
    DecompressSlimPackage,
    ExportArchiveListOperator,
    CopyArchiveEntryOperator,
    PasteArchiveEntryOperator,
    ClearClipboardOperator,
    SaveTextureFromDDSOperator,
    HelpOperator,
    ArchiveSpreadsheetOperator,
    UnloadArchivesOperator,
    ArchiveEntryOperator,
    CreatePatchFromActiveOperator,
    AddEntryToPatchOperator,
    RemoveEntryFromPatchOperator,
    CopyTextOperator,
    BatchExportTextureOperator,
    BatchSaveStingrayMeshOperator,
    SelectAllOfTypeOperator,
    RenamePatchEntryOperator,
    DuplicateEntryOperator,
    DuplicateEntriesOperator,
    SetEntryFriendlyNameOperator,
    MaterialShaderVariableEntryOperator,
    MaterialShaderVariableColorEntryOperator,
    ButtonOpenCacheDirectory,
    ButtonAutoRenamePatch,
    ButtonOpenPatchOutDirectory,
    ZipPatchArchiveOperator,
    DefaultLoadArchiveOperator,
    ChangeFilepathOperator,
    ButtonUpdateArchivelistCN,
    CopyArchiveIDOperator,
    EntrySectionOperator,
    ButtonAQGitHub,
    UnloadPatchesOperator,
    AddSwapsID_property,
    CopyCustomPropertyOperator,
    PasteCustomPropertyOperator,
    CopyDecimalIDOperator,
    AQ_Modified_Credits,
    ButtonAQSDKBilibili,
    ButtonAQSDKGitHub,
    # SearchArmatureAnimationsOperator,
)

Global_TocManager = TocManager()

def register():
    LoadTypeHashes()
    LoadNameHashes()
    LoadShaderVariables(Global_variablespath)
    LoadShaderVariables_CN(Global_variablesCNpath)
    LoadUpdateArchiveList_CN()
    InitializeConfig()
    LoadBoneHashes(Global_bonehashpath, Global_BoneNames)
    for cls in classes:
        bpy.utils.register_class(cls)
    Scene.Hd2ToolPanelSettings = PointerProperty(type=Hd2ToolPanelSettings)
    bpy.utils.register_class(WM_MT_button_context)
    addonPreferences.register()
    addon_updater_ops.register(bl_info)
    bpy.types.VIEW3D_MT_object_context_menu.append(CustomPropertyContext)

def unregister():
    bpy.utils.unregister_class(WM_MT_button_context)
    del Scene.Hd2ToolPanelSettings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    addonPreferences.unregister()
    addon_updater_ops.unregister()
    bpy.types.VIEW3D_MT_object_context_menu.remove(CustomPropertyContext)

if __name__=="__main__":
    register()