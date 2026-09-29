from math import ceil, sqrt
import math

import mathutils
import bpy
import bmesh
import json
from copy import deepcopy

from ..utils.memoryStream import MemoryStream, MakeTenBitUnsigned, TenBitUnsigned
from ..utils.logger import PrettyPrint
from .hash import murmur32_hash
from ..utils.constants import *
from ..preferences.access import AQ_PublicClass , AQ_StaticMeshError
from .material import AddMaterialToBlend_EMPTY
from ..hd2_system.skin_weights import normalize_half4
from ..hd2_system.independent_bone_closure import independent_export_bones

Global_MaterialSlotNames = {}

HD2_LIGHT_NAME_HASH_PROP = "HD2SDK_LightNameHash"
HD2_LIGHT_UNIT_ID_PROP = "HD2SDK_LightUnitID"
HD2_LIGHT_VOLUMETRIC_PROP = "Volumetric"
HD2_LIGHT_DIRECT_PROP = "Direct Lighting"
HD2_LIGHT_IMPORTED_UNITS_PROP = "HD2SDK_LightUnitsImported"


def _next_positive_offset(current, *offsets):
    candidates = [offset for offset in offsets if offset and offset > current]
    return min(candidates) if candidates else None


def _read_optional_lod_group_region(stream, lod_offset, following_offset):
    """Split anonymous padding from the optional pointed-to LOD section."""

    current = stream.tell()
    end = current if following_offset is None else following_offset
    if end < current or end > len(stream.Data):
        raise ValueError("LOD Group region end offset is invalid")

    has_lod_section = lod_offset > 0
    if has_lod_section:
        if lod_offset < current or lod_offset > end:
            raise ValueError("LOD Group offset is outside its Unit region")
        prefix = stream.bytes(bytearray(), lod_offset - current)
        data = stream.bytes(bytearray(), end - lod_offset)
    else:
        prefix = stream.bytes(bytearray(), end - current)
        data = bytearray()
    return has_lod_section, prefix, data


def _write_optional_lod_group_region(stream, has_lod_section, prefix, data):
    """Write the region while retaining a zero offset for an absent section."""

    stream.bytes(prefix, len(prefix))
    lod_offset = stream.tell() if has_lod_section else 0
    stream.bytes(data, len(data))
    return lod_offset

class StingrayMeshFile:
    def __init__(self):
        self.HeaderData1        = bytearray(28); self.HeaderData2 = bytearray(8); self.HeaderData3 = bytearray(12); self.UnReversedData1 = bytearray(); self.UnReversedData2 = bytearray()
        self.StreamInfoOffset   = self.EndingOffset = self.MeshInfoOffset = self.NumStreams = self.NumMeshes = self.EndingBytes = self.StreamInfoUnk2 = self.HeaderUnk = self.MaterialsOffset = self.NumMaterials = self.NumBoneInfo = self.BoneInfoOffset = 0
        self.StreamInfoOffsets  = self.StreamInfoUnk = self.StreamInfoArray = self.MeshInfoOffsets = self.MeshInfoUnk = self.MeshInfoArray = []
        self.CustomizationInfoOffset = self.UnkHeaderOffset1 = self.ConnectingBoneHashOffset = self.TransformInfoOffset = self.UnkRef1 = self.BonesRef = self.CompositeRef = 0
        self.BoneInfoOffsets = self.BoneInfoArray = []
        self.RawMeshes = []
        self.SectionsIDs = []
        self.MaterialIDs = []
        self.LightList = LightList()
        self.DEV_MeshInfoMap = [] # Allows removing of meshes while mapping them to the original meshes
        self.CustomizationInfo = CustomizationInfo()
        self.TransformInfo     = TransformInfo()
        self.BoneNames = None
        self.UnreversedCustomizationData = bytearray()
        self.UnreversedConnectingBoneData = bytearray()
        self.UnreversedLODGroupListData = bytearray()
        self.UnreversedWwiseCallbackData = bytearray()
        self.UnreversedPreLightListData = bytearray()
        self.UnreversedPreWwiseCallbackData = bytearray()
        self.UnreversedPreLODGroupListData = bytearray()
        self.UnreversedLODGroupListDataOffset = 0
        self._has_lod_group_list_section = False
        self.UnkHeaderData1 = bytearray()
        self.StateMachineRef = self.UnkRef2 = self.LodGroupOffset = 0
        self.NameHash = 0
        self.LightListOffset = 0
        self.UnkPreLightListOffset = 0
        self.WwiseCallbackOffset = 0
        self.LoadMaterialSlotNames = True
        self.Version = 0

    # -- Serialize Mesh -- #
    def Serialize(self, f: MemoryStream, gpu, Global_TocManager, redo_offsets = False):
        PrettyPrint("Serialize")
        if f.IsWriting() and not redo_offsets:
            # duplicate bone info sections if needed
            temp_boneinfos = [None for n in range(len(self.BoneInfoArray))]
            for Raw_Mesh in self.RawMeshes:
                idx         = Raw_Mesh.MeshInfoIndex
                Mesh_info   = self.MeshInfoArray[self.DEV_MeshInfoMap[idx]]
                if Mesh_info.LodIndex == -1:
                    continue
                RealBoneInfoIdx = Mesh_info.LodIndex
                BoneInfoIdx     = Raw_Mesh.DEV_BoneInfoIndex
                temp_boneinfos[RealBoneInfoIdx] = self.BoneInfoArray[BoneInfoIdx]
            self.BoneInfoArray = temp_boneinfos
            PrettyPrint("Building materials")
            self.SectionsIDs = []
            self.MaterialIDs = []
            Order = 0xffffffff
            for Raw_Mesh in self.RawMeshes:
                if len(Raw_Mesh.Materials) == 0:
                    raise Exception("Mesh has no materials, but at least one is required")
                idx         = Raw_Mesh.MeshInfoIndex
                Mesh_info   = self.MeshInfoArray[self.DEV_MeshInfoMap[idx]]
                Mesh_info.Sections = []
                Mesh_info.NumSections = 0
                Mesh_info.NumMaterials = 0
                for Material in Raw_Mesh.Materials:
                    Section = MeshSectionInfo()
                    Section.ID          = int(Material.ShortID)
                    Section.NumIndices  = Material.NumIndices
                    Section.VertexOffset  = Order # | Used for ordering function
                    Section.IndexOffset   = Order # /

                    # This doesnt do what it was intended to do
                    if Material.DEV_BoneInfoOverride != None:
                        PrettyPrint("Overriding unknown material values")
                        Section.MaterialIndex = Material.DEV_BoneInfoOverride
                        Section.GroupIndex = Material.DEV_BoneInfoOverride
                    else:
                        Section.MaterialIndex = len(Mesh_info.Sections) # | dont know what these actually are, but this is usually correct it seems
                        Section.GroupIndex = len(Mesh_info.Sections) # /

                    Mesh_info.Sections.append(Section)
                    Mesh_info.NumSections += 1
                    Mesh_info.NumMaterials += 1
                    Order -= 1
                    try: # if material ID uses the defualt material string it will throw an error, but thats fine as we dont want to include those ones anyway
                        #if int(Material.MatID) not in self.MaterialIDs:
                        self.MaterialIDs.append(int(Material.MatID))
                        self.SectionsIDs.append(int(Material.ShortID)) # MATERIAL SLOT NAME
                    except:
                        pass

        # serialize file
        self.UnkRef1            = f.uint64(self.UnkRef1)
        self.BonesRef           = f.uint64(self.BonesRef)
        if f.IsWriting():         f.uint64(0)
        else: self.CompositeRef = f.uint64(self.CompositeRef)
        self.UnkRef2            = f.uint64(self.UnkRef2)
        self.StateMachineRef    = f.uint64(self.StateMachineRef)
        self.HeaderData1        = f.uint32(self.HeaderData1)

        if f.IsWriting() and self.Version in [10800437, 10800436, 1]:
            self.Version        = f.uint32(10800438)
        else:
            self.Version        = f.uint32(self.Version)
        print(f"Unit Version: {self.Version}")

        self.UnreversedLODGroupListDataOffset = f.uint32(self.UnreversedLODGroupListDataOffset)
        self.TransformInfoOffset= f.uint32(self.TransformInfoOffset)
        self.LightListOffset = f.uint32(self.LightListOffset)
        self.UnkPreLightListOffset = f.uint32(self.UnkPreLightListOffset)
        self.WwiseCallbackOffset = f.uint32(self.WwiseCallbackOffset)
        self.HeaderData2 = f.bytes(self.HeaderData2, 8)
        self.CustomizationInfoOffset  = f.uint32(self.CustomizationInfoOffset)
        self.UnkHeaderOffset1   = f.uint32(self.UnkHeaderOffset1)
        self.ConnectingBoneHashOffset   = f.uint32(self.ConnectingBoneHashOffset)
        self.BoneInfoOffset     = f.uint32(self.BoneInfoOffset)
        self.StreamInfoOffset   = f.uint32(self.StreamInfoOffset)
        self.EndingOffset       = f.uint32(self.EndingOffset)
        self.MeshInfoOffset     = f.uint32(self.MeshInfoOffset)
        self.HeaderUnk          = f.uint64(self.HeaderUnk)
        self.MaterialsOffset    = f.uint32(self.MaterialsOffset)
        self.HeaderData3 = f.bytes(self.HeaderData3, 12)
        if f.IsReading():
            self._has_lod_group_list_section = (
                self.UnreversedLODGroupListDataOffset > 0
            )

        if f.IsReading() and self.MeshInfoOffset == 0:
            raise Exception("Unsupported Mesh Format (No geometry)")

        if f.IsReading() and (self.StreamInfoOffset == 0 and self.CompositeRef == 0):
            raise Exception("Unsupported Mesh Format (No buffer stream)")

        # Get bones file
        if f.IsReading() and self.BonesRef != 0:
            Entry = Global_TocManager.GetEntry(self.BonesRef, BoneID)
            if Entry != None:
                Global_TocManager.Load(Entry.FileID, Entry.TypeID)
                self.BoneNames = Entry.LoadedData.Names
                self.BoneHashes = Entry.LoadedData.BoneHashes

        # Customization metadata is read for Blender naming but preserved as raw bytes on save.
        if f.IsReading() and self.CustomizationInfoOffset > 0:
            return_offset = f.tell()
            f.seek(self.CustomizationInfoOffset)
            self.CustomizationInfo.Serialize(f)
            f.seek(return_offset)

        # Preserve bytes before the first addressed optional section. Some Unit
        # variants align that section without exposing a separate padding offset.
        if f.IsReading():
            following = _next_positive_offset(
                f.tell(),
                self.WwiseCallbackOffset,
                self.UnkPreLightListOffset,
                self.LightListOffset,
                self.UnreversedLODGroupListDataOffset,
                self.TransformInfoOffset,
                self.CustomizationInfoOffset,
                self.UnkHeaderOffset1,
                self.ConnectingBoneHashOffset,
                self.BoneInfoOffset,
                self.StreamInfoOffset,
                self.MeshInfoOffset,
                self.EndingOffset,
            )
            data_size = following - f.tell() if following is not None else 0
        else:
            data_size = len(self.UnreversedPreWwiseCallbackData)
        self.UnreversedPreWwiseCallbackData = f.bytes(
            self.UnreversedPreWwiseCallbackData, data_size
        )

        # Preserve the opaque audio and pre-light sections before the HD2 light table.
        if self.WwiseCallbackOffset > 0:
            if f.IsReading():
                f.seek(self.WwiseCallbackOffset)
                following = _next_positive_offset(
                    f.tell(),
                    self.UnkPreLightListOffset,
                    self.LightListOffset,
                    self.UnreversedLODGroupListDataOffset,
                    self.TransformInfoOffset,
                    self.CustomizationInfoOffset,
                    self.UnkHeaderOffset1,
                    self.ConnectingBoneHashOffset,
                    self.BoneInfoOffset,
                    self.StreamInfoOffset,
                    self.MeshInfoOffset,
                    self.EndingOffset,
                )
                data_size = following - f.tell() if following is not None else 0
            else:
                self.WwiseCallbackOffset = f.tell()
                data_size = len(self.UnreversedWwiseCallbackData)
            self.UnreversedWwiseCallbackData = f.bytes(
                self.UnreversedWwiseCallbackData, data_size
            )

        if self.UnkPreLightListOffset > 0:
            if f.IsReading():
                f.seek(self.UnkPreLightListOffset)
                following = _next_positive_offset(
                    f.tell(),
                    self.LightListOffset,
                    self.UnreversedLODGroupListDataOffset,
                    self.TransformInfoOffset,
                    self.CustomizationInfoOffset,
                    self.UnkHeaderOffset1,
                    self.ConnectingBoneHashOffset,
                    self.BoneInfoOffset,
                    self.StreamInfoOffset,
                    self.MeshInfoOffset,
                    self.EndingOffset,
                )
                data_size = following - f.tell() if following is not None else 0
            else:
                self.UnkPreLightListOffset = f.tell()
                data_size = len(self.UnreversedPreLightListData)
            self.UnreversedPreLightListData = f.bytes(
                self.UnreversedPreLightListData, data_size
            )

        has_light_section = self.LightListOffset > 0 or bool(self.LightList.lights)
        if f.IsReading():
            if self.LightListOffset > 0:
                light_end = _next_positive_offset(
                    self.LightListOffset,
                    self.UnreversedLODGroupListDataOffset,
                    self.TransformInfoOffset,
                    self.CustomizationInfoOffset,
                    self.UnkHeaderOffset1,
                    self.ConnectingBoneHashOffset,
                    self.BoneInfoOffset,
                    self.StreamInfoOffset,
                    self.MeshInfoOffset,
                    self.EndingOffset,
                )
                if self.LightListOffset + 16 > len(f.Data):
                    raise ValueError("HD2 Light table offset is outside the Unit data")
                f.seek(self.LightListOffset)
                self.LightList.Serialize(f, end_offset=light_end)
        elif has_light_section:
            self.LightListOffset = f.tell()
            self.LightList.Serialize(f)
        else:
            self.LightListOffset = 0

        # Preserve anonymous padding independently from the optional LOD-group
        # section so an originally absent (zero) offset remains zero on write.
        if f.IsReading():
            current = f.tell()
            following = _next_positive_offset(
                current,
                self.TransformInfoOffset,
                self.CustomizationInfoOffset,
                self.UnkHeaderOffset1,
                self.ConnectingBoneHashOffset,
                self.BoneInfoOffset,
                self.StreamInfoOffset,
                self.MeshInfoOffset,
                self.EndingOffset,
            )
            (
                self._has_lod_group_list_section,
                self.UnreversedPreLODGroupListData,
                self.UnreversedLODGroupListData,
            ) = _read_optional_lod_group_region(
                f,
                self.UnreversedLODGroupListDataOffset,
                following,
            )
        else:
            self.UnreversedLODGroupListDataOffset = (
                _write_optional_lod_group_region(
                    f,
                    self._has_lod_group_list_section,
                    self.UnreversedPreLODGroupListData,
                    self.UnreversedLODGroupListData,
                )
            )

        if self.TransformInfoOffset > 0:
            if f.IsReading():
                f.seek(self.TransformInfoOffset)
            else:
                self.TransformInfoOffset = f.tell()
            self.TransformInfo.Serialize(f)
            if f.tell() % 16 != 0:
                f.seek(f.tell() + (16 - f.tell() % 16))

        if self.CustomizationInfoOffset > 0:
            if f.IsReading():
                f.seek(self.CustomizationInfoOffset)
                following = [
                    offset
                    for offset in (
                        self.UnkHeaderOffset1,
                        self.ConnectingBoneHashOffset,
                        self.BoneInfoOffset,
                        self.StreamInfoOffset,
                        self.MeshInfoOffset,
                    )
                    if offset > f.tell()
                ]
                data_size = min(following) - f.tell() if following else 0
            else:
                self.CustomizationInfoOffset = f.tell()
                data_size = len(self.UnreversedCustomizationData)
            self.UnreversedCustomizationData = f.bytes(
                self.UnreversedCustomizationData, data_size
            )

        if self.UnkHeaderOffset1 > 0:
            if f.IsReading():
                f.seek(self.UnkHeaderOffset1)
                following = [
                    offset
                    for offset in (
                        self.ConnectingBoneHashOffset,
                        self.BoneInfoOffset,
                        self.StreamInfoOffset,
                        self.MeshInfoOffset,
                    )
                    if offset > f.tell()
                ]
                data_size = min(following) - f.tell() if following else 0
            else:
                self.UnkHeaderOffset1 = f.tell()
                data_size = len(self.UnkHeaderData1)
            self.UnkHeaderData1 = f.bytes(self.UnkHeaderData1, data_size)

        # ConnectingBoneHash Data
        if self.ConnectingBoneHashOffset > 0:
            if self.BoneInfoOffset > 0:
                UnreversedConnectingBoneDataSize = self.BoneInfoOffset-f.tell()
            elif self.StreamInfoOffset > 0:
                UnreversedConnectingBoneDataSize = self.StreamInfoOffset-f.tell()
            elif self.MeshInfoOffset > 0:
                UnreversedConnectingBoneDataSize = self.MeshInfoOffset-f.tell()
            if f.IsReading():
                f.seek(self.ConnectingBoneHashOffset)
            else:
                self.ConnectingBoneHashOffset = f.tell()
                UnreversedConnectingBoneDataSize = len(self.UnreversedConnectingBoneData)
            self.UnreversedConnectingBoneData = f.bytes(self.UnreversedConnectingBoneData, UnreversedConnectingBoneDataSize)

        # Bone Info
        if f.IsReading(): f.seek(self.BoneInfoOffset)
        else            : self.BoneInfoOffset = f.tell()
        self.NumBoneInfo = f.uint32(len(self.BoneInfoArray))
        if f.IsWriting() and not redo_offsets:
            self.BoneInfoOffsets = [0]*self.NumBoneInfo
        if f.IsReading():
            self.BoneInfoOffsets = [0]*self.NumBoneInfo
            self.BoneInfoArray   = [BoneInfo() for n in range(self.NumBoneInfo)]
        self.BoneInfoOffsets    = [f.uint32(Offset) for Offset in self.BoneInfoOffsets]
        for boneinfo_idx in range(self.NumBoneInfo):
            end_offset = None
            if f.IsReading():
                f.seek(self.BoneInfoOffset + self.BoneInfoOffsets[boneinfo_idx])
                if boneinfo_idx+1 != self.NumBoneInfo:
                    end_offset = self.BoneInfoOffset + self.BoneInfoOffsets[boneinfo_idx+1]
                else:
                    end_offset = self.StreamInfoOffset
                    if self.StreamInfoOffset == 0:
                        end_offset = self.MeshInfoOffset
            else:
                self.BoneInfoOffsets[boneinfo_idx] = f.tell() - self.BoneInfoOffset
            self.BoneInfoArray[boneinfo_idx] = self.BoneInfoArray[boneinfo_idx].Serialize(f, end_offset)
            # Bone Hash linking
            # if f.IsReading():
            #     PrettyPrint("Hashes")
            #     PrettyPrint(f"Length of bone names: {len(self.BoneNames)}")
            #     HashOffset = self.CustomizationInfoOffset - ((len(self.BoneNames) - 1) * 4) # this is a bad work around as we can't always get the bone names since some meshes don't have a bone file listed
            #     PrettyPrint(f"Hash Offset: {HashOffset}")
            #     f.seek(HashOffset)
            #     self.MeshBoneHashes = [0 for n in range(len(self.BoneNames))]
            #     self.MeshBoneHashes = [f.uint32(Hash) for Hash in self.MeshBoneHashes]
            #     PrettyPrint(self.MeshBoneHashes)
            #     for index in self.BoneInfoArray[boneinfo_idx].RealIndices:
            #         BoneInfoHash = self.MeshBoneHashes[index]
            #         for index in range(len(self.BoneHashes)):
            #             if self.BoneHashes[index] == BoneInfoHash:
            #                 BoneName = self.BoneNames[index]
            #                 PrettyPrint(f"Index: {index}")
            #                 PrettyPrint(f"Bone: {BoneName}")
            #                 continue


        # Stream Info
        if self.StreamInfoOffset != 0:
            if f.IsReading(): f.seek(self.StreamInfoOffset)
            else:
                f.seek(ceil(float(f.tell())/16)*16); self.StreamInfoOffset = f.tell()
            self.NumStreams = f.uint32(len(self.StreamInfoArray))
            if f.IsWriting():
                if not redo_offsets: self.StreamInfoOffsets = [0]*self.NumStreams
                self.StreamInfoUnk = [mesh_info.MeshID for mesh_info in self.MeshInfoArray[:self.NumStreams]]
            if f.IsReading():
                self.StreamInfoOffsets = [0]*self.NumStreams
                self.StreamInfoUnk     = [0]*self.NumStreams
                self.StreamInfoArray   = [StreamInfo() for n in range(self.NumStreams)]

            self.StreamInfoOffsets  = [f.uint32(Offset) for Offset in self.StreamInfoOffsets]
            self.StreamInfoUnk      = [f.uint32(Unk) for Unk in self.StreamInfoUnk]
            self.StreamInfoUnk2     = f.uint32(self.StreamInfoUnk2)
            for stream_idx in range(self.NumStreams):
                if f.IsReading(): f.seek(self.StreamInfoOffset + self.StreamInfoOffsets[stream_idx])
                else            : self.StreamInfoOffsets[stream_idx] = f.tell() - self.StreamInfoOffset
                self.StreamInfoArray[stream_idx] = self.StreamInfoArray[stream_idx].Serialize(f, devUnitVersion=self.Version)

        # Mesh Info
        if f.IsReading(): f.seek(self.MeshInfoOffset)
        else            : self.MeshInfoOffset = f.tell()
        self.NumMeshes = f.uint32(len(self.MeshInfoArray))

        if f.IsWriting():
            if not redo_offsets: self.MeshInfoOffsets = [0]*self.NumMeshes
            self.MeshInfoUnk = [mesh_info.MeshID for mesh_info in self.MeshInfoArray]
        if f.IsReading():
            self.MeshInfoOffsets = [0]*self.NumMeshes
            self.MeshInfoUnk     = [0]*self.NumMeshes
            self.MeshInfoArray   = [MeshInfo() for n in range(self.NumMeshes)]
            self.DEV_MeshInfoMap = [n for n in range(len(self.MeshInfoArray))]

        self.MeshInfoOffsets  = [f.uint32(Offset) for Offset in self.MeshInfoOffsets]
        self.MeshInfoUnk      = [f.uint32(Unk) for Unk in self.MeshInfoUnk]
        for mesh_idx in range(self.NumMeshes):
            if f.IsReading(): f.seek(self.MeshInfoOffset+self.MeshInfoOffsets[mesh_idx])
            else            : self.MeshInfoOffsets[mesh_idx] = f.tell() - self.MeshInfoOffset
            self.MeshInfoArray[mesh_idx] = self.MeshInfoArray[mesh_idx].Serialize(f)

        # Get geometry group
        if f.IsReading() and self.CompositeRef != 0:
            Entry = Global_TocManager.GetEntry(self.CompositeRef, CompositeUnitID)
            if Entry != None:
                Global_TocManager.Load(Entry.FileID, Entry.TypeID)
                geometry_group = Entry.LoadedData
                unit_index = geometry_group.UnitHashes.index(int(self.NameHash))
                c_mesh_info = geometry_group.MeshInfos[unit_index]
                self.StreamInfoArray = Entry.LoadedData.StreamInfoArray
                self.NumStreams = len(self.StreamInfoArray)
                for i, mesh_info_item in enumerate(self.MeshInfoArray):
                    mesh_index = c_mesh_info.Meshes.index(mesh_info_item.MeshID)
                    c_mesh_info_item = c_mesh_info.MeshInfoItems[mesh_index]
                    mesh_info_item.StreamIndex      = c_mesh_info_item.MeshLayoutIdx
                    mesh_info_item.NumMaterials     = c_mesh_info_item.NumMaterials
                    mesh_info_item.MaterialOffset   = c_mesh_info_item.MaterialsOffset + 0x50
                    mesh_info_item.Sections         = c_mesh_info_item.Groups
                    mesh_info_item.MaterialIDs      = c_mesh_info_item.Materials
                    mesh_info_item.SectionsOffset   = c_mesh_info_item.GroupsOffset + 0x50
                    mesh_info_item.NumSections      = c_mesh_info_item.NumGroups
                self.StreamInfoOffset = 1
                gpu = Entry.LoadedData.GpuData
            else:
                raise Exception(f"Composite mesh file {self.CompositeRef} could not be found")

        # Materials
        if f.IsReading(): f.seek(self.MaterialsOffset)
        else            : self.MaterialsOffset = f.tell()
        self.NumMaterials = f.uint32(len(self.MaterialIDs))
        if f.IsReading():
            self.SectionsIDs = [0]*self.NumMaterials
            self.MaterialIDs = [0]*self.NumMaterials
        self.SectionsIDs = [f.uint32(ID) for ID in self.SectionsIDs]
        self.MaterialIDs = [f.uint64(ID) for ID in self.MaterialIDs]
        if f.IsReading() and self.LoadMaterialSlotNames:
            global Global_MaterialSlotNames
            id = str(self.NameHash)
            if id not in Global_MaterialSlotNames:
                Global_MaterialSlotNames[id] = {}
            for i in range(self.NumMaterials):
                if self.MaterialIDs[i] not in Global_MaterialSlotNames[id]: # probably going to have to save material slot names per LOD/mesh
                    Global_MaterialSlotNames[id][self.MaterialIDs[i]] = []
                PrettyPrint(f"Saving material slot name {self.SectionsIDs[i]} for material {self.MaterialIDs[i]}")
                if self.SectionsIDs[i] not in Global_MaterialSlotNames[id][self.MaterialIDs[i]]:
                    Global_MaterialSlotNames[id][self.MaterialIDs[i]].append(self.SectionsIDs[i])

        # Unreversed Data
        if f.IsReading(): UnreversedData2Size = self.EndingOffset-f.tell()
        else: UnreversedData2Size = len(self.UnReversedData2)
        self.UnReversedData2    = f.bytes(self.UnReversedData2, UnreversedData2Size)
        if f.IsWriting(): self.EndingOffset = f.tell()
        self.EndingBytes        = f.uint64(self.NumMeshes)
        if redo_offsets:
            return self

        # Serialize Data
        self.SerializeGpuData(gpu, Global_TocManager)

        # TODO: update offsets only instead of re-writing entire file
        if f.IsWriting() and not redo_offsets:
            f.seek(0)
            self.Serialize(f, gpu, Global_TocManager, True)
        return self

    def SerializeGpuData(self, gpu: MemoryStream, Global_TocManager):
        PrettyPrint("SerializeGpuData")
        # Init Raw Meshes If Reading
        if gpu.IsReading():
            self.InitRawMeshes()
        # re-order the meshes to match the vertex order (this is mainly for writing)
        OrderedMeshes = self.CreateOrderedMeshList()
        # Create Vertex Components If Writing
        if gpu.IsWriting():
            self.SetupRawMeshComponents(OrderedMeshes)

        # Serialize Gpu Data
        for stream_idx in range(len(OrderedMeshes)):
            Stream_Info = self.StreamInfoArray[stream_idx]
            if gpu.IsReading():
                self.SerializeIndexBuffer(gpu, Stream_Info, stream_idx, OrderedMeshes, Global_TocManager)
                self.SerializeVertexBuffer(gpu, Stream_Info, stream_idx, OrderedMeshes)
            else:
                self.SerializeVertexBuffer(gpu, Stream_Info, stream_idx, OrderedMeshes)
                self.SerializeIndexBuffer(gpu, Stream_Info, stream_idx, OrderedMeshes, Global_TocManager)

    def SerializeIndexBuffer(self, gpu: MemoryStream, Stream_Info, stream_idx, OrderedMeshes, Global_TocManager):
        # get indices
        IndexOffset  = 0
        CompiledIncorrectly = False
        if gpu.IsWriting():Stream_Info.IndexBufferOffset = gpu.tell()
        for mesh in OrderedMeshes[stream_idx][1]:
            Mesh_Info = self.MeshInfoArray[self.DEV_MeshInfoMap[mesh.MeshInfoIndex]]
            # Lod Info
            if gpu.IsReading():
                mesh.LodIndex = Mesh_Info.LodIndex
                mesh.DEV_BoneInfoIndex = Mesh_Info.LodIndex
            # handle index formats
            IndexStride = 2
            IndexInt = gpu.uint16
            if Stream_Info.IndexBuffer_Type == 1:
                IndexStride = 4
                IndexInt = gpu.uint32

            TotalIndex = 0
            mat_count = {}
            for Section in Mesh_Info.Sections:
                # Create mat info
                if gpu.IsReading():
                    mat = RawMaterialClass()
                    if Section.ID in self.SectionsIDs:
                        mat_idx = self.SectionsIDs.index(Section.ID)
                        mat.MatID = str(self.MaterialIDs[mat_idx])
                        if mat.MatID not in mat_count:
                            mat_count[mat.MatID] = -1
                        mat_count[mat.MatID] += 1
                        mat.IDFromName(str(self.NameHash), str(self.MaterialIDs[mat_idx]), mat_count[mat.MatID])
                        mat.MatID = str(self.MaterialIDs[mat_idx])
                        #mat.ShortID = self.SectionsIDs[mat_idx]
                        if bpy.context.scene.Hd2ToolPanelSettings.ImportMaterials:
                            Global_TocManager.Load(mat.MatID, MaterialID, False, True)
                        else:
                            AddMaterialToBlend_EMPTY(mat.MatID) # 占位符空材质
                    else:
                        try   : bpy.data.materials[mat.MatID]
                        except: bpy.data.materials.new(mat.MatID)
                    mat.StartIndex = TotalIndex*3
                    mat.NumIndices = Section.NumIndices
                    mesh.Materials.append(mat)

                if gpu.IsReading(): gpu.seek(Stream_Info.IndexBufferOffset + (Section.IndexOffset*IndexStride))
                else:
                    Section.IndexOffset = IndexOffset
                    PrettyPrint(f"Updated Section Offset: {Section.IndexOffset}")
                for fidx in range(int(Section.NumIndices/3)):
                    indices = mesh.Indices[TotalIndex]
                    for i in range(3):
                        value = indices[i]
                        if not (0 <= value <= 0xffff) and IndexStride == 2:
                            PrettyPrint(f"Index: {value} TotalIndex: {TotalIndex}indecies out of bounds", "ERROR")
                            CompiledIncorrectly = True
                            value = min(max(0, value), 0xffff)
                        elif not (0 <= value <= 0xffffffff) and IndexStride == 4:
                            PrettyPrint(f"Index: {value} TotalIndex: {TotalIndex} indecies out of bounds", "ERROR")
                            CompiledIncorrectly = True
                            value = min(max(0, value), 0xffffffff)
                        indices[i] = IndexInt(value)
                    mesh.Indices[TotalIndex] = indices
                    TotalIndex += 1
                IndexOffset  += Section.NumIndices
        # update stream info
        if gpu.IsWriting():
            Stream_Info.IndexBufferSize    = gpu.tell() - Stream_Info.IndexBufferOffset
            Stream_Info.NumIndices         = IndexOffset

        # calculate correct vertex num (sometimes its wrong, no clue why, see 9102938b4b2aef9d->7040046837345593857)
        if gpu.IsReading():
            for mesh in OrderedMeshes[stream_idx][0]:
                RealNumVerts = 0
                for face in mesh.Indices:
                    for index in face:
                        if index > RealNumVerts:
                            RealNumVerts = index
                RealNumVerts += 1
                Mesh_Info = self.MeshInfoArray[self.DEV_MeshInfoMap[mesh.MeshInfoIndex]]
                if Mesh_Info.Sections[0].NumVertices != RealNumVerts:
                    for Section in Mesh_Info.Sections:
                        Section.NumVertices = RealNumVerts
                    self.ReInitRawMeshVerts(mesh)

    def SerializeVertexBuffer(self, gpu: MemoryStream, Stream_Info, stream_idx, OrderedMeshes):
        # Vertex Buffer
        VertexOffset = 0
        if gpu.IsWriting(): Stream_Info.VertexBufferOffset = gpu.tell()
        for mesh in OrderedMeshes[stream_idx][0]:
            Mesh_Info = self.MeshInfoArray[self.DEV_MeshInfoMap[mesh.MeshInfoIndex]]
            if gpu.IsWriting():
                for Section in Mesh_Info.Sections:
                    Section.VertexOffset = VertexOffset
                    Section.NumVertices  = len(mesh.VertexPositions)
                    PrettyPrint(f"Updated VertexOffset Offset: {Section.VertexOffset}")
            MainSection = Mesh_Info.Sections[0]
            # get vertices
            if gpu.IsReading(): gpu.seek(Stream_Info.VertexBufferOffset + (MainSection.VertexOffset*Stream_Info.VertexStride))
            if gpu.IsReading() and mesh.IsCullingBody():
                start = gpu.tell()
                mesh.DEV_NativeVertexBytes = gpu.read(len(mesh.VertexPositions) * Stream_Info.VertexStride)
                gpu.seek(start)
                mesh.DEV_NativeComponents = deepcopy(Stream_Info.Components)
                mesh.DEV_NativeStride = Stream_Info.VertexStride
            if gpu.IsWriting() and getattr(mesh, "DEV_PreserveNativeStream", False):
                gpu.write(mesh.DEV_NativeVertexBytes)
                VertexOffset += len(mesh.VertexPositions)
                continue

            for vidx in range(len(mesh.VertexPositions)):
                if gpu.IsReading():
                    pass
                vstart = gpu.tell()

                for Component in Stream_Info.Components:
                    serialize_func = FUNCTION_LUTS.SERIALIZE_MESH_LUT[Component.Type]
                    serialize_func(gpu, mesh, Component, vidx)

                gpu.seek(vstart + Stream_Info.VertexStride)
            if gpu.IsReading() and mesh.IsCullingBody():
                mesh.DEV_NativeVertexSnapshot = deepcopy(mesh.NativeVertexSnapshot())
            VertexOffset += len(mesh.VertexPositions)
        # update stream info
        if gpu.IsWriting():
            gpu.seek(ceil(float(gpu.tell())/16)*16)
            Stream_Info.VertexBufferSize    = gpu.tell() - Stream_Info.VertexBufferOffset
            Stream_Info.NumVertices         = VertexOffset

    def CreateOrderedMeshList(self):
        # re-order the meshes to match the vertex order (this is mainly for writing)
        meshes_ordered_by_vert = [
            sorted(
                [mesh for mesh in self.RawMeshes if self.MeshInfoArray[self.DEV_MeshInfoMap[mesh.MeshInfoIndex]].StreamIndex == index],
                key=lambda mesh: self.MeshInfoArray[self.DEV_MeshInfoMap[mesh.MeshInfoIndex]].Sections[0].VertexOffset
            ) for index in range(len(self.StreamInfoArray))
        ]
        meshes_ordered_by_index = [
            sorted(
                [mesh for mesh in self.RawMeshes if self.MeshInfoArray[self.DEV_MeshInfoMap[mesh.MeshInfoIndex]].StreamIndex == index],
                key=lambda mesh: self.MeshInfoArray[self.DEV_MeshInfoMap[mesh.MeshInfoIndex]].Sections[0].IndexOffset
            ) for index in range(len(self.StreamInfoArray))
        ]
        OrderedMeshes = [list(a) for a in zip(meshes_ordered_by_vert, meshes_ordered_by_index)]

        # set 32 bit face indices if needed
        for stream_idx in range(len(OrderedMeshes)):
            Stream_Info = self.StreamInfoArray[stream_idx]
            for mesh in OrderedMeshes[stream_idx][0]:
                if mesh.DEV_Use32BitIndices:
                    Stream_Info.IndexBuffer_Type = 1
        return OrderedMeshes

    def InitRawMeshes(self):
        for n in range(len(self.MeshInfoArray)):
            NewMesh     = RawMeshClass()
            Mesh_Info   = self.MeshInfoArray[n]

            indexerror = Mesh_Info.StreamIndex >= len(self.StreamInfoArray)
            messageerror = "ERROR" if indexerror else "INFO"
            message = "Stream index out of bounds" if indexerror else ""
            PrettyPrint(f"Num: {len(self.StreamInfoArray)} Index: {Mesh_Info.StreamIndex}    {message}", messageerror)
            if indexerror: continue

            Stream_Info = self.StreamInfoArray[Mesh_Info.StreamIndex]
            NewMesh.MeshInfoIndex = n
            NewMesh.MeshID = Mesh_Info.MeshID
            group_transform = self.TransformInfo.TransformMatrices[Mesh_Info.TransformIndex]
            NewMesh.DEV_Transform = group_transform.ToBlenderMatrix()

            try:
                NewMesh.DEV_BoneInfo  = self.BoneInfoArray[Mesh_Info.LodIndex]
            except: pass
            numUVs          = 0
            numBoneIndices  = 0
            for component in Stream_Info.Components:
                if component.TypeName() == "uv":
                    numUVs += 1
                if component.TypeName() == "bone_index":
                    numBoneIndices += 1
            NewMesh.InitBlank(Mesh_Info.GetNumVertices(), Mesh_Info.GetNumIndices(), numUVs, numBoneIndices)
            self.RawMeshes.append(NewMesh)

    def ReInitRawMeshVerts(self, mesh):
        # for mesh in self.RawMeshes:
        Mesh_Info = self.MeshInfoArray[self.DEV_MeshInfoMap[mesh.MeshInfoIndex]]
        mesh.ReInitVerts(Mesh_Info.GetNumVertices())

    def SetupRawMeshComponents(self, OrderedMeshes):
        for stream_idx in range(len(OrderedMeshes)):
            Stream_Info = self.StreamInfoArray[stream_idx]

            # Culling helpers are not authored render meshes. Preserve their
            # opaque native stream (including scalar 0/1 weights and auxiliary
            # index encodings) only while every decoded attribute is unchanged.
            native_meshes = OrderedMeshes[stream_idx][0]
            for native_mesh in native_meshes:
                native_mesh.DEV_PreserveNativeStream = False
            if native_meshes and all(mesh.CanPreserveNativeStream() for mesh in native_meshes):
                first = native_meshes[0]
                schema = lambda mesh: (mesh.DEV_NativeStride, [
                    (c.Type, c.Format, c.Index, c.Unknown) for c in mesh.DEV_NativeComponents])
                if all(schema(mesh) == schema(first) for mesh in native_meshes):
                    Stream_Info.Components = deepcopy(first.DEV_NativeComponents)
                    Stream_Info.VertexStride = first.DEV_NativeStride
                    for mesh in native_meshes:
                        mesh.DEV_PreserveNativeStream = True
                    continue

            HasPositions = False
            HasNormals   = False
            HasTangents  = False
            HasBiTangents= False
            IsSkinned    = False
            HasColors    = False
            NumUVs       = 0
            NumBoneIndices = 0
            # get total number of components
            for mesh in OrderedMeshes[stream_idx][0]:
                if len(mesh.VertexPositions)  > 0: HasPositions  = True
                if len(mesh.VertexColors)     > 0: HasColors     = True
                if len(mesh.VertexNormals)    > 0: HasNormals    = True
                if len(mesh.VertexTangents)   > 0: HasTangents   = True
                if len(mesh.VertexBiTangents) > 0: HasBiTangents = True
                if len(mesh.VertexBoneIndices)> 0: IsSkinned     = True
                if len(mesh.VertexUVs)   > NumUVs: NumUVs = len(mesh.VertexUVs)
                if len(mesh.VertexBoneIndices) > NumBoneIndices: NumBoneIndices = len(mesh.VertexBoneIndices)
            if bpy.context.scene.Hd2ToolPanelSettings.Force2UVs:
                NumUVs = max(3, NumUVs)
            if IsSkinned and NumBoneIndices > 1 and bpy.context.scene.Hd2ToolPanelSettings.Force1Group:
                NumBoneIndices = 1

            for mesh in OrderedMeshes[stream_idx][0]: # fill default values for meshes which are missing some components
                if not len(mesh.VertexPositions)  > 0:
                    raise Exception("bruh... your mesh doesn't have any vertices")
                if HasNormals and not len(mesh.VertexNormals)    > 0:
                    mesh.VertexNormals = [[0,0,0] for n in mesh.VertexPositions]
                if HasColors and not len(mesh.VertexColors) > 0:
                    mesh.VertexColors = [[0, 0, 0, 0] for n in mesh.VertexColors]
                if HasTangents and not len(mesh.VertexTangents)   > 0:
                    mesh.VertexTangents = [[0,0,0] for n in mesh.VertexPositions]
                if HasBiTangents and not len(mesh.VertexBiTangents) > 0:
                    mesh.VertexBiTangents = [[0,0,0] for n in mesh.VertexPositions]
                if IsSkinned and not len(mesh.VertexWeights) > 0:
                    mesh.VertexWeights      = [[0,0,0,0] for n in mesh.VertexPositions]
                    mesh.VertexBoneIndices  = [[[0,0,0,0] for n in mesh.VertexPositions]*NumBoneIndices]
                if IsSkinned and len(mesh.VertexBoneIndices) > NumBoneIndices:
                    mesh.VertexBoneIndices = mesh.VertexBoneIndices[::NumBoneIndices]
                if NumUVs > len(mesh.VertexUVs):
                    dif = NumUVs - len(mesh.VertexUVs)
                    for n in range(dif):
                        mesh.VertexUVs.append([[0,0] for n in mesh.VertexPositions])
            # make stream components
            Stream_Info.Components = []
            if HasColors:     Stream_Info.Components.append(StreamComponentInfo("color", "rgba_r8g8b8a8", devUnitVersion=self.Version))
            if HasPositions:  Stream_Info.Components.append(StreamComponentInfo("position", "vec3_float", devUnitVersion=self.Version))
            if HasNormals:    Stream_Info.Components.append(StreamComponentInfo("normal", "unk_normal", devUnitVersion=self.Version))
            for n in range(NumUVs):
                UVComponent = StreamComponentInfo("uv", "vec2_float", devUnitVersion=self.Version)
                UVComponent.Index = n
                Stream_Info.Components.append(UVComponent)
            if IsSkinned:     Stream_Info.Components.append(StreamComponentInfo("bone_weight", "vec4_half", devUnitVersion=self.Version))
            for n in range(NumBoneIndices):
                BIComponent = StreamComponentInfo("bone_index", "vec4_uint8", devUnitVersion=self.Version)
                BIComponent.Index = n
                Stream_Info.Components.append(BIComponent)
            # calculate Stride
            Stream_Info.VertexStride = 0
            for Component in Stream_Info.Components:
                Stream_Info.VertexStride += Component.GetSize()


class BoneInfo:
    def __init__(self):
        self.NumBones = self.unk1 = self.RealIndicesOffset = self.FakeIndicesOffset = self.NumFakeIndices = self.FakeIndicesUnk = 0
        self.Bones = self.RealIndices = self.FakeIndices = []
        self.NumRemaps = self.MatrixOffset = 0
        self.Remaps = self.RemapOffsets = self.RemapCounts = []
    def Serialize(self, f: MemoryStream, end=None):
        self.Serialize_REAL(f)
        return self

    def Serialize_REAL(self, f: MemoryStream): # still need to figure out whats up with the unknown bit
        RelPosition = f.tell()

        self.NumBones       = f.uint32(self.NumBones)
        self.MatrixOffset           = f.uint32(self.MatrixOffset) # matrix pointer
        self.RealIndicesOffset = f.uint32(self.RealIndicesOffset) # unit indices
        self.FakeIndicesOffset = f.uint32(self.FakeIndicesOffset) # remap indices
        # get bone data
        if f.IsReading():
            self.Bones = [StingrayMatrix4x4() for n in range(self.NumBones)]
            self.RealIndices = [0 for n in range(self.NumBones)]
            self.FakeIndices = [0 for n in range(self.NumBones)]
        if f.IsReading(): f.seek(RelPosition+self.MatrixOffset)
        else            : self.MatrixOffset = f.tell()-RelPosition
        # save the right bone
        for i, bone in enumerate(self.Bones):
            if i == self.NumBones:
                break
            bone.Serialize(f)
        #self.Bones = [bone.Serialize(f) for bone in self.Bones]
        # get real indices
        if f.IsReading(): f.seek(RelPosition+self.RealIndicesOffset)
        else            : self.RealIndicesOffset = f.tell()-RelPosition
        self.RealIndices = [f.uint32(index) for index in self.RealIndices]

        # get remapped indices
        if f.IsReading(): f.seek(RelPosition+self.FakeIndicesOffset)
        else            : self.FakeIndicesOffset = f.tell()-RelPosition
        if f.IsReading():
            RemapStartPosition = f.tell()
            self.NumRemaps = f.uint32(self.NumRemaps)
            self.RemapOffsets = [0]*self.NumRemaps
            self.RemapCounts = [0]*self.NumRemaps
            for i in range(self.NumRemaps):
                self.RemapOffsets[i] = f.uint32(self.RemapOffsets[i])
                self.RemapCounts[i] = f.uint32(self.RemapCounts[i])
            for i in range(self.NumRemaps):
                f.seek(RemapStartPosition+self.RemapOffsets[i])
                self.Remaps.append([0]*self.RemapCounts[i])
                self.Remaps[i] = [f.uint32(index) for index in self.Remaps[i]]
        else:
            RemapStartPosition = f.tell()
            self.NumRemaps = f.uint32(self.NumRemaps)
            for i in range(self.NumRemaps):
                self.RemapOffsets[i] = f.uint32(self.RemapOffsets[i])
                self.RemapCounts[i] = f.uint32(self.RemapCounts[i])
            for i in range(self.NumRemaps):
                f.seek(RemapStartPosition+self.RemapOffsets[i])
                self.Remaps[i] = [f.uint32(index) for index in self.Remaps[i]]
        return self
    def GetRealIndex(self, bone_index, material_index=0):
        FakeIndex = self.Remaps[material_index][bone_index]
        return self.RealIndices[FakeIndex]

    def GetRemappedIndex(self, bone_index, material_index=0):
        return self.Remaps[material_index].index(self.RealIndices.index(bone_index))

    def SetRemap(self, remap_info: list[list[str]], transform_info):
        # remap_info is a list of bones indexed by material
        # so the list of bones for material slot 0 is covered by remap_info[0]
        #ideally this eventually allows for creating a remap for any arbitrary bone; requires editing the transform_info
        #return
        # I wonder if you can just take the transform component from the previous bone it was on
        # remap index should match the transform_info index!!!!!
        self.NumRemaps = len(remap_info)
        self.RemapCounts = [0] * self.NumRemaps
        #self.RemapCounts = [len(bone_names) for bone_names in remap_info]
        self.Remaps = []
        self.RemapOffsets = [8*self.NumRemaps+4]
        for i, bone_names in enumerate(remap_info):
            r = []
            for bone in bone_names:
                try:
                    h = int(bone)
                except ValueError:
                    h = murmur32_hash(bone.encode("utf-8"))
                try:
                    real_index = transform_info.NameHashes.index(h)
                except ValueError: # bone not in transform info for unit, unrecoverable
                    PrettyPrint(f"Bone '{bone}' does not exist in unit transform info, skipping...")
                    continue
                try:
                    r.append(self.RealIndices.index(real_index))
                    self.RemapCounts[i] += 1
                except ValueError:
                    PrettyPrint(f"Bone '{bone}' does not exist in LOD bone info, adding...")
                    self.RealIndices.append(real_index)
                    r.append(len(self.RealIndices)-1)
                    self.RemapCounts[i] += 1
                    self.NumBones += 1
                    self.Bones.append(None)

            self.Remaps.append(r)

        for i in range(1, self.NumRemaps):
            self.RemapOffsets.append(
                self.RemapOffsets[i-1] + 4*self.RemapCounts[i-1]
            )

class StreamInfo:
    def __init__(self):
        self.Components = []
        self.ComponentInfoID = self.NumComponents = self.VertexBufferID = self.VertexBuffer_unk1 = self.NumVertices = self.VertexStride = self.VertexBuffer_unk2 = self.VertexBuffer_unk3 = 0
        self.IndexBufferID = self.IndexBuffer_unk1 = self.NumIndices = self.IndexBuffer_unk2 = self.IndexBuffer_unk3 = self.IndexBuffer_Type = self.VertexBufferOffset = self.VertexBufferSize = self.IndexBufferOffset = self.IndexBufferSize = 0
        self.VertexBufferOffset = self.VertexBufferSize = self.IndexBufferOffset = self.IndexBufferSize = 0
        self.UnkEndingBytes = bytearray(16)
        self.DEV_StreamInfoOffset    = self.DEV_ComponentInfoOffset = 0 # helper vars, not in file

    def Serialize(self, f: MemoryStream, devUnitVersion=0):
        self.DEV_StreamInfoOffset = f.tell()
        self.ComponentInfoID = f.uint64(self.ComponentInfoID)
        self.DEV_ComponentInfoOffset = f.tell()
        f.seek(self.DEV_ComponentInfoOffset + 320)
        # vertex buffer info
        self.NumComponents      = f.uint64(len(self.Components))
        self.VertexBufferID     = f.uint64(self.VertexBufferID)
        self.VertexBuffer_unk1  = f.uint64(self.VertexBuffer_unk1)
        self.NumVertices        = f.uint32(self.NumVertices)
        self.VertexStride       = f.uint32(self.VertexStride)
        self.VertexBuffer_unk2  = f.uint64(self.VertexBuffer_unk2)
        self.VertexBuffer_unk3  = f.uint64(self.VertexBuffer_unk3)
        # index buffer info
        self.IndexBufferID      = f.uint64(self.IndexBufferID)
        self.IndexBuffer_unk1   = f.uint64(self.IndexBuffer_unk1)
        self.NumIndices         = f.uint32(self.NumIndices)
        self.IndexBuffer_Type   = f.uint32(self.IndexBuffer_Type)
        self.IndexBuffer_unk2   = f.uint64(self.IndexBuffer_unk2)
        self.IndexBuffer_unk3   = f.uint64(self.IndexBuffer_unk3)
        # offset info
        self.VertexBufferOffset = f.uint32(self.VertexBufferOffset)
        self.VertexBufferSize   = f.uint32(self.VertexBufferSize)
        self.IndexBufferOffset  = f.uint32(self.IndexBufferOffset)
        self.IndexBufferSize    = f.uint32(self.IndexBufferSize)
        # allign to 16
        self.UnkEndingBytes     = f.bytes(self.UnkEndingBytes, 16) # exact length is unknown
        EndOffset = ceil(float(f.tell())/16) * 16
        # component info
        f.seek(self.DEV_ComponentInfoOffset)
        if f.IsReading():
            self.Components = [StreamComponentInfo(devUnitVersion=devUnitVersion) for n in range(self.NumComponents)]
        self.Components = [Comp.Serialize(f, devUnitVersion=devUnitVersion) for Comp in self.Components]

        # return
        f.seek(EndOffset)
        return self

class MeshSectionInfo: # material info
    def __init__(self, material_slot_list=[]):
        self.MaterialIndex = self.VertexOffset=self.NumVertices=self.IndexOffset=self.NumIndices=self.unk2 = 0
        self.DEV_MeshInfoOffset=0 # helper var, not in file
        self.material_slot_list = material_slot_list
        self.ID = 0
        self.MaterialIndex = self.GroupIndex = 0
    def Serialize(self, f: MemoryStream):
        self.DEV_MeshInfoOffset = f.tell()
        self.MaterialIndex           = f.uint32(self.MaterialIndex)
        if f.IsReading():
            self.ID = self.material_slot_list[self.MaterialIndex]
        self.VertexOffset   = f.uint32(self.VertexOffset)
        self.NumVertices    = f.uint32(self.NumVertices)
        self.IndexOffset    = f.uint32(self.IndexOffset)
        self.NumIndices     = f.uint32(self.NumIndices)
        self.GroupIndex           = f.uint32(self.GroupIndex)
        return self

class MeshInfo:
    def __init__(self):
        self.unk1 = self.unk3 = self.unk4 = self.TransformIndex = self.LodIndex = self.StreamIndex = self.NumSections = self.unk7 = self.unk8 = self.unk9 = self.NumSections_unk = self.MeshID = 0
        self.unk2 = bytearray(32); self.unk6 = bytearray(40)
        self.MaterialIDs = self.Sections = []
        self.NumMaterials = 0
        self.MaterialOffset = 0
        self.SectionsOffset = 0
    def Serialize(self, f: MemoryStream):
        start_offset = f.tell()
        self.unk1 = f.uint64(self.unk1)
        self.unk2 = f.bytes(self.unk2, 32)
        self.MeshID= f.uint32(self.MeshID)
        self.unk3 = f.uint32(self.unk3)
        self.TransformIndex = f.uint32(self.TransformIndex)
        self.unk4 = f.uint32(self.unk4)
        self.LodIndex       = f.int32(self.LodIndex)
        self.StreamIndex    = f.uint32(self.StreamIndex)
        self.unk6           = f.bytes(self.unk6, 40)
        self.NumMaterials = f.uint32(self.NumMaterials)
        self.MaterialOffset = f.uint32(self.MaterialOffset)
        self.unk8           = f.uint64(self.unk8)
        self.NumSections    = f.uint32(self.NumSections)
        if f.IsWriting(): self.SectionsOffset = self.MaterialOffset + 4*self.NumMaterials
        self.SectionsOffset  = f.uint32(self.SectionsOffset)
        if f.IsReading(): self.MaterialIDs  = [0 for n in range(self.NumMaterials)]
        else:             self.MaterialIDs  = [section.ID for section in self.Sections]
        self.MaterialIDs  = [f.uint32(ID) for ID in self.MaterialIDs]
        if f.IsReading(): self.Sections    = [MeshSectionInfo(self.MaterialIDs) for n in range(self.NumSections)]
        self.Sections   = [Section.Serialize(f) for Section in self.Sections]
        return self
    def GetNumIndices(self):
        total = 0
        for section in self.Sections:
            total += section.NumIndices
        return total
    def GetNumVertices(self):
        return self.Sections[0].NumVertices

class StingrayMatrix4x4: # Matrix4x4: https://help.autodesk.com/cloudhelp/ENU/Stingray-SDK-Help/engine_c/plugin__api__types_8h.html#line_89
    def __init__(self):
        self.v = [float(0)]*16
    def Serialize(self, f: MemoryStream):
        self.v = [f.float32(value) for value in self.v]
        return self
    def ToBlenderMatrix(self):
        mat = mathutils.Matrix.Identity(4)
        mat[0] = self.v[0:4]
        mat[1] = self.v[4:8]
        mat[2] = self.v[8:12]
        mat[3] = self.v[12:16]
        mat.transpose()
        return mat
    def ToLocalTransform(self):
        matrix = mathutils.Matrix([
            [self.v[0], self.v[1], self.v[2], self.v[12]],
            [self.v[4], self.v[5], self.v[6], self.v[13]],
            [self.v[8], self.v[9], self.v[10], self.v[14]],
            [self.v[3], self.v[7], self.v[11], self.v[15]]
        ])
        local_transform = StingrayLocalTransform()
        loc, rot, scale = matrix.decompose()
        rot = rot.to_matrix()
        local_transform.pos = loc
        local_transform.scale = scale
        local_transform.rot.x = rot[0]
        local_transform.rot.y = rot[1]
        local_transform.rot.z = rot[2]
        return local_transform

class StingrayMatrix3x3: # Matrix3x3: https://help.autodesk.com/cloudhelp/ENU/Stingray-SDK-Help/engine_c/plugin__api__types_8h.html#line_84
    def __init__(self):
        self.x = [1,0,0]
        self.y = [0,1,0]
        self.z = [0,0,1]
    def Serialize(self, f: MemoryStream):
        self.x = f.vec3_float(self.x)
        self.y = f.vec3_float(self.y)
        self.z = f.vec3_float(self.z)
        return self
    def ToQuaternion(self):
        T = self.x[0] + self.y[1] + self.z[2]
        M = max(T, self.x[0], self.y[1], self.z[2])
        qmax = 0.5 * sqrt(1-T + 2*M)
        if M == self.x[0]:
            qx = qmax
            qy = (self.x[1] + self.y[0]) / (4*qmax)
            qz = (self.x[2] + self.z[0]) / (4*qmax)
            qw = (self.z[1] - self.y[2]) / (4*qmax)
        elif M == self.y[1]:
            qx = (self.x[1] + self.y[0]) / (4*qmax)
            qy = qmax
            qz = (self.y[2] + self.z[1]) / (4*qmax)
            qw = (self.x[2] - self.z[0]) / (4*qmax)
        elif M == self.z[2]:
            qx = (self.x[2] + self.z[0]) / (4*qmax)
            qy = (self.y[2] + self.z[1]) / (4*qmax)
            qz = qmax
            qw = (self.x[2] - self.z[0]) / (4*qmax)
        else:
            qx = (self.z[1] - self.y[2]) / (4*qmax)
            qy = (self.x[2] - self.z[0]) / (4*qmax)
            qz = (self.y[0] + self.x[1]) / (4*qmax)
            qw = qmax
        return [qx, qy, qz, qw]

class StingrayLocalTransform: # Stingray Local Transform: https://help.autodesk.com/cloudhelp/ENU/Stingray-SDK-Help/engine_c/plugin__api__types_8h.html#line_100
    def __init__(self):
        self.rot   = StingrayMatrix3x3()
        self.pos   = [0,0,0]
        self.scale = [1,1,1]
        self.dummy = 0 # Force 16 byte alignment
        self.Incriment = self.ParentBone = 0

    def Serialize(self, f: MemoryStream):
        self.rot    = self.rot.Serialize(f)
        self.pos    = f.vec3_float(self.pos)
        self.scale  = f.vec3_float(self.scale)
        self.dummy  = f.float32(self.dummy)
        return self
    def SerializeV2(self, f: MemoryStream): # Quick and dirty solution, unknown exactly what this is for
        f.seek(f.tell()+48)
        self.pos    = f.vec3_float(self.pos)
        self.dummy  = f.float32(self.dummy)
        return self
    def SerializeTransformEntry(self, f: MemoryStream):
        self.Incriment = f.uint16(self.Incriment)
        self.ParentBone = f.uint16(self.ParentBone)
        return self

class TransformInfo: # READ ONLY
    def __init__(self):
        self.NumTransforms = 0
        self.Transforms = []
        self.TransformMatrices = []
        self.TransformEntries = []
        self.NameHashes = []
    def Serialize(self, f: MemoryStream):
        if f.IsReading():
            self.NumTransforms = f.uint32(self.NumTransforms)
            f.seek(f.tell()+12)
            self.Transforms = [StingrayLocalTransform().Serialize(f) for n in range(self.NumTransforms)]
            self.TransformMatrices = [StingrayMatrix4x4().Serialize(f) for n in range(self.NumTransforms)]
            self.TransformEntries = [StingrayLocalTransform().SerializeTransformEntry(f) for n in range(self.NumTransforms)]
            self.NameHashes = [f.uint32(n) for n in range(self.NumTransforms)]
        else:
            self.NumTransforms = f.uint32(self.NumTransforms)
            f.seek(f.tell()+12)
            self.Transforms = [t.Serialize(f) for t in self.Transforms]
            self.TransformMatrices = [t.Serialize(f) for t in self.TransformMatrices]
            self.TransformEntries = [t.SerializeTransformEntry(f) for t in self.TransformEntries]
            self.NameHashes = [f.uint32(h) for h in self.NameHashes]
        return self

class CustomizationInfo: # READ ONLY
    def __init__(self):
        self.BodyType  = ""
        self.Slot      = ""
        self.Weight    = ""
        self.PieceType = ""
    def Serialize(self, f: MemoryStream):
        if f.IsWriting():
            raise Exception("This struct is read only (write not implemented)")
        try: # TODO: fix this, this is basically completely wrong, this is generic user data, but for now this works
            f.seek(f.tell()+24)
            length = f.uint32(0)
            self.BodyType = bytes(f.bytes(b"", length)).replace(b"\x00", b"").decode()
            f.seek(f.tell()+12)
            length = f.uint32(0)
            self.Slot = bytes(f.bytes(b"", length)).replace(b"\x00", b"").decode()
            f.seek(f.tell()+12)
            length = f.uint32(0)
            self.Weight = bytes(f.bytes(b"", length)).replace(b"\x00", b"").decode()
            f.seek(f.tell()+12)
            length = f.uint32(0)
            self.PieceType = bytes(f.bytes(b"", length)).replace(b"\x00", b"").decode()
        except:
            self.BodyType  = ""
            self.Slot      = ""
            self.Weight    = ""
            self.PieceType = ""
            pass # tehee

class StreamComponentInfo:

    def __init__(self, type="position", format="float", devUnitVersion=0):
        self.DEVUnitVersion = devUnitVersion
        self.Type   = self.TypeFromName(type)
        self.Format = self.FormatFromName(format)
        self.Index   = 0
        self.Unknown = 0
    def Serialize(self, f: MemoryStream, devUnitVersion=0):
        self.Type      = f.uint32(self.Type)
        self.Format    = f.uint32(self.Format)
        self.Index     = f.uint32(self.Index)
        self.Unknown   = f.uint64(self.Unknown)
        self.DEVUnitVersion = devUnitVersion
        return self
    def TypeName(self):
        if   self.Type == 0: return "position"
        elif self.Type == 1: return "normal"
        elif self.Type == 2: return "tangent" # not confirmed
        elif self.Type == 3: return "bitangent" # not confirmed
        elif self.Type == 4: return "uv"
        elif self.Type == 5: return "color"
        elif self.Type == 6: return "bone_index"
        elif self.Type == 7: return "bone_weight"
        return "unknown"
    def TypeFromName(self, name):
        if   name == "position": return 0
        elif name == "normal":   return 1
        elif name == "tangent":  return 2
        elif name == "bitangent":return 3
        elif name == "uv":       return 4
        elif name == "color":    return 5
        elif name == "bone_index":  return 6
        elif name == "bone_weight": return 7
        return -1
    def FormatFromName(self, name):
        format = -1
        if   name == "float":         format = 0
        elif name == "vec2_float":    format =  1
        elif name == "vec3_float":    format = 2
        elif name == "vec4_float":    format = 3
        elif name == "rgba_r8g8b8a8": format = 4
        elif name == "vec4_uint32": format = 24
        elif name == "vec4_uint8":  format = 28
        elif name == "vec4_1010102":  format = 29
        elif name == "unk_normal":  format = 30
        elif name == "vec2_half":   format = 33
        elif name == "vec4_half":   format = 35
        if self.DEVUnitVersion in [10800437, 10800436, 1] and format > 16: format -= 4 # quick and dirty fix for format changes in newer versions of the unit file, this is really gross and should be fixed properly at some point
        return format
    def GetSize(self):
        if self.DEVUnitVersion in [10800437, 10800436, 1]:
                if   self.Format == 0:  return 4
                elif self.Format == 1:  return 8
                elif self.Format == 2:  return 12
                elif self.Format == 3:  return 16
                elif self.Format == 4:  return 4
                elif self.Format == 20: return 16
                elif self.Format == 24: return 4
                elif self.Format == 25: return 4
                elif self.Format == 26: return 4
                elif self.Format == 29: return 4
                elif self.Format == 31: return 8
                raise Exception("Unit file version: " + str(self.DEVUnitVersion) + " Cannot get size of unknown vertex format: "+str(self.Format))
        if   self.Format == 0:  return 4
        elif self.Format == 1:  return 8
        elif self.Format == 2:  return 12
        elif self.Format == 3:  return 16
        elif self.Format == 4:  return 4
        elif self.Format == 24: return 16
        elif self.Format == 28: return 4
        elif self.Format == 29: return 4
        elif self.Format == 30: return 4
        elif self.Format == 33: return 4
        elif self.Format == 35: return 8
        raise Exception("Cannot get size of unknown vertex format: "+str(self.Format))
    def SerializeComponent(self, f: MemoryStream, value):
        try:
            if self.DEVUnitVersion in [10800437, 10800436, 1]:
                serialize_func = FUNCTION_LUTS.SERIALIZE_COMPONENT_LUT_OLD_UNITS[self.Format]
            else:
                serialize_func = FUNCTION_LUTS.SERIALIZE_COMPONENT_LUT[self.Format]
            return serialize_func(f, value)
        except:
            raise Exception("Cannot serialize unknown vertex format: "+str(self.Format))

class RawMeshClass:
    def NativeVertexSnapshot(self):
        return tuple(getattr(self, field) for field in (
            "VertexPositions", "VertexNormals", "VertexTangents", "VertexBiTangents",
            "VertexUVs", "VertexColors", "VertexBoneIndices", "VertexWeights"))

    def CanPreserveNativeStream(self):
        return (self.IsCullingBody()
                and hasattr(self, "DEV_NativeVertexSnapshot")
                and self.NativeVertexSnapshot() == self.DEV_NativeVertexSnapshot
                and len(self.DEV_NativeVertexBytes) == len(self.VertexPositions) * self.DEV_NativeStride)

    def __init__(self):
        self.MeshInfoIndex = 0
        self.VertexPositions  = []
        self.VertexNormals    = []
        self.VertexTangents   = []
        self.VertexBiTangents = []
        self.VertexUVs        = []
        self.VertexColors     = []
        self.VertexBoneIndices= []
        self.VertexWeights    = []
        self.Indices          = []
        self.Materials        = []
        self.LodIndex         = -1
        self.MeshID           = 0
        self.DEV_Use32BitIndices = False
        self.DEV_BoneInfo      = None
        self.DEV_BoneInfoIndex = 0
        self.DEV_Transform     = None
    def IsCullingBody(self):
        IsPhysics = True
        for material in self.Materials:
            if material.MatID != material.DefaultMaterialName:
                IsPhysics = False
        return IsPhysics
    def IsLod(self):
        IsLod = True
        if self.LodIndex == 0 or self.LodIndex == -1:
            IsLod = False
        if self.IsCullingBody():
            IsLod = False
        return IsLod
    def IsStaticMesh(self):
        for vertex in self.VertexWeights:
            if vertex != [0, 0, 0, 0]:
                return False
        return True

    def InitBlank(self, numVertices, numIndices, numUVs, numBoneIndices):
        self.VertexPositions    = [[0,0,0] for n in range(numVertices)]
        self.VertexNormals      = [[0,0,0] for n in range(numVertices)]
        self.VertexTangents     = [[0,0,0] for n in range(numVertices)]
        self.VertexBiTangents   = [[0,0,0] for n in range(numVertices)]
        self.VertexColors       = [[0,0,0,0] for n in range(numVertices)]
        self.VertexWeights      = [[0,0,0,0] for n in range(numVertices)]
        self.Indices            = [[0,0,0] for n in range(int(numIndices/3))]
        for idx in range(numUVs):
            self.VertexUVs.append([[0,0] for n in range(numVertices)])
        for idx in range(numBoneIndices):
            self.VertexBoneIndices.append([[0,0,0,0] for n in range(numVertices)])

    def ReInitVerts(self, numVertices):
        self.VertexPositions    = [[0,0,0] for n in range(numVertices)]
        self.VertexNormals      = [[0,0,0] for n in range(numVertices)]
        self.VertexTangents     = [[0,0,0] for n in range(numVertices)]
        self.VertexBiTangents   = [[0,0,0] for n in range(numVertices)]
        self.VertexColors       = [[0,0,0,0] for n in range(numVertices)]
        self.VertexWeights      = [[0,0,0,0] for n in range(numVertices)]
        numVerts        = len(self.VertexUVs)
        numBoneIndices  = len(self.VertexBoneIndices)
        self.VertexUVs = []
        self.VertexBoneIndices = []
        for idx in range(numVerts):
            self.VertexUVs.append([[0,0] for n in range(numVertices)])
        for idx in range(numBoneIndices):
            self.VertexBoneIndices.append([[0,0,0,0] for n in range(numVertices)])

class RawMaterialClass:
    DefaultMaterialName    = "StingrayDefaultMaterial"
    DefaultMaterialShortID = 155175220
    def __init__(self):
        self.MatID      = self.DefaultMaterialName
        self.ShortID    = self.DefaultMaterialShortID
        self.StartIndex = 0
        self.NumIndices = 0
        self.DEV_BoneInfoOverride = None

    def IDFromName(self, unit_id, name, index):
        if name.find(self.DefaultMaterialName) != -1:
            self.MatID   = self.DefaultMaterialName
            self.ShortID = self.DefaultMaterialShortID
        else:
            try:
                self.MatID   = int(name)
                try:
                    self.ShortID = Global_MaterialSlotNames[unit_id][self.MatID][index]
                except (KeyError, IndexError):
                    # Custom materials are not present in the target Unit's
                    # original material-slot table.  The old fallback generated
                    # a fresh random section ID on every save, so byte-identical
                    # authoring input produced different Unit metadata.  Use a
                    # stable synthetic slot name and avoid collisions with the
                    # target Unit's known slots instead.
                    reserved = {
                        int(slot)
                        for slots in Global_MaterialSlotNames.get(unit_id, {}).values()
                        for slot in slots
                    }
                    salt = 0
                    while True:
                        key = (
                            f"HD2SDK_CustomMaterialSlot:{unit_id}:"
                            f"{self.MatID}:{index}:{salt}"
                        )
                        candidate = murmur32_hash(key.encode("utf-8"))
                        if (
                            candidate not in (0, self.DefaultMaterialShortID)
                            and candidate not in reserved
                        ):
                            break
                        salt += 1
                    self.ShortID = candidate
                    PrettyPrint(
                        f"Unable to find original material slot for material {name} "
                        f"index {index} in unit {unit_id}; using deterministic "
                        f"custom slot {self.ShortID}"
                    )
            except:
                raise Exception("Material name must be a number")

class BoneIndexException(Exception):
    pass

class LightList:

    def __init__(self):
        self.lights = []
        self.light_count = 0
        self.unk0 = [0, 0, 0]

    def Serialize(self, stream: MemoryStream, end_offset=None):
        if stream.IsWriting():
            self.light_count = len(self.lights)
        self.light_count = stream.uint32(self.light_count)
        self.unk0 = [stream.uint32(i) for i in self.unk0]
        if stream.IsReading():
            section_end = len(stream.Data) if end_offset is None else end_offset
            if section_end < stream.tell() or section_end > len(stream.Data):
                raise ValueError("HD2 Light table end offset is invalid")
            available_records = (section_end - stream.tell()) // 112
            if self.light_count > available_records:
                raise ValueError(
                    f"HD2 Light 表声明 {self.light_count} 条记录，但剩余数据最多容纳 "
                    f"{available_records} 条"
                )
            self.lights = [Light() for _ in range(self.light_count)]
        for light in self.lights:
            light.Serialize(stream)

class Light:

    OMNI = 0 # point in Blender
    SPOT = 1 # spot
    BOX = 2 # area?
    DIRECTIONAL = 3 # sun

    CAST_SHADOW = 0x1
    DISABLED = 0x2
    INDIRECT_LIGHTING = 0x4
    VOLUMETRIC_FOG = 0x10
    DIRECT_LIGHTING = 0x40

    def __init__(self):
        self.name_hash = self.bone_index = self.falloff_start = self.falloff_end = self.start_angle = self.end_angle = self.unk0 = self.flags = self.light_type = 0
        self.intensity = self.falloff_exp = 1
        self.shadow_bias = 0.4
        self.unk1 = [0] * 5
        self.unk2 = bytearray(32)
        self.color = [0, 0, 0]

    def Serialize(self, stream: MemoryStream):
        self.name_hash = stream.uint32(self.name_hash)
        self.bone_index = stream.uint32(self.bone_index)
        self.color = [stream.float32(i) for i in self.color]
        self.intensity = stream.float32(self.intensity)
        self.falloff_start = stream.float32(self.falloff_start)
        self.falloff_end = stream.float32(self.falloff_end)
        self.falloff_exp = stream.float32(self.falloff_exp)
        self.start_angle = stream.float32(self.start_angle)
        self.end_angle = stream.float32(self.end_angle)
        self.unk0 = stream.float32(self.unk0)
        self.shadow_bias = stream.float32(self.shadow_bias)
        self.unk1 = [stream.float32(i) for i in self.unk1]
        self.flags = stream.uint8(self.flags)
        for _ in range(3):
            stream.uint8(0)
        self.light_type = stream.uint32(self.light_type)
        self.unk2 = stream.bytes(self.unk2, 32)

def sign(n):
    if n >= 0:
        return 1
    if n < 0:
        return -1

def octahedral_encode(x, y, z):
    l1_norm = abs(x) + abs(y) + abs(z)
    if l1_norm == 0: return 0, 0
    x /= l1_norm
    y /= l1_norm
    if z < 0:
        x, y = ((1-abs(y)) * sign(x)), ((1-abs(x)) * sign(y))
    return x, y

def octahedral_decode(x, y):
    z = 1 - abs(x) - abs(y)
    if z < 0:
        x, y = ((1-abs(y)) * sign(x)), ((1-abs(x)) * sign(y))
    return mathutils.Vector((x, y, z)).normalized().to_tuple()

def decode_packed_oct_norm(norm):
    r10 = norm & 0x3ff
    g10 = (norm >> 10) & 0x3ff
    return octahedral_decode(
        r10 * (2.0/1023.0) - 1,
        g10 * (2.0/1023.0) - 1
    )

def encode_packed_oct_norm(x, y, z):
    x, y = octahedral_encode(x, y, z)
    return int((x+1)*(1023.0/2.0)) | (int((y+1)*(1023.0/2.0)) << 10)

class SerializeFunctions:

    def SerializePositionComponent(gpu, mesh, component, vidx):
        mesh.VertexPositions[vidx] = component.SerializeComponent(gpu, mesh.VertexPositions[vidx])

    def SerializeNormalComponent(gpu, mesh, component, vidx):
        if gpu.IsReading():
            norm = component.SerializeComponent(gpu, mesh.VertexNormals[vidx])
            if not isinstance(norm, int):
                while len(norm) < 3:
                    norm.append(0.0)
                norm = list(mathutils.Vector((norm[0],norm[1],norm[2])).normalized())
                mesh.VertexNormals[vidx] = norm[:3]
            else:
                mesh.VertexNormals[vidx] = decode_packed_oct_norm(norm)
        else:
            norm = encode_packed_oct_norm(*mathutils.Vector(mesh.VertexNormals[vidx]).normalized().to_tuple())
            norm = component.SerializeComponent(gpu, norm)

    def SerializeTangentComponent(gpu, mesh, component, vidx):
        mesh.VertexTangents[vidx] = component.SerializeComponent(gpu, mesh.VertexTangents[vidx])

    def SerializeBiTangentComponent(gpu, mesh, component, vidx):
        mesh.VertexBiTangents[vidx] = component.SerializeComponent(gpu, mesh.VertexBiTangents[vidx])

    def SerializeUVComponent(gpu, mesh, component, vidx):
        mesh.VertexUVs[component.Index][vidx] = component.SerializeComponent(gpu, mesh.VertexUVs[component.Index][vidx])

    def SerializeColorComponent(gpu, mesh, component, vidx):
        mesh.VertexColors[vidx] = component.SerializeComponent(gpu, mesh.VertexColors[vidx])

    def SerializeBoneIndexComponent(gpu, mesh, component, vidx):
        try:
             mesh.VertexBoneIndices[component.Index][vidx] = component.SerializeComponent(gpu, mesh.VertexBoneIndices[component.Index][vidx])
        except:
            raise BoneIndexException(f"Vertex bone index out of range. Component index: {component.Index} vidx: {vidx}")

    def SerializeBoneWeightComponent(gpu, mesh, component, vidx):
        if component.Index > 0: # TODO: add support for this (check archive 9102938b4b2aef9d)
            PrettyPrint("Multiple weight indices are unsupported!", "warn")
            gpu.seek(gpu.tell()+component.GetSize())
        else:
            weight = mesh.VertexWeights[vidx]
            # Native culling meshes can store one scalar influence. Rebuilt
            # streams use vec4_half: pad that influence, do not broadcast it
            # through the generic vector serializer (1 -> [1, 1, 1, 1]).
            if (not gpu.IsReading() and isinstance(weight, (int, float))
                    and component.Format == component.FormatFromName("vec4_half")):
                weight = [weight, 0.0, 0.0, 0.0]
            mesh.VertexWeights[vidx] = component.SerializeComponent(gpu, weight)


    def SerializeFloatComponent(f: MemoryStream, value):
        return f.float32(value)

    def SerializeVec2FloatComponent(f: MemoryStream, value):
        return f.vec2_float(value)

    def SerializeVec3FloatComponent(f: MemoryStream, value):
        return f.vec3_float(value)

    def SerializeVec4FloatComponent(f: MemoryStream, value):
        return f.vec4_float(value)

    def SerializeRGBA8888Component(f: MemoryStream, value):
        if f.IsReading():
            value = f.vec4_uint8([0,0,0,0])
            value[0] = min(1, float(value[0]/255))
            value[1] = min(1, float(value[1]/255))
            value[2] = min(1, float(value[2]/255))
            value[3] = min(1, float(value[3]/255))
        else:
            r = min(255, int(value[0]*255))
            g = min(255, int(value[1]*255))
            b = min(255, int(value[2]*255))
            a = min(255, int(value[3]*255))
            value = f.vec4_uint8([r,g,b,a])
        return value

    def SerializeUint32Component(f: MemoryStream, value):
        return f.uint32(value)

    def SerializeVec2Uint32Component(f: MemoryStream, value):
        return f.vec2_uint32(value)

    def SerializeVec3Uint32Component(f: MemoryStream, value):
        return f.vec3_uint32(value)

    def SerializeVec4Uint32Component(f: MemoryStream, value):
        return f.vec4_uint32(value)

    def SerializeUint8Component(f: MemoryStream, value):
        return f.uint8(value)

    def SerializeVec2Uint8Component(f: MemoryStream, value):
        return f.vec2_uint8(value)

    def SerializeVec3Uint8Component(f: MemoryStream, value):
        return f.vec3_uint8(value)

    def SerializeVec4Uint8Component(f: MemoryStream, value):
        return f.vec4_uint8(value)

    def SerializeVec41010102Component(f: MemoryStream, value):
        if f.IsReading():
            value = TenBitUnsigned(f.uint32(0))
            value[3] = 0 # seems to be needed for weights
        else:
            f.uint32(MakeTenBitUnsigned(value))
        return value

    def SerializeUnkNormalComponent(f: MemoryStream, value):
        if isinstance(value, int):
            return f.uint32(value)
        else:
            return f.uint32(0)

    def SerializeFloat16Component(f: MemoryStream, value):
        return f.float16(value)

    def SerializeVec2HalfComponent(f: MemoryStream, value):
        return f.vec2_half(value)

    def SerializeVec3HalfComponent(f: MemoryStream, value):
        return f.vec3_half(value)

    def SerializeVec4HalfComponent(f: MemoryStream, value):
        if isinstance(value, float):
            return f.vec4_half([value,value,value,value])
        else:
            return f.vec4_half(value)

    def SerializeUnknownComponent(f: MemoryStream, value):
        raise Exception("Cannot serialize unknown vertex format!")

class StreamComponentType:
    POSITION = 0
    NORMAL = 1
    TANGENT = 2 # not confirmed
    BITANGENT = 3 # not confirmed
    UV = 4
    COLOR = 5
    BONE_INDEX = 6
    BONE_WEIGHT = 7
    UNKNOWN_TYPE = -1

class StreamComponentFormat:
    FLOAT = 0
    VEC2_FLOAT = 1
    VEC3_FLOAT = 2
    VEC4_FLOAT = 3
    RGBA_R8G8B8A8 = 4
    UINT32 = 21
    VEC2_UINT32 = 22
    VEC3_UINT32 = 23
    VEC4_UINT32 = 24
    UINT8 = 25
    VEC2_UINT8 = 26
    VEC3_UINT8 = 27
    VEC4_UINT8 = 28
    VEC4_1010102 = 29
    UNK_NORMAL = 30
    FLOAT16 = 32
    VEC2_HALF = 33
    VEC3_HALF = 34
    VEC4_HALF = 35
    UNKNOWN_TYPE = -1

class StreamComponentFormat_OLD_UNITS:
    FLOAT = 0
    VEC2_FLOAT = 1
    VEC3_FLOAT = 2
    VEC4_FLOAT = 3
    RGBA_R8G8B8A8 = 4
    UINT32 = 17
    VEC2_UINT32 = 18
    VEC3_UINT32 = 19
    VEC4_UINT32 = 20
    UINT8 = 21
    VEC2_UINT8 = 22
    VEC3_UINT8 = 23
    VEC4_UINT8 = 24
    VEC4_1010102 = 25
    UNK_NORMAL = 26
    FLOAT16 = 28
    VEC2_HALF = 29
    VEC3_HALF = 30
    VEC4_HALF = 31
    UNKNOWN_TYPE = -1

class FUNCTION_LUTS:

    SERIALIZE_MESH_LUT = {
        StreamComponentType.POSITION: SerializeFunctions.SerializePositionComponent,
        StreamComponentType.NORMAL: SerializeFunctions.SerializeNormalComponent,
        StreamComponentType.TANGENT: SerializeFunctions.SerializeTangentComponent,
        StreamComponentType.BITANGENT: SerializeFunctions.SerializeBiTangentComponent,
        StreamComponentType.UV: SerializeFunctions.SerializeUVComponent,
        StreamComponentType.COLOR: SerializeFunctions.SerializeColorComponent,
        StreamComponentType.BONE_INDEX: SerializeFunctions.SerializeBoneIndexComponent,
        StreamComponentType.BONE_WEIGHT: SerializeFunctions.SerializeBoneWeightComponent
    }

    SERIALIZE_COMPONENT_LUT = {
        StreamComponentFormat.FLOAT: SerializeFunctions.SerializeFloatComponent,
        StreamComponentFormat.VEC2_FLOAT: SerializeFunctions.SerializeVec2FloatComponent,
        StreamComponentFormat.VEC3_FLOAT: SerializeFunctions.SerializeVec3FloatComponent,
        StreamComponentFormat.VEC4_FLOAT: SerializeFunctions.SerializeVec4FloatComponent,
        StreamComponentFormat.RGBA_R8G8B8A8: SerializeFunctions.SerializeRGBA8888Component,
        StreamComponentFormat.UINT32: SerializeFunctions.SerializeUint32Component,
        StreamComponentFormat.VEC2_UINT32: SerializeFunctions.SerializeVec2Uint32Component,
        StreamComponentFormat.VEC3_UINT32: SerializeFunctions.SerializeVec3Uint32Component,
        StreamComponentFormat.VEC4_UINT32: SerializeFunctions.SerializeVec4Uint32Component,
        StreamComponentFormat.UINT8: SerializeFunctions.SerializeUint8Component,
        StreamComponentFormat.VEC2_UINT8: SerializeFunctions.SerializeVec2Uint8Component,
        StreamComponentFormat.VEC3_UINT8: SerializeFunctions.SerializeVec3Uint8Component,
        StreamComponentFormat.VEC4_UINT8: SerializeFunctions.SerializeVec4Uint8Component,
        StreamComponentFormat.VEC4_1010102: SerializeFunctions.SerializeVec41010102Component,
        StreamComponentFormat.UNK_NORMAL: SerializeFunctions.SerializeUnkNormalComponent,
        StreamComponentFormat.FLOAT16: SerializeFunctions.SerializeFloat16Component,
        StreamComponentFormat.VEC2_HALF: SerializeFunctions.SerializeVec2HalfComponent,
        StreamComponentFormat.VEC3_HALF: SerializeFunctions.SerializeVec3HalfComponent,
        StreamComponentFormat.VEC4_HALF: SerializeFunctions.SerializeVec4HalfComponent
    }

    SERIALIZE_COMPONENT_LUT_OLD_UNITS = {
        StreamComponentFormat_OLD_UNITS.FLOAT: SerializeFunctions.SerializeFloatComponent,
        StreamComponentFormat_OLD_UNITS.VEC2_FLOAT: SerializeFunctions.SerializeVec2FloatComponent,
        StreamComponentFormat_OLD_UNITS.VEC3_FLOAT: SerializeFunctions.SerializeVec3FloatComponent,
        StreamComponentFormat_OLD_UNITS.VEC4_FLOAT: SerializeFunctions.SerializeVec4FloatComponent,
        StreamComponentFormat_OLD_UNITS.RGBA_R8G8B8A8: SerializeFunctions.SerializeRGBA8888Component,
        StreamComponentFormat_OLD_UNITS.UINT32: SerializeFunctions.SerializeUint32Component,
        StreamComponentFormat_OLD_UNITS.VEC2_UINT32: SerializeFunctions.SerializeVec2Uint32Component,
        StreamComponentFormat_OLD_UNITS.VEC3_UINT32: SerializeFunctions.SerializeVec3Uint32Component,
        StreamComponentFormat_OLD_UNITS.VEC4_UINT32: SerializeFunctions.SerializeVec4Uint32Component,
        StreamComponentFormat_OLD_UNITS.UINT8: SerializeFunctions.SerializeUint8Component,
        StreamComponentFormat_OLD_UNITS.VEC2_UINT8: SerializeFunctions.SerializeVec2Uint8Component,
        StreamComponentFormat_OLD_UNITS.VEC3_UINT8: SerializeFunctions.SerializeVec3Uint8Component,
        StreamComponentFormat_OLD_UNITS.VEC4_UINT8: SerializeFunctions.SerializeVec4Uint8Component,
        StreamComponentFormat_OLD_UNITS.VEC4_1010102: SerializeFunctions.SerializeVec41010102Component,
        StreamComponentFormat_OLD_UNITS.UNK_NORMAL: SerializeFunctions.SerializeUnkNormalComponent,
        StreamComponentFormat_OLD_UNITS.FLOAT16: SerializeFunctions.SerializeFloat16Component,
        StreamComponentFormat_OLD_UNITS.VEC2_HALF: SerializeFunctions.SerializeVec2HalfComponent,
        StreamComponentFormat_OLD_UNITS.VEC3_HALF: SerializeFunctions.SerializeVec3HalfComponent,
        StreamComponentFormat_OLD_UNITS.VEC4_HALF: SerializeFunctions.SerializeVec4HalfComponent
    }


def duplicate(obj, data=True, actions=True, collection=None):
    obj_copy = obj.copy()
    if data:
        obj_copy.data = obj_copy.data.copy()
    if actions and obj_copy.animation_data:
        if obj_copy.animation_data.action:
            obj_copy.animation_data.action = obj_copy.animation_data.action.copy()
    bpy.context.collection.objects.link(obj_copy)
    return obj_copy

def PrepareMesh(og_object):
    if og_object.data.shape_keys is not None:
        raise ValueError(f"网格“{og_object.name}”仍有形态键，请先手动删除形态键后再保存")
    object = duplicate(og_object)
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = object
    if og_object.get("HD2SDK_IndependentExportBonesOnly", False):
        # Independent export resolves weights and bind matrices itself.  Keeping
        # the Armature modifier ahead of the temporary normal/triangulate
        # modifiers can make Blender apply evaluated (already skinned) geometry
        # and then serialize skinning a second time.
        for modifier in list(object.modifiers):
            if modifier.type == 'ARMATURE':
                object.modifiers.remove(modifier)
    # split UV seams
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.select_all(action='SELECT')
        bpy.ops.uv.seams_from_islands()
    except: PrettyPrint("Failed to create seams from UV islands. This is not fatal, but will likely cause undesirable results in-game", "warn")
    bpy.ops.object.mode_set(mode='OBJECT')

    bm = bmesh.new()
    bm.from_mesh(object.data)

    # get all sharp edges and uv seams
    sharp_edges = [e for e in bm.edges if not e.smooth]
    boundary_seams = [e for e in bm.edges if e.seam]
    # split edges
    bmesh.ops.split_edges(bm, edges=sharp_edges)
    bmesh.ops.split_edges(bm, edges=boundary_seams)
    # update mesh
    bm.to_mesh(object.data)
    bm.free()
    # transfer normals
    modifier = object.modifiers.new("EXPORT_NORMAL_TRANSFER", 'DATA_TRANSFER')
    bpy.context.object.modifiers[modifier.name].data_types_loops = {'CUSTOM_NORMAL'}
    bpy.context.object.modifiers[modifier.name].object = og_object
    bpy.context.object.modifiers[modifier.name].use_loop_data = True
    bpy.context.object.modifiers[modifier.name].loop_mapping = 'TOPOLOGY'
    bpy.ops.object.modifier_apply(modifier=modifier.name)

    # triangulate
    modifier = object.modifiers.new("EXPORT_TRIANGULATE", 'TRIANGULATE')
    bpy.context.object.modifiers[modifier.name].keep_custom_normals = True
    bpy.ops.object.modifier_apply(modifier=modifier.name)

    # adjust weights
    bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
    try:
        bpy.ops.object.vertex_group_limit_total(group_select_mode='ALL', limit=4)
        # Limiting after normalization leaves the removed influences missing
        # from the total.  Normalize last so every exported skinned vertex has
        # a complete palette weight.
        bpy.ops.object.vertex_group_normalize_all(lock_active=False)
    except: pass

    bpy.ops.object.mode_set(mode='OBJECT')
    return object

def compute_bone_name_hash(bone_name):
    try:
        return int(bone_name)
    except ValueError:
        return murmur32_hash(bone_name.encode("utf-8"))


def _strip_blender_numeric_suffix(name):
    stem, separator, suffix = name.rpartition(".")
    if separator and len(suffix) == 3 and suffix.isdigit():
        return stem
    return name


def _light_object_name_hash(light_object):
    stored_hash = light_object.get(HD2_LIGHT_NAME_HASH_PROP)
    if stored_hash is not None:
        try:
            return int(stored_hash) & 0xFFFFFFFF
        except (TypeError, ValueError):
            PrettyPrint(
                f"灯光 {light_object.name} 的 {HD2_LIGHT_NAME_HASH_PROP} 无效，改用名称哈希",
                "warn",
            )
    return compute_bone_name_hash(_strip_blender_numeric_suffix(light_object.name))


def _light_unit_key(unit_id):
    return str(unit_id)


def _imported_light_units(armature_object):
    raw_value = armature_object.get(HD2_LIGHT_IMPORTED_UNITS_PROP, "[]")
    try:
        values = json.loads(raw_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values}


def _mark_light_unit_imported(armature_object, unit_id):
    units = _imported_light_units(armature_object)
    units.add(_light_unit_key(unit_id))
    armature_object[HD2_LIGHT_IMPORTED_UNITS_PROP] = json.dumps(sorted(units))


def _armature_unit_ids(armature_object):
    unit_ids = set()
    for candidate in bpy.data.objects:
        if candidate.type != "MESH" or "Z_ObjectID" not in candidate:
            continue
        if any(
            modifier.type == "ARMATURE" and modifier.object is armature_object
            for modifier in candidate.modifiers
        ):
            unit_ids.add(str(candidate["Z_ObjectID"]))
    return unit_ids


def _light_belongs_to_unit(light_object, unit_id, armature_object):
    stored_unit = light_object.get(HD2_LIGHT_UNIT_ID_PROP)
    if stored_unit is not None:
        return str(stored_unit) == _light_unit_key(unit_id)

    # Legacy scenes did not tag imported lights. Adopting an untagged light is
    # safe only when this armature is associated with exactly one Unit.
    unit_ids = _armature_unit_ids(armature_object)
    return len(unit_ids) == 1 and _light_unit_key(unit_id) in unit_ids


def _armature_lights_for_unit(armature_object, unit_id):
    lights = [
        child
        for child in armature_object.children
        if child.type == "LIGHT"
        and child.parent is armature_object
        and child.parent_type == "BONE"
        and _light_belongs_to_unit(child, unit_id, armature_object)
    ]
    return sorted(lights, key=lambda item: (_light_object_name_hash(item), item.name))


def _sync_hd2_lights_from_armature(
    armature_object, transform_info, light_list, unit_id
):
    """Write bone-parented Blender lights back to one Unit's HD2 light table."""

    armature_unit_ids = _armature_unit_ids(armature_object)
    if len(armature_unit_ids) > 1:
        for child in armature_object.children:
            if (
                child.type == "LIGHT"
                and child.parent_type == "BONE"
                and child.get(HD2_LIGHT_UNIT_ID_PROP) is None
            ):
                PrettyPrint(
                    f"Merged armature light {child.name} has no {HD2_LIGHT_UNIT_ID_PROP}; "
                    f"it will not be written to Unit {unit_id}",
                    "warn",
                )

    blender_lights = _armature_lights_for_unit(armature_object, unit_id)
    exact_snapshot = _light_unit_key(unit_id) in _imported_light_units(armature_object)
    existing_by_hash = {light.name_hash: light for light in light_list.lights}
    if exact_snapshot:
        rebuilt_lights = []
    else:
        # Old .blend files predate the light import marker. Preserve archive lights
        # that the old plugin never materialized, while still allowing new lights.
        rebuilt_lights = list(light_list.lights)

    rebuilt_indices = {light.name_hash: index for index, light in enumerate(rebuilt_lights)}
    transform_indices = {
        bone_hash: index for index, bone_hash in enumerate(transform_info.NameHashes)
    }
    seen_hashes = set()

    for light_object in blender_lights:
        light_hash = _light_object_name_hash(light_object)
        if light_hash in seen_hashes:
            PrettyPrint(
                f"Unit {unit_id} 中存在重复 HD2 Light 哈希 {light_hash}，已跳过 {light_object.name}",
                "warn",
            )
            continue
        seen_hashes.add(light_hash)

        parent_bone = armature_object.data.bones.get(light_object.parent_bone)
        if parent_bone is None:
            PrettyPrint(
                f"灯光 {light_object.name} 引用不存在的父骨 {light_object.parent_bone}，已跳过",
                "warn",
            )
            continue
        bone_hash = compute_bone_name_hash(parent_bone.name)
        bone_index = transform_indices.get(bone_hash)
        if bone_index is None:
            PrettyPrint(
                f"灯光 {light_object.name} 的父骨 {parent_bone.name} 不在 Unit {unit_id} 的变换表中，已跳过",
                "warn",
            )
            continue

        target = existing_by_hash.get(light_hash, Light())
        target.name_hash = light_hash
        target.bone_index = bone_index
        blender_light = light_object.data
        light_type = blender_light.type

        if light_type == "SPOT":
            target.light_type = Light.SPOT
            target.falloff_end = blender_light.cutoff_distance
            target.end_angle = blender_light.spot_size
        elif light_type == "POINT":
            target.light_type = Light.OMNI
            target.falloff_end = blender_light.cutoff_distance
        elif light_type == "AREA":
            target.light_type = Light.BOX
            target.falloff_start = -(blender_light.size / 2.0)
            target.falloff_end = blender_light.size / 2.0
            target.falloff_exp = 0.0
            target.start_angle = -(blender_light.size_y / 2.0)
            target.end_angle = blender_light.cutoff_distance
            target.unk0 = blender_light.size_y / 2.0
        elif light_type == "SUN":
            target.light_type = Light.DIRECTIONAL
            target.end_angle = math.pi
        else:
            PrettyPrint(
                f"灯光 {light_object.name} 的 Blender 类型 {light_type} 不受 HD2 支持，已跳过",
                "warn",
            )
            continue

        target.flags &= ~(
            Light.CAST_SHADOW | Light.VOLUMETRIC_FOG | Light.DIRECT_LIGHTING
        )
        if getattr(blender_light, "use_shadow", False):
            target.flags |= Light.CAST_SHADOW
        if bool(blender_light.get(HD2_LIGHT_VOLUMETRIC_PROP, False)):
            target.flags |= Light.VOLUMETRIC_FOG
        if bool(blender_light.get(HD2_LIGHT_DIRECT_PROP, False)):
            target.flags |= Light.DIRECT_LIGHTING

        energy = float(blender_light.energy)
        target.color = [float(component) * energy for component in blender_light.color]
        light_object[HD2_LIGHT_NAME_HASH_PROP] = str(light_hash)
        light_object[HD2_LIGHT_UNIT_ID_PROP] = _light_unit_key(unit_id)

        old_index = rebuilt_indices.get(light_hash)
        if old_index is None:
            rebuilt_indices[light_hash] = len(rebuilt_lights)
            rebuilt_lights.append(target)
        else:
            rebuilt_lights[old_index] = target

    light_list.lights = rebuilt_lights
    light_list.light_count = len(rebuilt_lights)
    return len(rebuilt_lights)


def _find_light_parent_bone(armature_object, transform_info, light):
    if light.bone_index < 0 or light.bone_index >= len(transform_info.NameHashes):
        return None
    target_hash = transform_info.NameHashes[light.bone_index]
    for bone in armature_object.data.bones:
        if compute_bone_name_hash(bone.name) == target_hash:
            return bone
    return None


def _configure_blender_light(blender_light, light):
    color_length = sqrt(sum(float(component) ** 2 for component in light.color))
    if color_length > 1e-8:
        blender_light.color = tuple(
            max(0.0, min(1.0, float(component) / color_length))
            for component in light.color
        )
    else:
        blender_light.color = (1.0, 1.0, 1.0)
    blender_light.energy = color_length
    blender_light.use_shadow = bool(light.flags & Light.CAST_SHADOW)
    blender_light[HD2_LIGHT_VOLUMETRIC_PROP] = bool(
        light.flags & Light.VOLUMETRIC_FOG
    )
    blender_light[HD2_LIGHT_DIRECT_PROP] = bool(
        light.flags & Light.DIRECT_LIGHTING
    )
    if hasattr(blender_light, "use_custom_distance"):
        blender_light.use_custom_distance = True

    if light.light_type == Light.SPOT:
        blender_light.cutoff_distance = light.falloff_end
        blender_light.spot_size = light.end_angle
        blender_light.show_cone = True
    elif light.light_type == Light.OMNI:
        blender_light.cutoff_distance = light.falloff_end
    elif light.light_type == Light.BOX:
        blender_light.shape = "RECTANGLE"
        blender_light.size = max(0.0, light.falloff_end - light.falloff_start)
        blender_light.size_y = max(0.0, light.unk0 - light.start_angle)
        blender_light.cutoff_distance = light.end_angle
    elif light.light_type == Light.DIRECTIONAL:
        blender_light.angle = max(0.0, min(math.pi, light.end_angle))


def _import_hd2_lights(
    light_list,
    armature_object,
    transform_info,
    target_collection,
    unit_id,
):
    """Create or refresh this Unit's Blender light objects without duplicates."""

    type_names = {
        Light.OMNI: "POINT",
        Light.SPOT: "SPOT",
        Light.BOX: "AREA",
        Light.DIRECTIONAL: "SUN",
    }
    unit_key = _light_unit_key(unit_id)
    existing = {}
    duplicate_objects = []
    for child in armature_object.children:
        if child.type != "LIGHT" or not _light_belongs_to_unit(
            child, unit_id, armature_object
        ):
            continue
        child_hash = _light_object_name_hash(child)
        if child_hash in existing:
            duplicate_objects.append(child)
        else:
            existing[child_hash] = child

    # Older importer versions could create another object on every reimport.
    # Equal name hashes cannot be represented twice in one Unit, so retain the
    # first deterministic object and remove only the duplicate light objects.
    for duplicate in duplicate_objects:
        light_data = duplicate.data
        bpy.data.objects.remove(duplicate, do_unlink=True)
        if light_data.users == 0:
            bpy.data.lights.remove(light_data)

    imported_hashes = set()
    for light in light_list.lights:
        blender_type = type_names.get(light.light_type)
        if blender_type is None:
            PrettyPrint(
                f"Unit {unit_id} 的灯光 {light.name_hash} 使用未知类型 {light.light_type}，已跳过",
                "warn",
            )
            continue
        parent_bone = _find_light_parent_bone(armature_object, transform_info, light)
        if parent_bone is None:
            PrettyPrint(
                f"Unit {unit_id} 的灯光 {light.name_hash} 找不到骨索引 {light.bone_index}，已跳过",
                "warn",
            )
            continue

        imported_hashes.add(light.name_hash)
        light_object = existing.get(light.name_hash)
        if light_object is None:
            blender_light = bpy.data.lights.new(str(light.name_hash), blender_type)
            light_object = bpy.data.objects.new(str(light.name_hash), blender_light)
            target_collection.objects.link(light_object)
        elif light_object.data.type != blender_type:
            previous_data = light_object.data
            light_object.data = bpy.data.lights.new(str(light.name_hash), blender_type)
            if previous_data.users == 0:
                bpy.data.lights.remove(previous_data)

        _configure_blender_light(light_object.data, light)
        # Store unsigned 32-bit hashes as text: some Blender 4.x builds route
        # ID-property integers through a signed C int and reject values > 2^31-1.
        light_object[HD2_LIGHT_NAME_HASH_PROP] = str(light.name_hash)
        light_object[HD2_LIGHT_UNIT_ID_PROP] = unit_key
        light_object.lock_rotation = (True, True, True)
        light_object.lock_location = (True, True, True)
        light_object.lock_scale = (True, True, True)
        light_object.parent = armature_object
        light_object.parent_type = "BONE"
        light_object.parent_bone = parent_bone.name
        light_object.matrix_parent_inverse = mathutils.Matrix.Rotation(
            math.pi / 2.0, 4, "X"
        )

    # Reimport is a reset for objects previously created by this importer.
    for child in list(armature_object.children):
        if child.type != "LIGHT":
            continue
        if not _light_belongs_to_unit(child, unit_id, armature_object):
            continue
        child_hash = _light_object_name_hash(child)
        if child_hash in imported_hashes:
            continue
        light_data = child.data
        bpy.data.objects.remove(child, do_unlink=True)
        if light_data.users == 0:
            bpy.data.lights.remove(light_data)

    _mark_light_unit_imported(armature_object, unit_id)
    return len(imported_hashes)


ANIMATED_BONE_BASELINE_VERSION = 2


def get_or_initialize_animated_bone_baseline(mesh_object, current_hashes):
    """Return a trustworthy snapshot of the imported Animated membership.

    Version 1 snapshots were written while CreateModel was still constructing
    and merging armatures. With shared/multi-LOD skeletons that provisional
    bone table can differ from the final Blender Animated flags, causing a
    mesh-only first save to look like an animation edit. Migrate those projects
    by snapshotting the actual current Blender flags once.
    """
    baseline_version = int(
        mesh_object.get("HD2SDK_AnimationStructureBaselineVersion", 0)
    )
    stored_hashes = mesh_object.get("HD2SDK_OriginalAnimatedBoneHashes")
    if baseline_version >= ANIMATED_BONE_BASELINE_VERSION and stored_hashes is not None:
        try:
            return [int(value) for value in json.loads(stored_hashes)]
        except (TypeError, ValueError, json.JSONDecodeError):
            PrettyPrint(
                "Invalid Animated bone baseline; rebuilding it from the current armature",
                "warn"
            )

    baseline_hashes = list(current_hashes)
    mesh_object["HD2SDK_OriginalAnimatedBoneHashes"] = json.dumps(baseline_hashes)
    mesh_object["HD2SDK_AnimationStructureBaselineVersion"] = (
        ANIMATED_BONE_BASELINE_VERSION
    )
    return baseline_hashes


def animated_bone_membership_changed(current_hashes, baseline_hashes):
    """Only Animated membership, never order or Mesh data, is a trigger."""
    return set(current_hashes) != set(baseline_hashes)


def reset_reimported_animated_bones(
    armature_object, transform_hashes, source_animated_hashes, bone_names
):
    """Restore this Unit's existing bones to the reimported source state.

    MergeArmatures intentionally reuses the selected Blender armature. Without
    this reset, a bone changed to Animated during an earlier edit remains True
    when the same Unit is imported again, even after the Patch was cleared.
    Limit the reset to this Unit's Transform hashes so unrelated merged bones
    and user-created bones are not touched.
    """
    source_animated_hashes = set(source_animated_hashes)
    reset_count = 0
    for bone_hash in transform_hashes:
        bone_name = bone_names.get(bone_hash, str(bone_hash))
        bone = armature_object.data.bones.get(bone_name)
        if bone is None:
            continue
        bone["Animated"] = bone_hash in source_animated_hashes
        reset_count += 1
    return reset_count


def build_material_bone_remaps(object, mesh):
    """Build a valid bone palette for each material section."""
    material_count = len(object.material_slots)
    if material_count == 0:
        return [], [5000 for _ in mesh.vertices]

    vertex_materials = [set() for _ in mesh.vertices]
    for polygon in mesh.polygons:
        for vertex_index in polygon.vertices:
            vertex_materials[vertex_index].add(polygon.material_index)

    # Shared vertices carry only one bone-index vector in the vertex buffer, so
    # every material section using one must have an identical palette.
    parents = list(range(material_count))

    def find(material_index):
        while parents[material_index] != material_index:
            parents[material_index] = parents[parents[material_index]]
            material_index = parents[material_index]
        return material_index

    def union(first, second):
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parents[second_root] = first_root

    for material_indices in vertex_materials:
        material_indices = sorted(material_indices)
        for material_index in material_indices[1:]:
            union(material_indices[0], material_index)

    used_groups = [set() for _ in range(material_count)]
    for vertex in mesh.vertices:
        weighted_groups = [
            membership.group
            for membership in vertex.groups
            if membership.weight > 0.001
        ][:4]
        for material_index in vertex_materials[vertex.index]:
            used_groups[material_index].update(weighted_groups)

    component_groups = {}
    for material_index, groups in enumerate(used_groups):
        component_groups.setdefault(find(material_index), set()).update(groups)

    remap_info = []
    for material_index in range(material_count):
        groups = component_groups.get(find(material_index), set())
        bone_names = [
            vertex_group.name
            for vertex_group in object.vertex_groups
            if vertex_group.index in groups
        ]
        if len(bone_names) > 256:
            material_name = object.material_slots[material_index].name
            raise BoneIndexException(
                f"Material bone palette exceeds 256 entries for object "
                f"'{object.name}', material {material_index} ('{material_name}'): "
                f"{len(bone_names)} used vertex groups. First out-of-range "
                f"group: '{bone_names[256]}'."
            )
        remap_info.append(bone_names)

    vertex_to_material_index = [
        min(material_indices) if material_indices else 5000
        for material_indices in vertex_materials
    ]
    return remap_info, vertex_to_material_index


def GetMeshData(og_object, Global_TocManager, Global_BoneNames):
    # Reject authoring-only material names before PrepareMesh creates a
    # disposable mesh or changes Blender mode. A failed save must be retryable.
    for slot in og_object.material_slots:
        try:
            int(slot.name)
        except (ValueError, TypeError):
            raise ValueError(f"材质“{slot.name}”尚未映射到游戏材质 ID，请先完成材质设置")
    global Global_palettepath
    object = PrepareMesh(og_object)
    bpy.context.view_layer.objects.active = object
    mesh = object.data

    vertices    = [ [vert.co[0], vert.co[1], vert.co[2]] for vert in mesh.vertices]
    normals     = [ [vert.normal[0], vert.normal[1], vert.normal[2]] for vert in mesh.vertices]
    tangents    = [ [vert.normal[0], vert.normal[1], vert.normal[2]] for vert in mesh.vertices]
    bitangents  = [ [vert.normal[0], vert.normal[1], vert.normal[2]] for vert in mesh.vertices]
    colors      = [[0,0,0,0] for n in range(len(vertices))]
    uvs         = []
    weights     = [[0,0,0,0] for n in range(len(vertices))]
    boneIndices = []
    faces       = []
    materials   = [ RawMaterialClass() for idx in range(len(object.material_slots))]
    mat_count = {}
    for idx in range(len(object.material_slots)):
        try:
            mat_id = int(object.material_slots[idx].name)
        except:
            raise Exception("Material name must be a number")
        if mat_id not in mat_count:
            mat_count[mat_id] = -1
        mat_count[mat_id] += 1
        materials[idx].IDFromName(og_object['Z_ObjectID'], str(mat_id), mat_count[mat_id])

    # get vertex color
    if mesh.vertex_colors:
        color_layer = mesh.vertex_colors.active
        for face in object.data.polygons:
            if color_layer == None:
                PrettyPrint(f"{og_object.name} Color Layer does not exist", 'ERROR')
                break
            for vert_idx, loop_idx in zip(face.vertices, face.loop_indices):
                col = color_layer.data[loop_idx].color
                colors[vert_idx] = [col[0], col[1], col[2], col[3]]

    # get normals, tangents, bitangents
    #mesh.calc_tangents()
    if hasattr(mesh, "calc_normals_split"):
        if not mesh.has_custom_normals:
            mesh.create_normals_split()
        mesh.calc_normals_split()

    for loop in mesh.loops:
        normals[loop.vertex_index]    = loop.normal.normalized()
        #tangents[loop.vertex_index]   = loop.tangent.normalized()
        #bitangents[loop.vertex_index] = loop.bitangent.normalized()
    # if fuckywuckynormalwormal do this bullshit
    #LoadNormalPalette()
    #normals = NormalsFromPalette(normals)
    # get uvs
    for uvlayer in object.data.uv_layers:
        # if len(uvs) >= 3:
        #     break
        texCoord = [[0,0] for vert in mesh.vertices]
        for face in object.data.polygons:
            for vert_idx, loop_idx in zip(face.vertices, face.loop_indices):
                texCoord[vert_idx] = [uvlayer.data[loop_idx].uv[0], uvlayer.data[loop_idx].uv[1]*-1 + 1]
        uvs.append(texCoord)


    unit_id = int(og_object["Z_ObjectID"])
    stingray_mesh_entry = Global_TocManager.GetEntry(unit_id, int(UnitID), IgnorePatch=False, SearchAll=True)
    if stingray_mesh_entry:
        if not stingray_mesh_entry.IsLoaded: stingray_mesh_entry.Load(True, False)
        stingray_mesh_entry = stingray_mesh_entry.LoadedData
    else:
        raise Exception(f"Unable to get mesh entry {og_object['Z_ObjectID']}")

    # Always keep a pristine archive reference for change detection. The active
    # Patch may be emptied and rebuilt repeatedly, so its current bone table is
    # not a reliable baseline for deciding whether automatic animations are
    # required.
    source_unit_entry = Global_TocManager.GetEntryFromGameArchive(unit_id, UnitID)
    if source_unit_entry is None:
        raise Exception(f"Unable to get original mesh entry {unit_id}")
    if not source_unit_entry.IsLoaded:
        source_unit_entry.Load(True, False)
    source_unit_data = source_unit_entry.LoadedData
    bone_info = stingray_mesh_entry.BoneInfoArray
    transform_info = stingray_mesh_entry.TransformInfo
    light_list = stingray_mesh_entry.LightList
    lod_index = og_object["BoneInfoIndex"]
    bone_entry = Global_TocManager.GetEntry(stingray_mesh_entry.BonesRef, BoneID, IgnorePatch=False, SearchAll=True)
    bone_data = None
    state_machine_data = None
    state_machine_entry = Global_TocManager.GetEntry(stingray_mesh_entry.StateMachineRef, StateMachineID, IgnorePatch=False, SearchAll=True)
    if bone_entry is None:
        PrettyPrint("This unit does not have any animated bone data, unable to edit bone animated state", "warn")
    else:
        if not Global_TocManager.IsInPatch(bone_entry):
            bone_entry = Global_TocManager.AddEntryToPatch(bone_entry.FileID, BoneID)
        if bone_entry:
            if not bone_entry.IsLoaded:
                bone_entry.Load()
            bone_data = bone_entry.LoadedData
    if state_machine_entry is None:
        PrettyPrint("This unit does not have any state machine data, unable to edit bone animated state", "warn")
    else:
        if not Global_TocManager.IsInPatch(state_machine_entry):
            state_machine_entry = Global_TocManager.AddEntryToPatch(state_machine_entry.FileID, StateMachineID)
        if state_machine_entry:
            if not state_machine_entry.IsLoaded:
                state_machine_entry.Load()
            state_machine_data = state_machine_entry.LoadedData


    # get armature object
    prev_obj = bpy.context.view_layer.objects.active
    prev_objs = bpy.context.selected_objects
    prev_mode = prev_obj.mode
    armature_obj = None
    for modifier in og_object.modifiers:
        if modifier.type == "ARMATURE":
            armature_obj = modifier.object
            break
    if armature_obj is not None:
        was_hidden = armature_obj.hide_get()
        state_machine = armature_obj.get("StateMachineID", None)
        armature_obj.hide_set(False)
        bpy.context.view_layer.objects.active = armature_obj
        bpy.ops.object.mode_set(mode='EDIT')
        export_bones = list(armature_obj.data.edit_bones)
        animation_export_bones = export_bones
        if og_object.get("HD2SDK_IndependentExportBonesOnly", False):
            used_bone_names = {
                og_object.vertex_groups[membership.group].name
                for vertex in og_object.data.vertices
                for membership in vertex.groups
                if membership.weight > 0.001
            }
            # A light can be attached to an otherwise unweighted custom bone.
            # Keep that bone and all parents in an independent Unit's closure.
            used_bone_names.update(
                light_object.parent_bone
                for light_object in _armature_lights_for_unit(armature_obj, unit_id)
                if light_object.parent_bone
            )
            # A collider or chain endpoint can have no direct mesh weight but
            # is still required by this particular Unit's physics consumer.
            physical_names = json.loads(og_object.get("HD2SDK_RequiredPhysicsBones", "[]"))
            if not isinstance(physical_names, list) or not all(isinstance(n, str) for n in physical_names):
                raise ValueError("物理骨闭包不是有效骨名列表")
            for name in physical_names:
                if armature_obj.data.edit_bones.get(name) is None:
                    raise ValueError(f"物理项目引用不存在的骨骼：{name}")
            used_bone_names.update(physical_names)
            # Rest-only dependencies must not enlarge Animated membership or
            # trigger automatic animation storage. Keep the former consumer
            # closure for animation-table/baseline handling below.
            animation_export_bones = independent_export_bones(
                armature_obj.data.edit_bones, used_bone_names, "",
            )
            # A manually separated body mesh can have no knee/foot/twist
            # weights. Its runtime IK/grip still needs the same authored public
            # Rest as the other pieces, not leftover native-reference matrices.
            # Structural dependencies do not imply skin weights or physics
            # ownership; unrelated custom chains remain excluded.
            export_bones = independent_export_bones(
                armature_obj.data.edit_bones, used_bone_names,
                og_object.get("HD2BT_PartSlot", ""),
            )
        # --- MAKE CHANGES TO ANIMATED BONE DATA ---
        if bone_data:
            source_bone_entry = Global_TocManager.GetEntryFromGameArchive(
                source_unit_data.BonesRef, BoneID
            )
            if source_bone_entry is None:
                raise Exception(f"Unable to get original bone entry {source_unit_data.BonesRef}")
            if not source_bone_entry.IsLoaded:
                source_bone_entry.Load(False, False)
            archive_source_hashes = list(source_bone_entry.LoadedData.BoneHashes)

            animated_bones = [
                bone for bone in animation_export_bones
                if bone.get('Animated', False)
            ]
            current_animated_hashes = [
                compute_bone_name_hash(bone.name) for bone in animated_bones
            ]
            armature_transform_hashes = {
                compute_bone_name_hash(bone.name)
                for bone in animation_export_bones
            }

            # New imports persist the animation-structure baseline on each mesh
            # object. It contains only membership (Animated hashes and bone
            # hashes), never matrices, so moving/rotating/scaling an existing
            # edit bone cannot trigger automatic animation storage.
            #
            # Older .blend files have no reliable import-time snapshot. Capture
            # their current membership on first use instead of comparing them
            # with an arbitrary currently loaded Patch, which can falsely turn
            # a pose-only edit into an Animated-bone change.
            stored_transform_hashes = og_object.get(
                "HD2SDK_OriginalTransformHashes"
            )
            source_hashes = get_or_initialize_animated_bone_baseline(
                og_object, current_animated_hashes
            )
            if stored_transform_hashes is not None:
                original_transform_hashes = {
                    int(value) for value in json.loads(stored_transform_hashes)
                }
            else:
                original_transform_hashes = set(armature_transform_hashes)
                og_object["HD2SDK_OriginalTransformHashes"] = json.dumps(
                    list(armature_transform_hashes)
                )

            bones_by_hash = {
                compute_bone_name_hash(bone.name): bone for bone in animated_bones
            }
            desired_hashes = set(bones_by_hash)
            old_hashes = list(bone_data.BoneHashes)

            # Original archive order is the stable baseline. Retain selected
            # original animated bones in that order, then append newly animated
            # bones in Blender edit-bone order.
            target_hashes = [
                bone_hash for bone_hash in source_hashes
                if bone_hash in desired_hashes
            ]
            target_hashes.extend(
                compute_bone_name_hash(bone.name)
                for bone in animated_bones
                if compute_bone_name_hash(bone.name) not in source_hashes
            )
            target_names = [bones_by_hash[bone_hash].name for bone_hash in target_hashes]

            animated_bones_changed_from_original = animated_bone_membership_changed(
                current_animated_hashes, source_hashes
            )
            # Automatic animation storage has exactly one trigger: membership
            # of the Animated bone table changed from the import baseline.
            # Mesh edits, weights, materials, edit-bone matrices, and ordinary
            # (non-Animated) custom bones must never create animation entries.
            animation_structure_changed = animated_bones_changed_from_original

            # A modified skeleton needs a complete set of patch animations. Do
            # not use the current Patch as the trigger: after the user removes
            # every stored modification, the same Blender armature must rebuild
            # those animations from the pristine archive on the next save.
            patch_animations_incomplete = False
            if animation_structure_changed and state_machine_data:
                for animation in state_machine_data.animation_ids:
                    patch_animation_entry = Global_TocManager.GetPatchEntry_B(
                        animation, AnimationID
                    )
                    if patch_animation_entry is None:
                        patch_animations_incomplete = True
                        break
                    if not patch_animation_entry.IsLoaded:
                        patch_animation_entry.Load(False, False)
                    patch_animation = patch_animation_entry.LoadedData
                    if (
                        patch_animation.bone_count != len(target_hashes)
                        or len(patch_animation.initial_bone_states) != len(target_hashes)
                    ):
                        patch_animations_incomplete = True
                        break

            animation_sync_required = animation_structure_changed and (
                old_hashes != target_hashes or patch_animations_incomplete
            )

            if not animation_structure_changed:
                PrettyPrint(
                    "Animated bone membership matches the import baseline; "
                    "automatic animation storage skipped"
                )
            elif animation_sync_required:
                PrettyPrint(
                    "Animation structure differs from the import baseline; ensuring saved "
                    "animations contain a complete initial pose"
                )

                # Build every animation in temporary copies first. A failure in
                # one animation must not leave the active patch half-overwritten.
                remapped_animation_entries = []
                if state_machine_data:
                    for animation in state_machine_data.animation_ids:
                        source_animation_entry = Global_TocManager.GetEntryFromGameArchive(
                            animation, AnimationID
                        )
                        if source_animation_entry is None:
                            raise Exception(f"Unable to get original animation {animation}")
                        if not source_animation_entry.IsLoaded:
                            source_animation_entry.Load(False, False)

                        patch_animation_entry = Global_TocManager.GetPatchEntry_B(
                            animation, AnimationID
                        )
                        if patch_animation_entry is not None:
                            if not patch_animation_entry.IsLoaded:
                                patch_animation_entry.Load(False, False)
                            working_entry = deepcopy(patch_animation_entry)
                        else:
                            working_entry = deepcopy(source_animation_entry)

                        state_count = len(working_entry.LoadedData.initial_bone_states)
                        if state_count == len(old_hashes) and state_count > 0:
                            working_source_hashes = old_hashes
                        elif state_count == len(archive_source_hashes):
                            # Repairs a patch produced by the old AQ path: its
                            # animation was reset to original data while its bone
                            # table had already been changed.
                            working_source_hashes = archive_source_hashes
                        elif 0 < state_count < len(archive_source_hashes):
                            # Old AQ builds could append names/hashes without
                            # appending matching initial states. Their existing
                            # states and motion still follow the leading part of
                            # that table, so retain that authored data and create
                            # initial states only for the missing trailing bones.
                            PrettyPrint(
                                f"Repairing animation {animation}: {state_count} initial "
                                f"states for {len(archive_source_hashes)} source bones",
                                "warn"
                            )
                            working_source_hashes = archive_source_hashes[:state_count]
                        else:
                            # A zero-state or otherwise corrupt patch animation
                            # has no reliable index map. Recover from the original
                            # entry rather than writing another malformed file.
                            working_entry = deepcopy(source_animation_entry)
                            working_source_hashes = archive_source_hashes

                        working_entry.LoadedData.remap_bones(
                            working_source_hashes, target_hashes, bones_by_hash
                        )
                        expected_count = len(target_hashes)
                        if len(working_entry.LoadedData.initial_bone_states) != expected_count:
                            raise Exception(
                                f"Animation {animation} initial-state count does not match "
                                f"the target bone table ({expected_count})"
                            )
                        working_entry.Save()
                        remapped_animation_entries.append(working_entry)

                old_weights_by_mask = []
                if state_machine_data and old_hashes != target_hashes:
                    old_weights_by_mask = [
                        dict(zip(old_hashes, blend_mask.bone_weights))
                        for blend_mask in state_machine_data.blend_masks
                    ]

                if old_hashes != target_hashes:
                    bone_data.BoneHashes = list(target_hashes)
                    bone_data.Names = list(target_names)
                    bone_data.NumNames = len(target_names)

                if state_machine_data and old_hashes != target_hashes:
                    for blend_mask, old_weights in zip(
                        state_machine_data.blend_masks, old_weights_by_mask
                    ):
                        blend_mask.bone_weights = [
                            old_weights.get(bone_hash, 0.0) for bone_hash in target_hashes
                        ]
                        blend_mask.bone_count = len(target_hashes)

                for working_entry in remapped_animation_entries:
                    Global_TocManager.AddEntryToPatchID(
                        working_entry, working_entry.FileID, ReloadUI=False
                    )
                if remapped_animation_entries:
                    Global_TocManager.ActivePatch.UpdateTypes()
                if bone_entry and old_hashes != target_hashes:
                    bone_entry.Save()
                if state_machine_entry and old_hashes != target_hashes:
                    state_machine_entry.Save()
            else:
                PrettyPrint(
                    "Modified skeleton already has complete Patch animations; "
                    "preserving the existing animation data"
                )

        # --- END ANIMATED BONE DATA ---

        # Register all custom bones before resolving parents. In a one-pass
        # loop, a child that appears before its custom parent is silently
        # assigned to transform 0.
        for bone in export_bones:
            try:
                name_hash = int(bone.name)
            except ValueError:
                name_hash = murmur32_hash(bone.name.encode("utf-8"))
            if name_hash not in transform_info.NameHashes:
                transform_info.NameHashes.append(name_hash)
                transform_info.TransformMatrices.append(None)
                transform_info.Transforms.append(None)
                l = StingrayLocalTransform()
                l.Incriment = 1
                l.ParentBone = 0
                transform_info.TransformEntries.append(l)
                transform_info.NumTransforms += 1

        for bone in export_bones:
            try:
                name_hash = int(bone.name)
            except ValueError:
                name_hash = murmur32_hash(bone.name.encode("utf-8"))
            transform_index = transform_info.NameHashes.index(name_hash)

            # set bone matrix
            loc, rot, scale = bone.matrix.decompose()
            if transform_info.TransformMatrices[transform_index]:
                scale = transform_info.TransformMatrices[transform_index].ToLocalTransform().scale
            else:
                scale = [1, 1, 1]
            m = mathutils.Matrix.LocRotScale(loc, rot, mathutils.Vector(scale))
            m.transpose()
            transform_matrix = StingrayMatrix4x4()
            transform_matrix.v = [
                m[0][0], m[0][1], m[0][2], m[0][3],
                m[1][0], m[1][1], m[1][2], m[1][3],
                m[2][0], m[2][1], m[2][2], m[2][3],
                m[3][0], m[3][1], m[3][2], m[3][3]
            ]

            # set bone local transform
            transform_info.TransformMatrices[transform_index] = transform_matrix
            if bone.parent:
                parent_matrix = bone.parent.matrix
                local_transform_matrix = parent_matrix.inverted() @ bone.matrix
                translation, rotation, _ = local_transform_matrix.decompose()
                rotation = rotation.to_matrix()
                transform_local = StingrayLocalTransform()
                transform_local.rot.x = [rotation[0][0], rotation[1][0], rotation[2][0]]
                transform_local.rot.y = [rotation[0][1], rotation[1][1], rotation[2][1]]
                transform_local.rot.z = [rotation[0][2], rotation[1][2], rotation[2][2]]
                transform_local.pos = translation
                transform_local.scale = scale
                transform_info.Transforms[transform_index] = transform_local
            else:
                transform_local = StingrayLocalTransform()
                transform_info.Transforms[transform_index] = transform_local

            # set bone parent
            if bone.parent:
                try:
                    parent_name_hash = int(bone.parent.name)
                except ValueError:
                    parent_name_hash = murmur32_hash(bone.parent.name.encode("utf-8"))
                try:
                    parent_transform_index = transform_info.NameHashes.index(parent_name_hash)
                    transform_info.TransformEntries[transform_index].ParentBone = parent_transform_index
                except ValueError:
                    PrettyPrint(f"Failed to parent bone: {bone.name}.", 'warn')

        # Light objects are regular bone-parented Blender objects; collecting
        # them in Object mode avoids another fragile edit-mode context switch.
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
            _sync_hd2_lights_from_armature(
                armature_obj, transform_info, light_list, unit_id
            )
        finally:
            if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            armature_obj.hide_set(was_hidden)
            for obj in list(bpy.context.selected_objects):
                obj.select_set(False)
            for obj in prev_objs:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = prev_obj
            if prev_obj is not None and prev_mode != "OBJECT":
                bpy.ops.object.mode_set(mode=prev_mode)


    # get weights
    vert_idx = 0
    numInfluences = 4
    if not bpy.context.scene.Hd2ToolPanelSettings.LegacyWeightNames:
        if len(object.vertex_groups) > 0:
            remap_info, vertex_to_material_index = build_material_bone_remaps(
                object, mesh
            )
            bone_info[lod_index].SetRemap(remap_info, transform_info)
        else:
            vertex_to_material_index = [5000 for _ in mesh.vertices]
    else:
        vertex_to_material_index = [0 for _ in mesh.vertices]
        for polygon in mesh.polygons:
            for vertex_index in polygon.vertices:
                vertex_to_material_index[vertex_index] = polygon.material_index

    if len(object.vertex_groups) > 0:
        for index, vertex in enumerate(mesh.vertices):
            group_idx = 0
            retained_bone_keys = ['', '', '', '']
            for group in vertex.groups:
                # limit influences
                if group_idx >= numInfluences:
                    break
                if group.weight > 0.001:
                    vertex_group        = object.vertex_groups[group.group]
                    vertex_group_name   = vertex_group.name

                    #
                    # CHANGE THIS TO SUPPORT THE NEW BONE NAMES
                    # HOW TO ACCESS transform_info OF STINGRAY MESH??
                    if bpy.context.scene.Hd2ToolPanelSettings.LegacyWeightNames:
                        parts               = vertex_group_name.split("_")
                        HDGroupIndex        = int(parts[0])
                        HDBoneIndex         = int(parts[1])
                    else:
                        material_idx = vertex_to_material_index[index]
                        try:
                            name_hash = int(vertex_group_name)
                        except ValueError:
                            name_hash = murmur32_hash(vertex_group_name.encode("utf-8"))
                        HDGroupIndex = 0
                        try:
                            real_index = transform_info.NameHashes.index(name_hash)
                        except ValueError:
                            existing_names = []
                            for i, h in enumerate(transform_info.NameHashes):
                                try:
                                    if i in bone_info[lod_index].RealIndices:
                                        existing_names.append(Global_BoneNames[h])
                                except KeyError:
                                    existing_names.append(str(h))
                                except IndexError:
                                    pass
                            if object:
                                PrettyPrint(f"Deleting object early and exiting weight painting mode...", 'error')
                                bpy.ops.object.mode_set(mode='OBJECT')
                                bpy.data.objects.remove(object, do_unlink=True)
                            raise Exception(f"\n\nVertex Group: {vertex_group_name} is not a valid vertex group for the model.\nIf you are using legacy weight names, make sure you enable the option in the settings.\n\nValid vertex group names: {existing_names}")
                        try:
                            HDBoneIndex = bone_info[lod_index].GetRemappedIndex(real_index, material_idx)
                        except (ValueError, IndexError): # bone index not in remap because the bone is not in the LOD bone data
                            continue

                    # get real index from remapped index -> hashIndex = bone_info[mesh.LodIndex].GetRealIndex(bone_index); boneHash = transform_info.NameHashes[hashIndex]
                    # want to get remapped index from bone name
                    # hash = ...
                    # real_index = transform_info.NameHashes.index(hash)
                    # remap = bone_info[mesh.LodIndex].GetRemappedIndex(real_index)
                    if HDGroupIndex+1 > len(boneIndices):
                        dif = HDGroupIndex+1 - len(boneIndices)
                        boneIndices.extend([[[0,0,0,0] for n in range(len(vertices))]]*dif)
                    boneIndices[HDGroupIndex][vert_idx][group_idx] = HDBoneIndex
                    weights[vert_idx][group_idx] = group.weight
                    # Ordinary names are stable across independently built
                    # palettes. Resolve legacy palette-local names to the same
                    # global transform identity before using them for ties.
                    if bpy.context.scene.Hd2ToolPanelSettings.LegacyWeightNames:
                        material_idx = vertex_to_material_index[index]
                        real = bone_info[lod_index].GetRealIndex(HDBoneIndex, material_idx)
                        retained_bone_keys[group_idx] = str(transform_info.NameHashes[real])
                    else:
                        retained_bone_keys[group_idx] = str(name_hash)
                    group_idx += 1
            if group_idx:
                # Only authored/rebuilt vertices enter this path. Leave raw
                # native helper streams and unsupported/empty weights alone;
                # existing export validation still rejects invalid bindings.
                weights[vert_idx] = normalize_half4(weights[vert_idx], retained_bone_keys)
            vert_idx += 1
    else:
        boneIndices = []
        weights     = []

    # set bone matrices in bone index mappings
    # matrices in bone_info are the inverted joint matrices (for some reason)
    # and also relative to the mesh transform
    if lod_index != -1:
        mesh_info_index = og_object["MeshInfoIndex"]
        mesh_info = stingray_mesh_entry.MeshInfoArray[mesh_info_index]
        origin_transform_matrix = transform_info.TransformMatrices[mesh_info.TransformIndex].ToBlenderMatrix().inverted()
        for i, transform_index in enumerate(bone_info[lod_index].RealIndices):
            bone_matrix = transform_info.TransformMatrices[transform_index]
            m = (origin_transform_matrix @ bone_matrix.ToBlenderMatrix()).inverted().transposed()
            transform_matrix = StingrayMatrix4x4()
            transform_matrix.v = [
                m[0][0], m[0][1], m[0][2], m[0][3],
                m[1][0], m[1][1], m[1][2], m[1][3],
                m[2][0], m[2][1], m[2][2], m[2][3],
                m[3][0], m[3][1], m[3][2], m[3][3]
            ]
            bone_info[lod_index].Bones[i] = transform_matrix

        if og_object.get("HD2SDK_IndependentExportBonesOnly", False):
            # The authored Rest updates shared native nodes too. Untouched
            # helper vertices keep their native encoding, but their separate
            # skin palettes must bind against the final Rest, not the old one.
            helper_origins = {lod_index: mesh_info.TransformIndex}
            for helper in stingray_mesh_entry.RawMeshes:
                helper_lod = int(helper.LodIndex)
                if helper_lod < 0 or not helper.CanPreserveNativeStream():
                    continue
                helper_info = stingray_mesh_entry.MeshInfoArray[helper.MeshInfoIndex]
                origin = helper_info.TransformIndex
                if helper_lod in helper_origins and helper_origins[helper_lod] != origin:
                    raise ValueError("Shared helper BoneInfo has incompatible mesh origins")
                helper_origins[helper_lod] = origin
                origin_world = transform_info.TransformMatrices[origin].ToBlenderMatrix()
                for i, joint in enumerate(bone_info[helper_lod].RealIndices):
                    if not 0 <= joint < len(transform_info.TransformMatrices):
                        raise ValueError("Native helper bone index out of range")
                    bind = (transform_info.TransformMatrices[joint].ToBlenderMatrix().inverted() @ origin_world).transposed()
                    if any(not math.isfinite(float(value)) for row in bind for value in row):
                        raise ValueError("Native helper inverse bind is non-finite")
                    matrix = StingrayMatrix4x4()
                    matrix.v = [value for row in bind for value in row]
                    bone_info[helper_lod].Bones[i] = matrix

    #bpy.ops.object.mode_set(mode='OBJECT')
    # get faces
    temp_faces = [[] for n in range(len(object.material_slots))]
    for f in mesh.polygons:
        temp_faces[f.material_index].append([f.vertices[0], f.vertices[1], f.vertices[2]])
        materials[f.material_index].NumIndices += 3
    for tmp in temp_faces: faces.extend(tmp)

    NewMesh = RawMeshClass()
    NewMesh.VertexPositions     = vertices
    NewMesh.VertexNormals       = normals
    #NewMesh.VertexTangents      = tangents
    #NewMesh.VertexBiTangents    = bitangents
    NewMesh.VertexColors        = colors
    NewMesh.VertexUVs           = uvs
    NewMesh.VertexWeights       = weights
    NewMesh.VertexBoneIndices   = boneIndices
    NewMesh.Indices             = faces
    NewMesh.Materials           = materials
    NewMesh.MeshInfoIndex       = og_object["MeshInfoIndex"]
    NewMesh.DEV_BoneInfoIndex   = og_object["BoneInfoIndex"]
    NewMesh.LodIndex            = og_object["BoneInfoIndex"]
    if len(vertices) > 0xffff: NewMesh.DEV_Use32BitIndices = True
    matNum = 0
    for material in NewMesh.Materials:
        try:
            material.DEV_BoneInfoOverride = int(og_object[f"matslot{matNum}"])
        except: pass
        matNum += 1

    if object is not None and object.name:
        PrettyPrint(f"Removing {object.name}")
        bpy.data.objects.remove(object, do_unlink=True)
    else:
        PrettyPrint(f"Current object: {object}")
    return NewMesh

def GetObjectsMeshData(Global_TocManager, Global_BoneNames):
    objects = bpy.context.selected_objects
    bpy.ops.object.select_all(action='DESELECT')
    data = {}
    for object in objects:
        if object.type != 'MESH':
            continue
        ID = object["Z_ObjectID"]
        MeshData = GetMeshData(object, Global_TocManager, Global_BoneNames)
        try:
            data[ID][MeshData.MeshInfoIndex] = MeshData
        except:
            data[ID] = {MeshData.MeshInfoIndex: MeshData}
    return data

def NameFromMesh(mesh, id, customization_info, bone_names, use_sufix=True):
    # generate name
    name = str(id)
    if customization_info.BodyType != "":
        BodyType    = customization_info.BodyType.replace("HelldiverCustomizationBodyType_", "")
        Slot        = customization_info.Slot.replace("HelldiverCustomizationSlot_", "")
        Weight      = customization_info.Weight.replace("HelldiverCustomizationWeight_", "")
        PieceType   = customization_info.PieceType.replace("HelldiverCustomizationPieceType_", "")
        name = Slot+"_"+PieceType+"_"+BodyType
    name_sufix = "_lod"+str(mesh.LodIndex)
    if mesh.LodIndex == -1:
        name_sufix = "_mesh"+str(mesh.MeshInfoIndex)
    if mesh.IsCullingBody():
        name_sufix = "_culling"+str(mesh.MeshInfoIndex)
    if use_sufix: name = name + name_sufix

    if use_sufix and bone_names != None:
        for bone_name in bone_names:
            if murmur32_hash(bone_name.encode()) == mesh.MeshID:
                name = bone_name + name_sufix

    return name

def CreateModel(
    stingray_unit, id, Global_BoneNames, Global_NameHashes, bones_entry,
    state_machine_entry, source_bones_entry=None
):
    addon_prefs = AQ_PublicClass.get_addon_prefs()
    model, customization_info, bone_names, transform_info, bone_info = stingray_unit.RawMeshes, stingray_unit.CustomizationInfo, stingray_unit.BoneNames, stingray_unit.TransformInfo, stingray_unit.BoneInfoArray

    StaticMeshCount = 0
    created_mesh_objects = []
    light_import_target = None

    # Animated import state is always defined by the base game, regardless of
    # whether this call merges into an existing armature, creates a new one,
    # or was initiated while a Mesh (rather than its armature) was selected.
    # A user Patch may supply Mesh bytes, but it must never seed Blender's
    # Animated flags or the new import baseline.
    if source_bones_entry is not None:
        bones_entry = source_bones_entry

    # A separate CreateModel call is a new import operation, not another LOD
    # pass of the previous import. If MergeArmatures reuses the selected old
    # skeleton, first restore this Unit's existing bones from the source bone
    # table. The per-mesh loop below can then merge all LOD information again.
    initial_skeleton_object = None
    if (
        bpy.context.scene.Hd2ToolPanelSettings.ImportArmature
        and bpy.context.scene.Hd2ToolPanelSettings.MergeArmatures
        and len(bpy.context.selected_objects) > 0
        and bpy.context.selected_objects[0].type == "ARMATURE"
    ):
        initial_skeleton_object = bpy.context.selected_objects[0]
    if initial_skeleton_object is not None:
        # A reimport of an existing Blender skeleton is a reset operation. Use
        # the base-game bone table for both the reset and all following LOD
        # passes; otherwise the per-mesh loop would immediately re-enable the
        # stale flag from a user Patch after resetting it.
        source_animated_hashes = (
            list(bones_entry.BoneHashes) if bones_entry is not None else []
        )
        reset_reimported_animated_bones(
            initial_skeleton_object,
            transform_info.NameHashes,
            source_animated_hashes,
            Global_BoneNames,
        )

    if len(model) < 1: return
    # Make collection
    old_collection = bpy.context.collection
    if addon_prefs.MakeCollections:
        new_collection = bpy.data.collections.new(NameFromMesh(model[0], id, customization_info, bone_names, False))
        old_collection.children.link(new_collection)
    else:
        new_collection = old_collection
    # Make Meshes
    for mesh in model:
        # check lod
        if not bpy.context.scene.Hd2ToolPanelSettings.ImportLods and mesh.IsLod():
            continue
        # check physics
        if not bpy.context.scene.Hd2ToolPanelSettings.ImportCulling and mesh.IsCullingBody():
            continue
        if not addon_prefs.ImportStatic and mesh.IsStaticMesh():
            StaticMeshCount += 1 # 统计静态网格数量
            if StaticMeshCount == len(model): # 如果所有网格都是静态网格，则抛出提示异常
                raise AQ_StaticMeshError("网格全部为静态网格，开启导入静态网格后再导入！")
            continue
        # do safety check
        for face in mesh.Indices:
            for index in face:
                if index > len(mesh.VertexPositions):
                    raise Exception("Bad Mesh Parse: indices do not match vertices")
        # generate name
        if addon_prefs.DisplayFriendlyName_Mesh_Skel:
            friendlyName = GetFriendlyNameFromID(id, Global_NameHashes)
            if len(friendlyName) >= 55: # 超过了blender的最大名称长度63
                friendlyName = friendlyName.split("/")[-1]
        else:
            friendlyName = id
        name = NameFromMesh(mesh, friendlyName, customization_info, bone_names)
        # create mesh
        new_mesh = bpy.data.meshes.new(name)
        #new_mesh.from_pydata(mesh.VertexPositions, [], [])
        new_mesh.from_pydata(mesh.VertexPositions, [], mesh.Indices)
        new_mesh.update()
        # make object from mesh
        new_object = bpy.data.objects.new(name, new_mesh)
        # set transform
        translation, rotation, scale = mesh.DEV_Transform.decompose()
        new_object.scale = scale
        new_object.location = translation
        new_object.rotation_mode = 'QUATERNION'
        new_object.rotation_quaternion = rotation
        #local_transform = mesh.DEV_Transform
        #new_object.scale = local_transform.scale
        #new_object.location = local_transform.pos
        #new_object.rotation_mode = 'QUATERNION'
        #new_object.rotation_quaternion = mathutils.Matrix([local_transform.rot.x, local_transform.rot.y, local_transform.rot.z]).to_quaternion()

        # set object properties
        new_object["MeshInfoIndex"] = mesh.MeshInfoIndex
        new_object["BoneInfoIndex"] = mesh.LodIndex
        new_object["Z_ObjectID"]    = str(id)
        new_object["Z_SwapID_0"]    = ""
        new_object["Z_SwapID_1"]    = ""
        new_object["Z_SwapID_2"]    = ""
        new_object["Z_SwapID_3"]    = ""
        new_object["Z_SwapID_4"]    = ""
        new_object["HD2SDK_OriginalTransformHashes"] = json.dumps(
            list(transform_info.NameHashes)
        )
        if bones_entry:
            new_object["HD2SDK_OriginalAnimatedBoneHashes"] = json.dumps(
                list(bones_entry.BoneHashes)
            )
        new_object["HD2SDK_AnimationStructureBaselineVersion"] = 1
        if customization_info.BodyType != "":
            new_object["Z_CustomizationBodyType"] = customization_info.BodyType
            new_object["Z_CustomizationSlot"]     = customization_info.Slot
            new_object["Z_CustomizationWeight"]   = customization_info.Weight
            new_object["Z_CustomizationPieceType"]= customization_info.PieceType
        if mesh.IsCullingBody():
            new_object.display_type = 'WIRE'

        # add object to scene collection
        new_collection.objects.link(new_object)
        created_mesh_objects.append(new_object)
        # -- || ASSIGN NORMALS || -- #
        if len(mesh.VertexNormals) == len(mesh.VertexPositions):
            if hasattr(new_mesh, "use_auto_smooth"):
                new_mesh.use_auto_smooth = True
            new_mesh.shade_smooth()

            new_mesh.polygons.foreach_set('use_smooth',  [True] * len(new_mesh.polygons))
            if not isinstance(mesh.VertexNormals[0], int):
                new_mesh.normals_split_custom_set_from_vertices(mesh.VertexNormals)


        # -- || ASSIGN VERTEX COLORS || -- #
        if len(mesh.VertexColors) == len(mesh.VertexPositions):
            color_layer = new_mesh.vertex_colors.new()
            for face in new_mesh.polygons:
                for vert_idx, loop_idx in zip(face.vertices, face.loop_indices):
                    color_layer.data[loop_idx].color = (mesh.VertexColors[vert_idx][0], mesh.VertexColors[vert_idx][1], mesh.VertexColors[vert_idx][2], mesh.VertexColors[vert_idx][3])
        # -- || ASSIGN UVS || -- #
        for uvs in mesh.VertexUVs:
            uvlayer = new_mesh.uv_layers.new()
            new_mesh.uv_layers.active = uvlayer
            for face in new_mesh.polygons:
                for vert_idx, loop_idx in zip(face.vertices, face.loop_indices):
                    uvlayer.data[loop_idx].uv = (uvs[vert_idx][0], uvs[vert_idx][1]*-1 + 1)
        # -- || ASSIGN WEIGHTS || -- #
        created_groups = []
        available_bones = []
        for i, h in enumerate(transform_info.NameHashes):
            try:
                if i in bone_info[mesh.LodIndex].RealIndices:
                    available_bones.append(Global_BoneNames.get(h, str(h)))
            except IndexError:
                pass
        vertex_to_material_index = [5000]*len(mesh.VertexPositions)
        for mat_idx, mat in enumerate(mesh.Materials):
            for face in mesh.Indices[mat.StartIndex//3:(mat.StartIndex//3+mat.NumIndices//3)]:
                for vert_idx in face:
                    vertex_to_material_index[vert_idx] = mat_idx
        for vertex_idx in range(len(mesh.VertexWeights)):
            weights      = mesh.VertexWeights[vertex_idx]
            index_groups = [Indices[vertex_idx] for Indices in mesh.VertexBoneIndices]
            for group_index, indices in enumerate(index_groups):
                if bpy.context.scene.Hd2ToolPanelSettings.ImportGroup0 and group_index != 0:
                    continue
                if type(weights) != list:
                    weights = [weights]
                for weight_idx in range(len(weights)):
                    weight_value = weights[weight_idx]
                    bone_index   = indices[weight_idx]
                    if not bpy.context.scene.Hd2ToolPanelSettings.LegacyWeightNames:
                        try:
                            hashIndex = bone_info[mesh.LodIndex].GetRealIndex(bone_index, vertex_to_material_index[vertex_idx])
                        except:
                            continue
                        boneHash = transform_info.NameHashes[hashIndex]
                        group_name = Global_BoneNames.get(boneHash, str(boneHash))
                    else:
                        group_name = str(group_index) + "_" + str(bone_index)
                    if group_name not in created_groups:
                        created_groups.append(group_name)
                        try:
                            available_bones.remove(group_name)
                        except ValueError:
                            pass
                        new_vertex_group = new_object.vertex_groups.new(name=str(group_name))
                    vertex_group_data = [vertex_idx]
                    new_object.vertex_groups[str(group_name)].add(vertex_group_data, weight_value, 'ADD')
        if not bpy.context.scene.Hd2ToolPanelSettings.LegacyWeightNames:
            for bone in available_bones:
                new_vertex_group = new_object.vertex_groups.new(name=str(bone))

        # -- || ADD BONES || -- #
        skeletonObj = None
        armature = None
        if bpy.context.scene.Hd2ToolPanelSettings.ImportArmature and not bpy.context.scene.Hd2ToolPanelSettings.LegacyWeightNames:
            if len(bpy.context.selected_objects) > 0:
                skeletonObj = bpy.context.selected_objects[0]
            if skeletonObj and skeletonObj.type == 'ARMATURE':
                armature = skeletonObj.data
            if bpy.context.scene.Hd2ToolPanelSettings.MergeArmatures and armature != None:
                PrettyPrint(f"Merging to previous skeleton: {skeletonObj.name}")
            else:
                PrettyPrint(f"Creating New Skeleton")
                armature = bpy.data.armatures.new(f"{id}_skeleton")
                armature.display_type = "OCTAHEDRAL"
                armature.show_names = False
                skeletonObj = bpy.data.objects.new(f"{friendlyName}_rig", armature)
                skeletonObj['BonesID'] = str(stingray_unit.BonesRef)
                skeletonObj['StateMachineID'] = str(stingray_unit.StateMachineRef)
                skeletonObj.show_in_front = True

            if addon_prefs.MakeCollections:
                if 'skeletons' not in bpy.data.collections:
                    collection = bpy.data.collections.new("skeletons")
                    bpy.context.scene.collection.children.link(collection)
                else:
                    collection = bpy.data.collections['skeletons']
            else:
                collection = bpy.context.collection

            try:
                collection.objects.link(skeletonObj)
            except Exception as e:
                PrettyPrint(f"{e}", 'warn')

            #bpy.context.active_object = skeletonObj
            bpy.context.view_layer.objects.active = skeletonObj
            bpy.ops.object.mode_set(mode='EDIT')
            bones = None
            boneParents = None
            boneTransforms = {}
            boneMatrices = {}
            doPoseBone = {}
            if mesh.LodIndex in [-1, 0]:
                bones = [None] * transform_info.NumTransforms
                boneParents = [0] * transform_info.NumTransforms
                for i, transform in enumerate(transform_info.TransformEntries):
                    boneParent = transform.ParentBone
                    boneHash = transform_info.NameHashes[i]
                    if boneHash in Global_BoneNames: # name of bone
                        boneName = Global_BoneNames[boneHash]
                    else:
                        boneName = str(boneHash)
                    animated = False
                    ragdoll = False
                    ragdoll_params = []
                    if bones_entry and boneName in bones_entry.Names:
                        animated = True
                        bone_index = bones_entry.Names.index(boneName)
                    try:
                        b = int(boneName)
                        if bones_entry and b in bones_entry.BoneHashes:
                            animated = True
                    except ValueError:
                        pass
                    newBone = armature.edit_bones.get(boneName)
                    if newBone is None:
                        newBone = armature.edit_bones.new(boneName)
                        newBone.tail = 0, 0.05, 0
                        if bones_entry: newBone['Animated'] = animated
                        doPoseBone[newBone.name] = True
                    else:
                        doPoseBone[newBone.name] = False
                    # A lower LOD may create this shared bone before the LOD
                    # that identifies it as animated. Existing bones must gain
                    # the flag as later meshes contribute their information.
                    if bones_entry and animated:
                        newBone['Animated'] = True
                    bones[i] = newBone
                    boneParents[i] = boneParent
                    boneTransforms[newBone.name] = transform_info.Transforms[i]
                    boneMatrices[newBone.name] = transform_info.TransformMatrices[i]
            else:
                b_info = bone_info[mesh.LodIndex]
                bones = [None] * b_info.NumBones
                boneParents = [0] * b_info.NumBones
                for i, bone in enumerate(b_info.Bones): # this is not every bone in the transform_info
                    boneIndex = b_info.RealIndices[i] # index of bone in transform info
                    boneParent = transform_info.TransformEntries[boneIndex].ParentBone # index of parent bone in transform info
                    # index of parent bone in b_info.Bones?
                    if boneParent in b_info.RealIndices:
                        boneParentIndex = b_info.RealIndices.index(boneParent)
                    else:
                        boneParentIndex = -1
                    boneHash = transform_info.NameHashes[boneIndex]
                    if boneHash in Global_BoneNames: # name of bone
                        boneName = Global_BoneNames[boneHash]
                    else:
                        boneName = str(boneHash)
                    animated = False
                    if bones_entry and boneName in bones_entry.Names:
                        animated = True
                    try:
                        b = int(boneName)
                        if bones_entry and b in bones_entry.BoneHashes:
                            animated = True
                    except ValueError:
                        pass
                    newBone = armature.edit_bones.get(boneName)
                    if newBone is None:
                        newBone = armature.edit_bones.new(boneName)
                        newBone.tail = 0, 0.05, 0
                        if bones_entry: newBone['Animated'] = animated
                        doPoseBone[newBone.name] = True
                    else:
                        doPoseBone[newBone.name] = False
                    if bones_entry and animated:
                        newBone['Animated'] = True
                    bones[i] = newBone
                    boneTransforms[newBone.name] = transform_info.Transforms[boneIndex]
                    boneMatrices[newBone.name] = transform_info.TransformMatrices[boneIndex]
                    boneParents[i] = boneParentIndex

            # parent all bones
            for i, bone in enumerate(bones):
                if boneParents[i] > -1:
                    bone.parent = bones[boneParents[i]]

            # pose all bones
            bpy.context.view_layer.objects.active = skeletonObj

            for i, bone in enumerate(armature.edit_bones):
                try:
                    if not doPoseBone[bone.name]: continue
                    a = boneMatrices[bone.name]
                    mat = mathutils.Matrix.Identity(4)
                    mat[0] = a.v[0:4]
                    mat[1] = a.v[4:8]
                    mat[2] = a.v[8:12]
                    mat[3] = a.v[12:16]
                    mat.transpose()
                    bone.matrix = mat
                except Exception as e:
                    PrettyPrint(f"Failed setting bone matricies for: {e}. This may be intended", 'warn')

            bpy.ops.object.mode_set(mode='OBJECT')

            # assign armature modifier to the mesh object
            modifier = new_object.modifiers.get("ARMATURE")
            if (modifier == None):
                modifier = new_object.modifiers.new("Armature", "ARMATURE")
                modifier.object = skeletonObj

            if bpy.context.scene.Hd2ToolPanelSettings.ParentArmature:
                new_object.parent = skeletonObj

            # select the armature at the end so we can chain import when merging
            for obj in bpy.context.selected_objects:
                obj.select_set(False)
            skeletonObj.select_set(True)

            # create empty animation data if it does not exist
            if not skeletonObj.animation_data:
              skeletonObj.animation_data_create()

            candidate = (len(skeletonObj.data.bones), skeletonObj, collection)
            if light_import_target is None or candidate[0] > light_import_target[0]:
                light_import_target = candidate

        # -- || ASSIGN MATERIALS || -- #
        # convert mesh to bmesh
        bm = bmesh.new()
        bm.from_mesh(new_object.data)
        # assign materials
        matNum = 0
        goreIndex = None
        for material in mesh.Materials:
            if str(material.MatID) == "12070197922454493211":
                goreIndex = matNum
                PrettyPrint(f"Found gore material at index: {matNum}")
            # append material to slot
            try:
                new_object.data.materials.append(bpy.data.materials[material.MatID])
            except Exception:
                # raise Exception(f"Tool was unable to find material that this mesh uses, ID: {material.MatID}")
                PrettyPrint(f"Tool was unable to find material that this mesh uses, ID: {material.MatID}")
                # 未找到材质直接新建
                AddMaterialToBlend_EMPTY(material.MatID)
                # 再次添加
                try:
                    new_object.data.materials.append(bpy.data.materials[material.MatID])
                except:
                    raise Exception(f"Tool was unable to find material that this mesh uses, ID: {material.MatID}")
            # assign material to faces
            numTris    = int(material.NumIndices/3)
            StartIndex = int(material.StartIndex/3)
            for f in bm.faces[StartIndex:(numTris+(StartIndex))]:
                f.material_index = matNum
            matNum += 1
        # remove gore mesh
        if bpy.context.scene.Hd2ToolPanelSettings.RemoveGoreMeshes and goreIndex:
            PrettyPrint(f"Removing Gore Mesh")
            verticies = []
            for vert in bm.verts:
                if len(vert.link_faces) == 0:
                    continue
                if vert.link_faces[0].material_index == goreIndex:
                    verticies.append(vert)
            for vert in verticies:
                bm.verts.remove(vert)

        # convert bmesh to mesh
        bm.to_mesh(new_object.data)
        bm.free()
        #平滑着色
        addon_prefs = AQ_PublicClass.get_addon_prefs()
        if addon_prefs.ShadeSmooth:
            if hasattr(new_mesh, "use_auto_smooth"):
                new_mesh.use_auto_smooth = False
            new_mesh.shade_smooth()

    # Import lights once, after every LOD has contributed its bones. When the
    # add-on creates separate LOD armatures, use the most complete skeleton.
    if light_import_target is not None:
        _, light_armature, light_collection = light_import_target
        _import_hd2_lights(
            stingray_unit.LightList,
            light_armature,
            transform_info,
            light_collection,
            id,
        )

    # The trustworthy baseline must be captured only after every mesh/LOD has
    # finished creating and merging its shared armature. Writing bones_entry
    # earlier is merely provisional and caused mesh-only first saves to create
    # animations when that table differed from the final Blender flags.
    for imported_object in created_mesh_objects:
        imported_armature = None
        for modifier in imported_object.modifiers:
            if modifier.type == "ARMATURE" and modifier.object is not None:
                imported_armature = modifier.object
                break
        if imported_armature is None:
            continue
        imported_animated_hashes = [
            compute_bone_name_hash(bone.name)
            for bone in imported_armature.data.bones
            if bone.get("Animated", False)
        ]
        imported_object["HD2SDK_OriginalAnimatedBoneHashes"] = json.dumps(
            imported_animated_hashes
        )
        imported_object["HD2SDK_AnimationStructureBaselineVersion"] = (
            ANIMATED_BONE_BASELINE_VERSION
        )

def GetFriendlyNameFromID(ID, NameHashes):
    try:
        hash_info_name = NameHashes[int(ID)]
        if hash_info_name != "":
            return str(hash_info_name)
    except KeyError:
        pass
    return str(ID)
