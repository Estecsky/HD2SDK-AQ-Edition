"""Build preview witnesses from FINAL Unit palettes and the matching consumer Rig.

No shader bytecode or vertex data is shipped. Unknown geometry stays untouched.
"""
import hashlib
import math
import struct
import zlib
from .physics_compiler import _runtime_stem
from .rig_format import _flags, TARGET_FLAGS
from .unit_rig_profiles import _multiply, _inverse_rigid

HEADER=struct.Struct('<8s6I64sQ')
REFERENCE=struct.Struct('<96sQ32s')
SPEC=struct.Struct('<QQ4IQQ32s32sI')
BONE=struct.Struct('<Ii24d')
MAX_BYTES=16*1024*1024


def _fixed(text,size):
    data=text.encode('ascii')
    if not data or len(data)>=size or any(c not in b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in data):
        raise ValueError('预览配套身份或文件名无效')
    return data.ljust(size,b'\0')


def _matrix(value):
    v=list(value.v)
    result=[v[c*4+r] for r in range(3) for c in range(4)]
    if not all(math.isfinite(x) for x in result):raise ValueError('预览骨矩阵非有限')
    return result


def _slice(data,offset,size):
    if offset<0 or size<0 or offset>len(data) or size>len(data)-offset:raise ValueError('预览几何数据越界')
    return data[offset:offset+size]


def _fnv(data):
    value=14695981039346656037
    for b in data:value=((value^b)*1099511628211)&0xffffffffffffffff
    return value


def compile_preview(plan, rig, entries, rig_name, rig_blob):
    project, stem=_runtime_stem(plan)
    archive=int(stem.rsplit('.',1)[1],16)
    records=[]
    for profile in rig['profiles']:
        uid=int(profile['unit_id'],16)
        entry=entries[uid];unit=entry.LoadedData;gpu=bytes(entry.GpuData)
        transforms=unit.TransformInfo
        rests=[_matrix(m) for m in transforms.TransformMatrices]
        targets={b['palette_slot']:b for b in profile['target_bones']}
        for mesh in unit.MeshInfoArray:
            # This transport currently understands GPU-resident skinned streams.
            # Other layouts continue through the ordinary runtime untouched.
            if mesh.LodIndex<0 or mesh.StreamIndex>1:continue
            stream=unit.StreamInfoArray[mesh.StreamIndex]
            if stream.IndexBuffer_Type not in (0,1):continue
            info=unit.BoneInfoArray[mesh.LodIndex]
            size=2 if stream.IndexBuffer_Type==0 else 4
            for section in mesh.Sections:
                count=section.NumIndices;stride=stream.VertexStride
                witness=min(128,section.NumVertices*stride)
                if count<24 or count*size>512*1024 or witness<32:continue
                indices=_slice(gpu,stream.IndexBufferOffset+section.IndexOffset*size,count*size)
                vertices=_slice(gpu,stream.VertexBufferOffset+section.VertexOffset*stride,witness)
                remap=info.Remaps[section.MaterialIndex]
                if not 1<=len(remap)<=256:raise ValueError('预览骨调色板数量无效')
                bones=[]
                unsupported=False
                for real in remap:
                    if not 0<=real<len(info.RealIndices) or real>=len(info.Bones):raise ValueError('预览 remap 越界')
                    node=info.RealIndices[real]
                    if node not in targets:
                        unsupported=True;break
                    ancestor=node;visited=set();source=-1
                    while ancestor not in visited:
                        if not 0<=ancestor<len(rests):raise ValueError('预览骨父级越界')
                        visited.add(ancestor);target=targets.get(ancestor)
                        if target and _flags(target.get('flags'),TARGET_FLAGS,'preview')&1024:
                            source=target['source_index'];break
                        parent=transforms.TransformEntries[ancestor].ParentBone
                        if parent==ancestor:break
                        ancestor=parent
                    else:raise ValueError('预览骨父链循环')
                    offset=_multiply(_inverse_rigid(rests[ancestor]),rests[node]) if source>=0 else rests[node]
                    bones.append(BONE.pack(node,source,*offset,*_matrix(info.Bones[real])))
                if unsupported:continue
                records.append(SPEC.pack(archive,uid,count,size,stride,witness,_fnv(indices),_fnv(vertices),
                    hashlib.sha256(indices).digest(),hashlib.sha256(vertices).digest(),len(bones))+b''.join(bones))
    if not records:return None
    if len(records)>1024:raise ValueError('预览几何规格超过 1024 项')
    # Profile enumeration can differ between save memory and cold Archive
    # readback. The public companion must be byte-deterministic in either path.
    records.sort()
    payload=REFERENCE.pack(_fixed(rig_name,96),len(rig_blob),hashlib.sha256(rig_blob).digest())+b''.join(records)
    if HEADER.size+len(payload)>MAX_BYTES:raise ValueError('预览配套超过 16 MiB')
    blob=HEADER.pack(b'HD2PRV1\0',1,HEADER.size,HEADER.size+len(payload),zlib.crc32(payload),len(records),0,
        _fixed(project,64),archive)+payload
    return stem+'.hd2preview',blob
