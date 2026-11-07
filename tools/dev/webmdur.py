import json,sys,struct,re
d=sys.argv[1]
j=json.load(open(d+'/index.json'))
f=open(d+'/romfs.bin','rb')
for v in j['files']:
    k=v.get('path') or v.get('name')
    if not k.endswith('.webm'): continue
    f.seek(v['offset']); b=f.read(1<<16)
    scale=1000000; dur=None
    i=b.find(b'\x2a\xd7\xb1')
    if i>=0:
        n=b[i+3]&0x7f; scale=int.from_bytes(b[i+4:i+4+n],'big')
    i=b.find(b'\x44\x89')
    if i>=0:
        n=b[i+2]&0x7f
        dur=struct.unpack('>d' if n==8 else '>f', b[i+3:i+3+n])[0]
    codecs=sorted(set(m.decode() for m in re.findall(rb'[AV]_[A-Z0-9/_]{2,20}',b)))
    print('%-38s %7.1f MB  %s s  %s' % (k.split('/')[-1], v['size']/1e6, 'n/a' if dur is None else '%.1f'%(dur*scale/1e9), ' '.join(codecs)))

