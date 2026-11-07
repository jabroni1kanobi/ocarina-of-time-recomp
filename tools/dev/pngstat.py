import sys, zlib, struct, glob, os
for p in sorted(glob.glob(sys.argv[1]+'/*.png')):
    b=open(p,'rb').read(); i=8; idat=b''; w=h=0; ct=0
    while i<len(b):
        n=struct.unpack('>I',b[i:i+4])[0]; t=b[i+4:i+8]; c=b[i+8:i+8+n]
        if t==b'IHDR': w,h,bd,ct=struct.unpack('>IIBB',c[:10])
        if t==b'IDAT': idat+=c
        i+=12+n
    raw=zlib.decompress(idat); bpp=4 if ct==6 else 3; st=w*bpp+1
    rs=[0]*bpp; nz=0
    for y in range(0,h,7):
        row=raw[y*st+1:(y+1)*st]
        if raw[y*st]!=0: continue
        for x in range(0,w,7):
            px=row[x*bpp:(x+1)*bpp]
            for k in range(bpp): rs[k]=max(rs[k],px[k])
    print(os.path.basename(p), w,h,'ct',ct,'max',rs)
