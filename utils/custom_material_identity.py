"""Strict v179 custom-material identities, independent of shipped templates.

This module only transforms bytes in memory. It accepts the explicitly parsed
88-byte group / 1040-byte instance / 544-byte program / six-stage v179 layout.
Unknown layouts fail with ValueError; no byte-pattern search identifies fields.
The container stores only a material ID's high32 tag. Full64 IDs are supplied
by the owning archive entry and retained in manifests and snapshots.
"""
from collections import Counter, OrderedDict
import hashlib
import math
import struct

__all__ = ['build_identity_manifest', 'rebind_gpu_identity', 'rebind_existing_gpu',
           'capture_gpu_identity', 'restore_gpu_identity']

_KIND = 'custom_material_v179'
_FAMILIES = ('stage_name_fields', 'group_name_fields')
_CHECKSUM_CACHE = OrderedDict()
_MASK = 0xffffffff
_MD5_K = tuple(int(abs(math.sin(i+1))*2**32)&_MASK for i in range(64))
_MD5_SHIFT = (7,12,17,22)*4+(5,9,14,20)*4+(4,11,16,23)*4+(6,10,15,21)*4


def _need(condition, message):
    if not condition:
        raise ValueError('custom material identity: '+message)


def _uint(value, bits=32):
    _need(type(value) is int and 0 <= value < (1 << bits), 'expected uint%d' % bits)
    return value


def _material_id(value):
    _uint(value,64)
    _need(value >> 32 != 0, 'material ID requires a nonzero high32 tag')
    return value


def _bytes(value):
    _need(isinstance(value,(bytes,bytearray,memoryview)), 'GPU must be bytes')
    return bytes(value)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _align(value, width=8):
    return (value+width-1)&~(width-1)


class _Reader:
    def __init__(self,data):
        self.data=data
        self.size=len(data)

    def span(self,at,size,limit=None):
        stop=self.size if limit is None else min(limit,self.size)
        _need(type(at) is int and type(size) is int and 0 <= at <= stop and 0 <= size <= stop-at,
              'out-of-bounds span at %r, size %r' % (at,size))
        return self.data[at:at+size]

    def u32(self,at,limit=None):
        return struct.unpack('<I',self.span(at,4,limit))[0]

    def unpack(self,fmt,at,limit=None):
        return struct.unpack(fmt,self.span(at,struct.calcsize(fmt),limit))

    def zeros(self,start,end,limit=None):
        _need(not any(self.span(start,end-start,limit)), 'nonzero alignment/reserved bytes at %d' % start)


def _dxbc_checksum(blob):
    """Microsoft retail DXBC hash: MD5 rounds with the DXBC final block."""
    data=blob[20:]
    size=len(data);last=size//64*64;tail=data[last:]
    blocks=[data[i:i+64] for i in range(0,last,64)]
    if len(tail)>=56:
        blocks.append(tail+b'\x80'+bytes(63-len(tail)))
        final=struct.pack('<I',(size*8)&_MASK)+bytes(56)+struct.pack('<I',((size*2)|1)&_MASK)
    else:
        final=struct.pack('<I',(size*8)&_MASK)+tail+b'\x80'+bytes(55-len(tail))+struct.pack('<I',((size*2)|1)&_MASK)
    blocks.append(final)
    state=[0x67452301,0xefcdab89,0x98badcfe,0x10325476]
    for block in blocks:
        words=struct.unpack('<16I',block);a,b,c,d=state
        for i in range(64):
            if i<16:f=(b&c)|(~b&d);g=i
            elif i<32:f=(d&b)|(~d&c);g=(5*i+1)%16
            elif i<48:f=b^c^d;g=(3*i+5)%16
            else:f=c^(b|~d);g=(7*i)%16
            v=(a+f+_MD5_K[i]+words[g])&_MASK;s=_MD5_SHIFT[i]
            a,d,c,b=d,c,b,(b+((v<<s)|(v>>(32-s))))&_MASK
        state=[(x+y)&_MASK for x,y in zip(state,(a,b,c,d))]
    return struct.pack('<4I',*state)


def _check_dxbc(blob,slot):
    r=_Reader(blob)
    _need(len(blob)>=32 and blob[:4]==b'DXBC', 'stage is not a DXBC container')
    _need(r.u32(20)==1 and r.u32(24)==len(blob), 'DXBC header/length mismatch')
    count=r.u32(28)
    _need(0 < count <= 256, 'unsupported DXBC chunk count')
    table_end=32+4*count;r.span(0,table_end)
    regions=[];kinds=set();shader=None
    for i in range(count):
        at=r.u32(32+4*i)
        _need(at%4==0 and at>=table_end, 'DXBC chunk overlaps directory')
        tag=r.span(at,4);size=r.u32(at+4);payload=r.span(at+8,size)
        _need(tag not in kinds, 'duplicate DXBC chunk')
        kinds.add(tag);regions.append((at,at+8+size))
        if tag in (b'SHEX',b'SHDR'):
            _need(shader is None and size>=8 and size%4==0, 'invalid shader code chunk')
            version,length=struct.unpack_from('<2I',payload)
            _need(length*4==size and ((version>>4)&15)==5, 'unsupported shader model or code length')
            shader=version>>16
    regions.sort()
    _need(all(a[1]<=b[0] for a,b in zip(regions,regions[1:])), 'overlapping DXBC chunks')
    # Engine record order is VS, DS, HS, GS, PS, CS (DS precedes HS).
    _need(shader=={0:1,1:4,2:3,3:2,4:0,5:5}[slot], 'shader type differs from stage slot')
    digest=_sha(blob)
    if digest not in _CHECKSUM_CACHE:
        _need(_dxbc_checksum(blob)==blob[4:20], 'DXBC checksum mismatch')
        _CHECKSUM_CACHE[digest]=True
        if len(_CHECKSUM_CACHE)>512:_CHECKSUM_CACHE.popitem(last=False)
    return digest


def _prefix(r,h,fronts):
    """Account for every CB, pool and instance descriptor before the group table."""
    end=h[3];pos=48+88*h[11]
    def take(size):
        nonlocal pos
        at=pos;r.span(pos,size,end);pos+=size;return at
    def pad():
        nonlocal pos
        stop=_align(pos);r.zeros(pos,stop,end);pos=stop
    for index,f in enumerate(fronts):
        at=f['offset']
        npool,ncb,nsecond,nperm=[r.u32(at+k) for k in (16,40,56,80)]
        _need(0<npool<10000 and 0<ncb<1000 and nsecond<1000 and 0<nperm<1000,
              'unsupported prefix counts')
        base=take(ncb*24)
        headers=[r.unpack('<QQII',base+24*j,end) for j in range(ncb)]
        _need(all(row[0]==0 and row[1]<10000 for row in headers), 'unsupported CB descriptor layout')
        pbase=take(nperm*1040)
        counts=[r.u32(pbase+1040*j+32,end) for j in range(nperm)]
        _need(all(n<10000 for n in counts), 'unsupported instance variable count')
        second=take(nsecond*24)
        r.zeros(second,pos,end)
        take(npool*12);pad()
        for row in headers:take(row[1]*20);pad()
        for j,n in enumerate(counts):
            take(n*20)
            if index != len(fronts)-1 or j != len(counts)-1:pad()
        if index==len(fronts)-1 and pos!=end:
            _need(end==_align(pos),'unexpected trailing prefix bytes');pad()
    _need(pos==end and h[10]+h[9]==end, 'prefix boundary/tail pointer mismatch')


def _groups(r,h,fronts):
    start=h[7];end=start+h[8]
    _need(r.u32(start)==1 and r.u32(start+4)==h[1], 'nested material tag mismatch')
    ntech=r.u32(start+20,end)
    _need(0<ntech<1000,'unsupported technique count')
    pos=_align(start+28);r.zeros(start+28,pos,end)
    techniques=[]
    for i in range(ntech):
        at=pos+24*i;r.span(at,24,end)
        r.zeros(at+4,at+16,end);r.zeros(at+20,at+24,end)
        number=r.u32(at+16,end)
        _need(number<1000,'unsupported technique group count')
        techniques.append(number)
    pos+=24*ntech
    groups=[]
    for number in techniques:
        pending=[]
        for i in range(number):
            at=pos+24*i;r.span(at,24,end)
            r.zeros(at+4,at+16,end);r.zeros(at+20,at+24,end)
            pending.append((r.u32(at,end),r.u32(at+16,end),at))
        pos+=24*number
        for value,size,at in pending:
            _need(value!=0,'zero group identity')
            r.span(pos,size,end);pos+=size
            groups.append(dict(value=value,offset=at))
    _need(pos==end and len(groups)==len(fronts), 'nested group count/end mismatch')
    _need(Counter(f['value'] for f in fronts)==Counter(g['value'] for g in groups),
          'front/nested group equivalence mismatch')
    return groups


def _programs(r,h,fronts):
    start=h[5];end=h[2]
    r.span(start,104,end)
    _need(r.u32(start+8)==len(fronts),'program group count mismatch')
    table=_align(start+104);r.zeros(start+104,table,end)
    r.span(table,len(fronts)*16,end)
    counts=[r.u32(table+16*i,end) for i in range(len(fronts))]
    _need(counts==[f['permutations'] for f in fronts], 'prefix/program permutation mismatch')
    pos=table+16*len(fronts)-8;stages=[];records=0
    for bi,number in enumerate(counts):
        _need(0<number<1000,'unsupported program permutation count')
        heads=[]
        for pi in range(number):
            r.span(pos,544,end);r.zeros(pos,pos+8,end)
            heads.append(pos+8);pos+=544
        for pi,head in enumerate(heads):
            state_begin=pos;state_units=r.u32(head,end)
            _need(0<state_units<1000,'invalid state-area length')
            state_end=pos+16*state_units;r.span(pos,16*state_units,end)
            n=r.u32(pos,state_end)
            _need(n<1000,'invalid state preamble count')
            r.span(pos,4+4*n,state_end)
            nxt=_align(pos+4+4*n);r.zeros(pos+4+4*n,nxt,state_end);pos=nxt
            terminated=False
            for _ in range(1000):
                value,reserved,key=r.unpack('<iIQ',pos,state_end);pos+=16
                _need(reserved==0,'unsupported state attribute reserved value')
                if value==-1:
                    _need(key==107,'unsupported state terminator');terminated=True;break
            _need(terminated and pos+8==state_end,'state length/terminator mismatch')
            r.zeros(pos,pos+8,state_end);pos=state_end
            ntex=r.u32(head+16,end)
            _need(ntex<1000,'unsupported texture count')
            samplers=[]
            for i in range(ntex):
                zero,ns,name,zero2=r.unpack('<QQII',pos+24*i,end)
                _need(zero==zero2==0 and ns<1000,'unsupported texture descriptor')
                samplers.append(ns)
            pos+=24*ntex
            r.span(pos,16*sum(samplers),end);pos+=16*sum(samplers)
            for slot in range(6):
                header=head+32+80*slot
                size,name=r.u32(header,end),r.u32(header+4,end)
                at=pos;blob=r.span(at,size,end);tail=at+size
                _need(r.u32(tail,end)==name,'stage header/trailer identity mismatch')
                counts2=[r.u32(header+k,end) for k in (16,32,48,64)]
                _need(all(n<1000 for n in counts2),'unsupported stage binding count')
                if size:
                    _need(name!=0,'zero nonempty stage identity')
                    digest=_check_dxbc(blob,slot)
                    stages.append(dict(value=name,offsets=[header+4,tail],
                        dxbc_offset=at,dxbc_size=size,dxbc_sha256=digest,slot=slot))
                else:
                    _need(name==0 and not any(counts2),'nonempty metadata on an empty stage')
                pos=_align(tail+4);r.zeros(tail+4,pos,end)
                length=16*sum(counts2[:3])+12*counts2[3]
                r.span(pos,length,end);mend=pos+length;pos=_align(mend);r.zeros(mend,pos,end)
            records+=1
    _need(pos==end,'program byte count mismatch')
    _need(h[6]==end-start,'program header length mismatch')
    _need(r.size-end in (8,16),'unsupported footer length')
    r.zeros(end,r.size)
    _need(stages,'material has no executable stages')
    return stages,records


def _identity_offsets(manifest):
    offsets=list(manifest['material_tag_offsets'])
    for family in _FAMILIES:
        for field in manifest[family]:offsets.extend(field['offsets'])
    return sorted(offsets)


def _normalized_hash(gpu,manifest):
    data=bytearray(gpu)
    for off in _identity_offsets(manifest):struct.pack_into('<I',data,off,0)
    return _sha(data)


def build_identity_manifest(gpu,material_id,*,expected_sha256=None):
    """Parse and validate a known v179 layout; return compatible identity data."""
    data=_bytes(gpu);_material_id(material_id);digest=_sha(data)
    if expected_sha256 is not None:
        _need(expected_sha256==digest,'GPU hash mismatch')
    r=_Reader(data);h=r.unpack('<12I',0)
    _need(h[0]==179 and h[4]==1,'unsupported GPU container version/layout')
    _need(h[1]==material_id>>32,'outer material tag differs from owning resource')
    _need(0<h[11]<1000,'unsupported group count')
    front_end=48+88*h[11]
    _need(front_end<=h[3] and h[3]+8==h[7] and h[7]+h[8]<=h[5]<h[2]<=len(data),
          'invalid GPU section boundaries')
    _need(h[5] in (h[7]+h[8],_align(h[7]+h[8],4),_align(h[7]+h[8])),
          'unsupported group-table alignment')
    r.zeros(h[7]+h[8],h[5])
    _need(r.unpack('<Q',h[3])[0]==0x209fb8c3c0a8c3a4,'missing nested group-table tag')
    fronts=[]
    for i in range(h[11]):
        at=48+88*i;r.span(at,88,h[3]);value=r.u32(at)
        _need(value!=0,'zero front group identity')
        fronts.append(dict(value=value,offset=at,permutations=r.u32(at+80)))
    _prefix(r,h,fronts);groups=_groups(r,h,fronts);stages,records=_programs(r,h,fronts)
    families={};content={}
    stage_fields={}
    for stage in stages:
        value=stage['value']
        _need(content.setdefault(value,stage['dxbc_sha256'])==stage['dxbc_sha256'],
              'one stage identity refers to different shader bytes')
        stage_fields.setdefault(value,[]).extend(stage['offsets'])
    group_fields={}
    for group in fronts+groups:group_fields.setdefault(group['value'],[]).append(group['offset'])
    for key,entries in zip(_FAMILIES,(stage_fields,group_fields)):
        families[key]=[dict(value=value,offsets=sorted(ats)) for value,ats in sorted(entries.items())]
    manifest=dict(schema_version=1,kind=_KIND,template_material_id='%016x'%material_id,
        gpu_version=179,material_tag_offsets=[4,h[7]+4],gpu_sha256=digest,
        reserved_names=sorted(set(stage_fields)|set(group_fields)),
        **families)
    locations=_identity_offsets(manifest)
    _need(len(locations)==len(set(locations)) and all(a+4<=b for a,b in zip(locations,locations[1:])),
          'overlapping identity fields')
    for stage in stages:
        _need(not any(at<stage['dxbc_offset']+stage['dxbc_size'] and at+4>stage['dxbc_offset'] for at in locations),
              'identity field overlaps shader bytecode')
    manifest['identity_normalized_sha256']=_normalized_hash(data,manifest)
    manifest['structure']=dict(groups=len(fronts),permutations=records,dxbc_count=len(stages),
        unique_programs=len(stage_fields),group_classes=len(group_fields),gpu_bytes=len(data),
        program_start=h[5],program_end=h[2],all_dxbc_checksums_valid=True)
    manifest['dxbc_regions']=[dict(offset=s['dxbc_offset'],size=s['dxbc_size'],sha256=s['dxbc_sha256']) for s in stages]
    return manifest


def _field_topology(manifest,family):
    fields=manifest.get(family)
    _need(isinstance(fields,list) and fields,'missing identity family')
    answer={};values=set()
    for field in fields:
        _need(isinstance(field,dict),'invalid identity class')
        value=_uint(field.get('value'))
        offsets=field.get('offsets')
        _need(value!=0 and value not in values,'duplicate/zero identity class')
        _need(isinstance(offsets,list) and offsets and all(type(x) is int and x>=0 for x in offsets),
              'invalid identity offset list')
        ats=tuple(sorted(offsets))
        _need(len(ats)==len(set(ats)) and ats not in answer,'duplicate identity offsets')
        values.add(value);answer[ats]=value
    return answer


def _checked_manifest(gpu,manifest):
    _need(isinstance(manifest,dict) and manifest.get('schema_version')==1,'unsupported identity manifest')
    try:old_id=int(manifest['template_material_id'],16)
    except (KeyError,TypeError,ValueError):raise ValueError('custom material identity: malformed manifest material ID')
    current=build_identity_manifest(gpu,old_id,expected_sha256=manifest.get('gpu_sha256'))
    _need(manifest.get('gpu_sha256')==current['gpu_sha256'],'missing manifest GPU hash')
    _need(manifest.get('material_tag_offsets')==current['material_tag_offsets'],'material-tag offset mismatch')
    for family in _FAMILIES:
        _need(_field_topology(manifest,family)==_field_topology(current,family),'manifest field inventory mismatch')
    reserved=manifest.get('reserved_names',[])
    _need(isinstance(reserved,list),'invalid reserved names')
    for value in reserved:_uint(value)
    current['reserved_names']=sorted(set(current['reserved_names'])|set(reserved))
    return current,old_id


def _patch(gpu,manifest,tag,values):
    out=bytearray(gpu)
    for at in manifest['material_tag_offsets']:struct.pack_into('<I',out,at,tag)
    for family in _FAMILIES:
        for offsets,value in values[family].items():
            for at in offsets:struct.pack_into('<I',out,at,value)
    result=bytes(out)
    # Compare exact bytecode slices, not merely a count or a pattern scan.
    for region in manifest['dxbc_regions']:
        at=region['offset'];end=at+region['size']
        _need(result[at:end]==gpu[at:end],'shader bytecode changed during identity operation')
    _need(_normalized_hash(result,manifest)==manifest['identity_normalized_sha256'],
          'non-identity bytes changed')
    return result


def rebind_gpu_identity(gpu,manifest,new_id):
    """Rekey only structurally parsed identities; validate again after writing."""
    data=_bytes(gpu);_material_id(new_id);current,old_id=_checked_manifest(data,manifest)
    if new_id==old_id:return data
    reserved=set(current['reserved_names']);replacements={}
    for family in _FAMILIES:
        values={}
        for offsets,old in sorted(_field_topology(current,family).items(),key=lambda row:row[1]):
            for nonce in range(10000):
                seed=('custom-v179:%016x:%s:%08x:%d'%(new_id,family,old,nonce)).encode('ascii')
                value=int.from_bytes(hashlib.sha256(seed).digest()[:4],'little')
                if value and value not in reserved:break
            else:raise ValueError('custom material identity: collision retry limit')
            reserved.add(value);values[offsets]=value
        replacements[family]=values
    result=_patch(data,current,new_id>>32,replacements)
    final=build_identity_manifest(result,new_id)
    _need(final['identity_normalized_sha256']==current['identity_normalized_sha256'],
          'rebound GPU changed non-identity data')
    return result


def rebind_existing_gpu(gpu,old_id,new_id):
    return rebind_gpu_identity(gpu,build_identity_manifest(gpu,old_id),new_id)


def capture_gpu_identity(gpu,material_id):
    """Capture an exact, self-contained identity snapshot for export/undo."""
    manifest=build_identity_manifest(gpu,material_id)
    return dict(schema_version=1,kind=_KIND,material_id='%016x'%material_id,
        gpu_sha256=manifest['gpu_sha256'],identity_normalized_sha256=manifest['identity_normalized_sha256'],
        material_tag_offsets=manifest['material_tag_offsets'],
        stage_name_fields=manifest['stage_name_fields'],group_name_fields=manifest['group_name_fields'])


def restore_gpu_identity(gpu,material_id,snapshot,restored_id):
    """Restore only when structure and every non-identity byte are unchanged."""
    data=_bytes(gpu);_material_id(restored_id)
    _need(isinstance(snapshot,dict) and snapshot.get('schema_version')==1 and snapshot.get('kind')==_KIND,
          'unsupported custom identity snapshot')
    _need(snapshot.get('material_id')=='%016x'%restored_id,'snapshot material ID mismatch')
    current=build_identity_manifest(data,material_id)
    _need(snapshot.get('material_tag_offsets')==current['material_tag_offsets'],'snapshot material tag locations mismatch')
    _need(snapshot.get('identity_normalized_sha256')==current['identity_normalized_sha256'],
          'snapshot differs in non-identity bytes')
    values={}
    for family in _FAMILIES:
        before=_field_topology(snapshot,family);after=_field_topology(current,family)
        _need(before.keys()==after.keys(),'snapshot equivalence classes differ')
        values[family]=before
    result=_patch(data,current,restored_id>>32,values)
    _need(_sha(result)==snapshot.get('gpu_sha256'),'restore did not reproduce original GPU hash')
    build_identity_manifest(result,restored_id)
    return result
