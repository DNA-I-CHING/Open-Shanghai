import struct, re, sys

p = sys.argv[1]
out = sys.argv[2]
d = open(p, 'rb').read()
HDR = 65  # bytes from 'MESH: ' tag start to index array

lines = ['# converted from .mox']
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
    verts = [struct.unpack_from('<3f', d, vstart + k*32) for k in range(nv)]
    lines.append('o mesh_%d' % count)
    for x, y, z in verts:
        lines.append('v %.6f %.6f %.6f' % (x, y, z))
    if nidx % 3 == 0:  # triangle list
        for k in range(0, nidx, 3):
            lines.append('f %d %d %d' % (vi+idx[k], vi+idx[k+1], vi+idx[k+2]))
    else:  # triangle strip: flip winding every other tri
        for k in range(nidx-2):
            a, b, c = idx[k], idx[k+1-(k % 2)*2], idx[k+2-(k % 2)*2]
            lines.append('f %d %d %d' % (vi+a+1, vi+b+1, vi+c+1))
    vi += nv
    count += 1
open(out, 'w').write('\n'.join(lines) + '\n')
print('meshes:', count, '->', out)
