"""Crop a region of uncompressed/compressed 8-bit PNGs and write it 2x enlarged: crop.py in.png out.png x y w h"""
import sys, zlib, struct
def read(p):
    b=open(p,'rb').read(); i=8; idat=b''
    while i<len(b):
        n=struct.unpack('>I',b[i:i+4])[0]; t=b[i+8-4:i+8]
        c=b[i+8:i+8+n]
        if t==b'IHDR': w,h,bd,ct=struct.unpack('>IIBB',c[:10])
        if t==b'IDAT': idat+=c
        i+=12+n
    raw=zlib.decompress(idat); bpp={6:4,2:3,0:1,4:2}[ct]*(bd//8); st=w*bpp+1
    rows=[]; prev=bytearray(st-1)
    for y in range(h):
        f=raw[y*st]; line=bytearray(raw[y*st+1:(y+1)*st])
        for x in range(len(line)):
            a=line[x-bpp] if x>=bpp else 0; b_=prev[x]; c_=prev[x-bpp] if x>=bpp else 0
            if f==1: line[x]=(line[x]+a)&255
            elif f==2: line[x]=(line[x]+b_)&255
            elif f==3: line[x]=(line[x]+((a+b_)>>1))&255
            elif f==4:
                p=a+b_-c_; pa=abs(p-a); pb=abs(p-b_); pc=abs(p-c_)
                line[x]=(line[x]+(a if pa<=pb and pa<=pc else b_ if pb<=pc else c_))&255
        rows.append(line); prev=line
    return w,h,bpp,rows
def write(p,w,h,rows):
    raw=b''.join(b'\0'+bytes(r) for r in rows)
    def ch(t,d): return struct.pack('>I',len(d))+t+d+struct.pack('>I',zlib.crc32(t+d)&0xffffffff)
    open(p,'wb').write(b'\x89PNG\r\n\x1a\n'+ch(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+ch(b'IDAT',zlib.compress(raw,6))+ch(b'IEND',b''))
w,h,bpp,rows=read(sys.argv[1]); x0,y0,cw,chh=map(int,sys.argv[3:7]); sc=int(sys.argv[7]) if len(sys.argv)>7 else 2
out=[]
for y in range(y0,y0+chh):
    r=rows[y]; line=bytearray()
    for x in range(x0,x0+cw):
        px=r[x*bpp:x*bpp+3]; line+=px*sc
    for _ in range(sc): out.append(line)
write(sys.argv[2],cw*sc,chh*sc,out)
