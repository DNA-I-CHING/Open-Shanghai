import struct, re, sys

p = sys.argv[1]
out = sys.argv[2]
d = open(p, 'rb').read()
HDR = 65  # bytes from 'MESH: ' tag start to index array

lines = ['# converted from .mox (pos+normal+uv)']
vi = 1
count = 0
for m in re.finditer(b'MESH: \x00', d):
    i = m.start()
    nv, nidx = struct.unpack_from('<ii', d, i+7)
    vs = i + HDR
    idx = struct.unpack_from('<%dH' % nidx, d, vs)
    vstart = vs + nidx*2
    if max(idx) >= nv:
        print('BAD indices at', hex(i)); continue
    data = [struct.unpack_from('<8f', d, vstart + k*32) for k in range(nv)]
    lines.append('o mesh_%d' % count)
    for x, y, z, nx, ny, nz, u, v in data:
        lines.append('v %.6f %.6f %.6f' % (x, y, z))
    for x, y, z, nx, ny, nz, u, v in data:
        lines.append('vt %.6f %.6f' % (u, 1.0 - v))  # OBJ/V 与引擎 UV 翻转惯例
    for x, y, z, nx, ny, nz, u, v in data:
        lines.append('vn %.6f %.6f %.6f' % (nx, ny, nz))
    if nidx % 3 == 0:
        for k in range(0, nidx, 3):
            a, b, c = idx[k], idx[k+1], idx[k+2]
            lines.append('f %d/%d/%d %d/%d/%d %d/%d/%d' % (
                vi+a, vi+a, vi+a, vi+b, vi+b, vi+b, vi+c, vi+c, vi+c))
    else:
        for k in range(nidx-2):
            a = idx[k]; b = idx[k+1-(k % 2)*2]; c = idx[k+2-(k % 2)*2]
            lines.append('f %d/%d/%d %d/%d/%d %d/%d/%d' % (
                vi+a+1, vi+a+1, vi+a+1, vi+b+1, vi+b+1, vi+b+1, vi+c+1, vi+c+1, vi+c+1))
    vi += nv
    count += 1
open(out, 'w').write('\n'.join(lines) + '\n')
print('meshes:', count, '->', out)
