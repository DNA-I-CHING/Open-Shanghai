# -*- coding: utf-8 -*-
"""
mox_anim.py - 《血战上海滩》Enya 引擎 .mox 全功能转换器（含动画）
================================================================
二进制格式（字节级验证, Ghidra: FRAME::Read 0x4AEC10, MESH 头实测）:

MOXB
FRAME 树 (根节点从偏移 4 开始, 递归):
  "FRAME: " <名称>\0
  u32 FrameCount
  FrameCount x 64B  4x4 float32 矩阵 (DirectX 行主序, 平移在第4行)
                    ※ 实测为烘焙的世界变换 (根级骨骼平移=世界坐标)
  u32 BoundingBoxNum
  u32 MeshCount
    每个 MESH:
      "MESH: " <名称>\0
      u32 VertCount, u32 IndexCount(u16个数), u32 VertFrameCount, u32 TriangleList
      u32 Flags (实测 0x280000) ... 头部共 65B (从 tag 起算, 空名称时)
      IndexCount x u16 索引
      VertFrameCount x VertCount x 32B  顶点记录(pos3+normal3+uv2 float32)
        ※ VertFrameCount==1 时第 0 条即静态顶点; >1 时为逐帧顶点形变
      "TEXTURE: " <文件名>\0
      "MATERIAL: " <名称>\0 + 73B 材质数据
  u32 ChildCount
    ChildCount 个递归 FRAME

动画机制 ("半硬动画"):
  - 骨骼节点: FrameCount>1 时每帧一个世界矩阵 (刚性)
  - 软体部件: MESH VertFrameCount>1 时顶点逐帧形变 (如通气管 1911 帧)

用法:
  python mox_anim.py 输入.mox --glb 输出.glb [--fps 30] [--morph-targets 100]
  python mox_anim.py 输入.mox --obj-dir OBJ序列目录 [--obj-step 32]
  python mox_anim.py 输入.mox --obj 静态.obj          (第0帧)
"""
import struct, json, math, sys, os, argparse

HDR = 65          # MESH 头部长度 (tag 起算, 名称常为空串)
MAT_PAYLOAD = 69  # MATERIAL: \0 之后的固定材质字节数 (之后紧跟 FRAME 的 ChildCount)


# ---------------------------------------------------------------- 解析
class Mesh:
    def __init__(self):
        self.name = ''
        self.nv = 0
        self.nidx = 0
        self.vframes = 1        # VertFrameCount 字段 (+15)
        self.trilist = 1
        self.flags = 0
        self.indices = []
        self.verts0 = []        # 第0帧 (nv x 8f: x,y,z,nx,ny,nz,u,v)
        self.frames = []        # 逐帧顶点 (vframes-1 条, 每条 nv x 8f); 静态网格为空
        self.texture = ''
        self.material_raw = b''


class Node:
    def __init__(self):
        self.name = ''
        self.mats = []          # FrameCount x 16f (DirectX 行主序)
        self.bbox_num = 0
        self.meshes = []
        self.children = []
        self.parent = None


def _cstr(d, p):
    e = d.index(0, p)
    return d[p:e].decode('gbk', 'replace'), e + 1


def parse_mesh(d, p):
    assert d[p:p + 6] == b'MESH: ', 'MESH 标签错误 @%x' % p
    m = Mesh()
    m.name, p = _cstr(d, p + 6)
    m.nv, m.nidx, m.vframes, m.trilist, m.flags = struct.unpack_from('<5I', d, p)
    vs = p + 58  # 5个u32字段(20B)+38B杂项 = tag起算65B(名称空时, 实测)
    m.indices = struct.unpack_from('<%dH' % m.nidx, d, vs)
    p = vs + m.nidx * 2
    rec = lambda q: struct.unpack_from('<8f', d, q)
    m.verts0 = [rec(p + k * 32) for k in range(m.nv)]
    p += m.nv * 32
    for f in range(m.vframes - 1):
        m.frames.append([rec(p + k * 32) for k in range(m.nv)])
        p += m.nv * 32
    assert d[p:p + 9] == b'TEXTURE: ', 'TEXTURE 标签错误 @%x' % p
    m.texture, p = _cstr(d, p + 9)
    assert d[p:p + 10] == b'MATERIAL: ', 'MATERIAL 标签错误 @%x' % p
    m.name2, p = _cstr(d, p + 10)
    m.material_raw = d[p:p + MAT_PAYLOAD]
    p += MAT_PAYLOAD
    if max(m.indices) >= m.nv:
        raise ValueError('索引越界 mesh %r' % m.name)
    return m, p


def parse_frame(d, p, parent=None):
    assert d[p:p + 7] == b'FRAME: ', 'FRAME 标签错误 @%x' % p
    n = Node()
    n.parent = parent
    n.name, p = _cstr(d, p + 7)
    fc = struct.unpack_from('<I', d, p)[0]
    p += 4
    n.mats = [struct.unpack_from('<16f', d, p + f * 64) for f in range(fc)]
    p += fc * 64
    n.bbox_num = struct.unpack_from('<I', d, p)[0]
    p += 4
    for _ in range(n.bbox_num):
        # 二进制 bbox 条目: 16B 定长 GBK 名称('BB_...') + 152B 碰撞载荷 (共168B)
        n.bbox_name = d[p:p + 16].split(b'\x00')[0].decode('gbk', 'replace')
        p += 168
    mn = struct.unpack_from('<I', d, p)[0]
    p += 4
    for _ in range(mn):
        m, p = parse_mesh(d, p)
        n.meshes.append(m)
    cn = struct.unpack_from('<I', d, p)[0]
    p += 4
    for _ in range(cn):
        c, p = parse_frame(d, p, n)
        n.children.append(c)
    return n, p


def parse_mox(path):
    d = open(path, 'rb').read()
    assert d[:4] == b'MOXB', '非二进制 MOXB 文件'
    root, p = parse_frame(d, 4)
    if p != len(d):
        raise ValueError('解析未对齐: 消耗 %d / 文件 %d' % (p, len(d)))
    return d, root


def walk(node):
    yield node
    for c in node.children:
        yield from walk(c)


# ---------------------------------------------------------------- 矩阵
def mat_math(m):
    """DirectX 行主序 16f -> (R3x3数学约定=行主序转置, t)。v' = R·v + t"""
    r = [[m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]]]
    R = [[r[0][0], r[1][0], r[2][0]],
         [r[0][1], r[1][1], r[2][1]],
         [r[0][2], r[1][2], r[2][2]]]
    t = (m[12], m[13], m[14])
    return R, t


def det3(R):
    return (R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1])
            - R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0])
            + R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0]))


def quat_from_mat(R):
    # Shepperd 方法, R 为数学约定旋转矩阵
    m00, m01, m02 = R[0]
    m10, m11, m12 = R[1]
    m20, m21, m22 = R[2]
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s
    return (x, y, z, w)


def apply(m16, v):
    R, t = mat_math(m16)
    return tuple(R[i][0] * v[0] + R[i][1] * v[1] + R[i][2] * v[2] + t[i] for i in range(3))


def apply_n(m16, n):
    R, _ = mat_math(m16)
    return tuple(R[i][0] * n[0] + R[i][1] * n[1] + R[i][2] * n[2] for i in range(3))


# ---------------------------------------------------------------- GLB 导出
class GLBBuilder:
    def __init__(self):
        self.json = {'asset': {'version': '2.0', 'generator': 'mox_anim.py'},
                     'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [],
                     'meshes': [], 'materials': [], 'buffers': [], 'bufferViews': [],
                     'accessors': [], 'animations': []}
        self.bin = bytearray()

    def buf(self, data, target=None):
        while len(self.bin) & 3:
            self.bin.append(0)
        off = len(self.bin)
        self.bin += data
        bv = len(self.json['bufferViews'])
        self.json['bufferViews'].append({'buffer': 0, 'byteOffset': off,
                                         'byteLength': len(data),
                                         **({'target': target} if target else {})})
        return bv

    def acc(self, bv, ctype, count, comps, mins=None, maxs=None):
        a = {'bufferView': bv, 'componentType': ctype, 'count': count, 'type': comps}
        if mins is not None:
            a['min'], a['max'] = mins, maxs
        self.json['accessors'].append(a)
        return len(self.json['accessors']) - 1

    def node(self, **kw):
        self.json['nodes'].append(kw)
        return len(self.json['nodes']) - 1

    def save(self, path):
        self.json['buffers'] = [{'byteLength': len(self.bin)}]
        bj = json.dumps(self.json, separators=(',', ':')).encode('utf-8')
        while len(bj) & 3:
            bj += b' '
        while len(self.bin) & 3:
            self.bin.append(0)
        total = 12 + 8 + len(bj) + 8 + len(self.bin)
        with open(path, 'wb') as f:
            f.write(struct.pack('<III', 0x46546C67, 2, total))
            f.write(struct.pack('<II', len(bj), 0x4E4F534A))
            f.write(bj)
            f.write(struct.pack('<II', len(self.bin), 0x004E4942))
            f.write(self.bin)


F32 = struct.Struct('<f')


def pack_floats(vals):
    return b''.join(F32.pack(v) for v in vals)


def export_glb(root, path, fps=30.0, morph_targets=100):
    g = GLBBuilder()
    nodes = list(walk(root))
    mat_index = {}
    # 材质 (按贴图去重)
    for n in nodes:
        for m in n.meshes:
            if m.texture not in mat_index:
                mat_index[m.texture] = len(g.json['materials'])
                g.json['materials'].append({
                    'name': os.path.splitext(m.texture)[0] or 'mat',
                    'pbrMetallicRoughness': {'metallicFactor': 0.0,
                                             'roughnessFactor': 1.0},
                    'doubleSided': True})
    # 注意: GLB 不内嵌 bmp 贴图, 材质名保留贴图名便于回贴

    dt = 1.0 / fps
    if not g.json['animations']:
        g.json['animations'].append({'name': 'take001', 'channels': [], 'samplers': []})
    ANIM = g.json['animations'][0]

    def add_sampler(channels, out_data, out_type, times):
        bv = g.buf(pack_floats(times))
        ta = g.acc(bv, 5126, len(times), 'SCALAR')
        step = 3 if out_type == 'VEC3' else (4 if out_type == 'VEC4' else 1)
        bv2 = g.buf(pack_floats(out_data))
        ca = g.acc(bv2, 5126, len(out_data) // step, out_type)
        s = len(ANIM['samplers'])
        ANIM['samplers'].append({'input': ta, 'output': ca, 'interpolation': 'LINEAR'})
        for c in channels:
            ANIM['channels'].append({'sampler': s, 'target': c})

    node_idx = {}
    mesh_node_of = {}
    # 根容器
    root_i = g.node(name=root.name or 'MOX_ROOT', children=[])
    children_all = []
    # det<0 的动画节点: 镜像 F=diag(1,1,-1) 烘入网格, 动画用 A(t)=M(t)*F (真旋转)
    mirror_meshes = set()
    for n in nodes:
        if n is root:
            continue
        ni = g.node(name=n.name)
        node_idx[id(n)] = ni
        children_all.append(ni)
        if len(n.mats) > 1:  # 骨骼动画
            improper = det3(mat_math(n.mats[0])[0]) < 0
            trans, quats = [], []
            for m in n.mats:
                if improper:  # A = F*M (行主序): 取消第3行(数学列)镜像
                    m = m[:8] + tuple(-x for x in m[8:11]) + m[11:]
                R, t = mat_math(m)
                trans.extend(t)
                quats.extend(quat_from_mat(R))
            if improper:
                mirror_meshes.add(id(n))
            times = [f * dt for f in range(len(n.mats))]
            add_sampler([{'node': ni, 'path': 'translation'}], trans, 'VEC3', times)
            add_sampler([{'node': ni, 'path': 'rotation'}], quats, 'VEC4', times)
        else:  # 静态节点: glTF matrix 属性原生支持反射, 直接用原始 16f (列主序兼容)
            g.json['nodes'][ni]['matrix'] = list(n.mats[0])
        for mi, m in enumerate(n.meshes):
            mni = g.node(name=(m.name or '%s_mesh%d' % (n.name, mi)))
            g.json['nodes'][ni].setdefault('children', []).append(mni)
            mesh_node_of[id(m)] = mni
    g.json['nodes'][root_i]['children'] = children_all

    def bake_z(seq):  # F=diag(1,1,-1): 烘焙镜像到顶点/法线/形变量
        return [(-c if i % 3 == 2 else c) for i, c in enumerate(seq)]

    # 几何
    for n in nodes:
        improper = id(n) in mirror_meshes
        for m in n.meshes:
            mni = mesh_node_of[id(m)]
            pos = [c for v in m.verts0 for c in v[:3]]
            nrm = [c for v in m.verts0 for c in v[3:6]]
            if improper:
                pos, nrm = bake_z(pos), bake_z(nrm)
            uv = [c for v in m.verts0 for c in v[6:8]]
            bvi = g.buf(pack_floats(pos))
            mn = [min(pos[i::3]) for i in range(3)]
            mx = [max(pos[i::3]) for i in range(3)]
            ap = g.acc(bvi, 5126, m.nv, 'VEC3', mn, mx)
            bvi2 = g.buf(pack_floats(nrm))
            an = g.acc(bvi2, 5126, m.nv, 'VEC3')
            bvi3 = g.buf(pack_floats(uv))
            au = g.acc(bvi3, 5126, m.nv, 'VEC2')
            bidx = g.buf(struct.pack('<%dH' % m.nidx, *m.indices), 34963)
            ai = g.acc(bidx, 5123, m.nidx, 'SCALAR')
            prim = {'attributes': {'POSITION': ap, 'NORMAL': an, 'TEXCOORD_0': au},
                    'indices': ai, 'mode': 4,
                    'material': mat_index[m.texture]}
            targets = []
            weights = []
            if m.frames:
                K = min(morph_targets, len(m.frames) + 1)
                if K < 2:
                    K = 2
                step = max(1, (len(m.frames) + 1) // (K - 1))
                picked = list(range(1, len(m.frames) + 1, step))[:K - 1]
                for fi in picked:
                    dp, dn = [], []
                    for k in range(m.nv):
                        f = m.frames[fi - 1][k]
                        b = m.verts0[k]
                        dp.extend((f[0] - b[0], f[1] - b[1], f[2] - b[2]))
                        dn.extend((f[3] - b[3], f[4] - b[4], f[5] - b[5]))
                    if improper:
                        dp, dn = bake_z(dp), bake_z(dn)
                    bvp = g.buf(pack_floats(dp))
                    ap2 = g.acc(bvp, 5126, m.nv, 'VEC3')
                    bvn = g.buf(pack_floats(dn))
                    an2 = g.acc(bvn, 5126, m.nv, 'VEC3')
                    targets.append({'POSITION': ap2, 'NORMAL': an2})
                    weights.append(0.0)
                prim['targets'] = targets
            mesh_i = len(g.json['meshes'])
            g.json['meshes'].append({'name': m.name or 'mesh', 'primitives': [prim]})
            g.json['nodes'][mni]['mesh'] = mesh_i
            if targets:
                g.json['nodes'][mni]['weights'] = [0.0] * len(targets)
                # 权重动画: 最近邻采样目标
                wanim = []
                fi = 0
                for f in range(len(m.frames) + 1):
                    while fi + 1 < len(picked) and picked[fi + 1] <= f:
                        fi += 1
                    w = [0.0] * len(picked)
                    w[fi] = 1.0
                    wanim.extend(w)
                times = [f * dt for f in range(len(m.frames) + 1)]
                # weights 是任意长度, 单独写 sampler
                bv = g.buf(pack_floats(times))
                ta = g.acc(bv, 5126, len(times), 'SCALAR')
                bv2 = g.buf(pack_floats(wanim))
                ca = g.acc(bv2, 5126, len(wanim), 'SCALAR')
                s = len(ANIM['samplers'])
                ANIM['samplers'].append({'input': ta, 'output': ca,
                                         'interpolation': 'LINEAR'})
                ANIM['channels'].append(
                    {'sampler': s, 'target': {'node': mni, 'path': 'weights'}})
    g.save(path)
    return g


# ---------------------------------------------------------------- OBJ 导出
def export_obj_frame(nodes, frame, out, vi0=1):
    lines = ['# mox_anim.py frame %d' % frame]
    vi = vi0
    for n in nodes:
        R, t = mat_math(n.mats[min(frame, len(n.mats) - 1)])
        for m in n.meshes:
            verts = m.frames[frame - 1] if (m.frames and 1 <= frame < m.vframes) else m.verts0
            lines.append('o %s|%s' % (n.name or 'node', m.texture))
            for v in verts:
                w = tuple(R[i][0] * v[0] + R[i][1] * v[1] + R[i][2] * v[2] + t[i]
                          for i in range(3))
                lines.append('v %.6f %.6f %.6f' % w)
            for v in verts:
                lines.append('vt %.6f %.6f' % (v[6], 1.0 - v[7]))
            for v in verts:
                wn = tuple(R[i][0] * v[3] + R[i][1] * v[4] + R[i][2] * v[5]
                           for i in range(3))
                lines.append('vn %.6f %.6f %.6f' % wn)
            idx = m.indices
            if m.trilist == 1 and m.nidx % 3 == 0:
                for k in range(0, m.nidx, 3):
                    lines.append('f %d/%d/%d %d/%d/%d %d/%d/%d' % (
                        vi + idx[k], vi + idx[k], vi + idx[k],
                        vi + idx[k + 1], vi + idx[k + 1], vi + idx[k + 1],
                        vi + idx[k + 2], vi + idx[k + 2], vi + idx[k + 2]))
            else:
                for k in range(m.nidx - 2):
                    a = idx[k]
                    b = idx[k + 1 - (k % 2) * 2]
                    c = idx[k + 2 - (k % 2) * 2]
                    lines.append('f %d/%d/%d %d/%d/%d %d/%d/%d' % (
                        vi + a, vi + a, vi + a,
                        vi + b, vi + b, vi + b,
                        vi + c, vi + c, vi + c))
            vi += m.nv
    open(out, 'a' if vi0 > 1 else 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    return vi


# ---------------------------------------------------------------- 报告
def report(root):
    print('=== 节点树 ===')
    def rec(n, d=0):
        info = 'FC=%d' % len(n.mats)
        if n.meshes:
            info += ' meshes=[' + ','.join(
                '%s(nv%d,idx%d,vf%d)' % (m.texture, m.nv, m.nidx, m.vframes)
                for m in n.meshes) + ']'
        print('  ' * d + '%r %s' % (n.name, info))
        for c in n.children:
            rec(c, d + 1)
    rec(root)
    # 矩阵正交性检查
    worst = 0.0
    dets = []
    for n in walk(root):
        for m in n.mats:
            R, t = mat_math(m)
            dets.append(det3(R))
            for i in range(3):
                ln = math.sqrt(sum(x * x for x in R[i]))
                worst = max(worst, abs(ln - 1.0))
    print('矩阵检查: 行长最大偏差 %.4f, det 范围 [%.4f, %.4f]' %
          (worst, min(dets), max(dets)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('--glb', help='输出 GLB 路径')
    ap.add_argument('--fps', type=float, default=30.0, help='动画帧率 (默认30)')
    ap.add_argument('--morph-targets', type=int, default=100, help='顶点形变目标数上限')
    ap.add_argument('--obj', help='导出第0帧静态 OBJ')
    ap.add_argument('--obj-dir', help='导出 OBJ 帧序列目录')
    ap.add_argument('--obj-step', type=int, default=32, help='OBJ 序列帧步长')
    ap.add_argument('--obj-all', action='store_true', help='OBJ 序列导出每一帧')
    a = ap.parse_args()

    d, root = parse_mox(a.input)
    nodes = list(walk(root))
    print('解析成功: %d 节点, 文件 %d 字节全部消费' % (len(nodes), len(d)))
    report(root)

    maxfc = max(len(n.mats) for n in nodes)
    if a.glb:
        export_glb(root, a.glb, a.fps, a.morph_targets)
        print('GLB -> %s (%d 帧 @%.0ffps = %.1fs)' %
              (a.glb, maxfc, a.fps, maxfc / a.fps))
    if a.obj:
        vi = export_obj_frame(nodes, 0, a.obj)
        print('OBJ(第0帧) -> %s' % a.obj)
    if a.obj_dir:
        os.makedirs(a.obj_dir, exist_ok=True)
        step = 1 if a.obj_all else a.obj_step
        for f in range(0, maxfc, step):
            p = os.path.join(a.obj_dir, 'frame_%04d.obj' % f)
            export_obj_frame(nodes, f, p)
        print('OBJ 序列 -> %s (每 %d 帧, 共 %d 个)' %
              (a.obj_dir, step, len(range(0, maxfc, step))))


if __name__ == '__main__':
    main()
