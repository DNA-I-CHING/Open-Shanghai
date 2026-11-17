# 《血战上海滩》.mox 3D 模型格式分析

逆向对象：血战上海滩（Enya 引擎，`shanghai.exe`，x86 PE，image base 0x400000）
分析环境：Ghidra 12.1.4（GUI MCP）+ 十六进制对照验证
验证样本：玻璃.mox / 巴祖卡火箭弹.mox / 97式坦克.mox / 生化兵_半硬动画.mox / z.mox（主角）

## 结论速览

`.mox` 是 **ASCII 标签驱动的分块（chunk）格式**，二进制模式数据为裸 float32 / uint16，无压缩、无加密、1 字节对齐。每个文件由 `MOXB` 魔数开头，按 `FRAME / MESH / TEXTURE / MATERIAL` 标签递归解析构建"节点树 + 网格 + 材质 + 动画"。

- 坐标系 **Y 轴向上**；节点名称为 GBK 编码（如 `生化兵_左脚`）
- **二进制模式下块标签仍是明文文本**，仅数值载荷为原始 u32 / float32
- 支持**骨骼关键帧动画**（每节点 FrameCount × 4×4 矩阵）与**逐帧顶点形变**（MESH 内 VertFrameCount × 顶点流），二者组合即"半硬动画"

## 双魔数与模式标志

`FUN_0050F660`（FileImage::Open）读文件头 4 字节校验：

| 魔数 | 字节 | 含义 | 标志位 |
|---|---|---|---|
| `MOXB` | `0x42584F4D` | 二进制容器 | `flags \| 0x10` |
| `MOTX` | `0x54584F4D` | 文本容器 | `flags \| 8` |

文本标志位于流对象 `+0x84`（读侧判断 `*(byte*)(stream+0x84) & 8`）。读写器据此在文本/二进制两条序列化路径间二选一。

## 文件顶层结构

```
MOXB                          魔数（4 字节）
FRAME: <名称>\0 { ... }       节点/骨骼块（见下，递归）
MESH:  <名称>\0 ...           网格块（嵌于 FRAME 内）
TEXTURE: <名称>               贴图引用（嵌于 MESH 尾部）
MATERIAL: <名称>\0 { ... }    材质块（嵌于 MESH 尾部）
```

## FRAME 块布局（二进制模式，字节级验证 ★新增★）

自文件偏移 4（紧跟 `MOXB`）起递归：

```
"FRAME: " + 名称\0
u32  FrameCount
FrameCount × 64B   4×4 float32 矩阵流（DirectX 行主序，平移在第 4 行）
u32  BoundingBoxNum
  × N: 168B 条目（见 §BoundingBox）
u32  MeshCount
  × K: MESH 块
u32  ChildCount
  × C: 递归 FRAME 块
```

实测对账（生化兵_半硬动画.mox, 3,084,672B）：

- 根节点 `FRAME: \0`：FrameCount=1（单位阵），BBox=0，Mesh=0，Child=17
- 子节点 `生化兵_左脚`：FrameCount=1911，122,304B 矩阵流，Mesh=1，Child=0
- 全树 20 节点递归解析后**恰好消耗到文件尾 0x2F1180**，零剩余

矩阵样例（`生化兵_左脚` 第 0 帧，行长偏差 <0.0001，纯刚性）：

```
[-0.909  0.085  0.412  0]      ← 旋转（行向量基）
[ 0.124  0.990  0.068  0]
[-0.402  0.224 -0.909  0]
[-0.120  0.020  0.080  1]      ← 平移在最后一行
```

### 矩阵语义（三条关键结论 ★新增★）

1. **烘焙的世界变换**：骨骼平移即世界坐标（左脚 y=0.02 贴地、`HH_开火` 枪口 y=1.35、胯部 y=0.99）；嵌套节点存储的同样是世界矩阵 → 导出时可全部拍平
2. **网格顶点为节点局部坐标**：左脚网格顶点聚于原点附近（y≈0 脚掌形状），运行时 `world = M(t) × v_local`
3. **半数节点带镜像**：右脚/右小腿/右大腿/头部/双手双臂/颈部/胸部/胯部共 13 节点 det(M)=-1（恒定），其余 +1 —— 导出 glTF 时取 `A(t)=M(t)·F`（F=diag(1,1,-1)）作真旋转动画，F 烘入顶点

## FRAME 文本模式字段序

```
FRAME: <名>
FrameCount: N
  每帧: "%d" 行 + 4 行 "%f,%f,%f,%f"（16 浮点 = 64B 矩阵的文本形式）
BoundingBoxNum: M
  每框: 名称行 + 5 行 "%f,%f,%f"（15 浮点）
MeshCount: K → K 个 MESH 块
ChildCount: C → C 个递归 FRAME
```

与二进制模式字段序完全一致。

## MESH 块布局（二进制模式，Ghidra 写器汇编逐字段解码 ★已完全钉死★）

写器 `0x4E32B0` 二进制分支的写入序列（capstone 逐条解码，与文件字节完全对账）：

```
"MESH: " + 名称\0             名称可为空串; 整串按 (strlen+1) 一次写入
u32  顶点数 (nv)              运行时驻留对象 +0x128
u32  索引数 (nidx)            uint16 个数，非三角形数; 对象 +0x12C
u32  VertFrameCount           顶点动画帧数! 静态=1; 对象 +0x120
u8   HasLOD                   对象 +0x525 (0=无LOD)
u8   TriangleList             对象 +0x115 ★0=三角形列表, 非0=三角形条带★
u32  常量 0x28 (=40)          每次写入恒为 40, 疑为块尺寸/版本标记
40B  包围体 blob              对象 +0x2E0 起连续写出 (实测样本恒全0, 运行时计算)
u16 × nidx                    三角形索引数组（与 40B blob 同一次 write 调用写出）
[HasLOD≠0 时 LOD 块]          f32 LODMinDis(+0x534) + f32 LODMaxDis(+0x538)
                              + u32 LODLimit(+0x530) + nv×u16 LOD数据(+0x528)
VertFrameCount × nv × 32B     顶点帧流（第 0 帧即静态顶点，其后为形变帧）：
                              [0]  pos.x/y/z    (3 × float32)
                              [12] normal.x/y/z (3 × float32)
                              [24] u/v          (2 × float32)
"TEXTURE: " <贴图名>\0
"MATERIAL: " <名称>\0 + 69B   材质块（见下节）
```

> 名称\0 后偏移：nv@0、nidx@4、VertFrameCount@8、HasLOD@12、TriangleList@13、
> 常量0x28@14、blob@18..57、索引@58 —— 与"tag 起算 65B"（名称空时）完全一致。

**此前版本的两处错误已修正**：
1. "TriangleList 1=列表" **反了** —— 写器按 `字节==0 → 三元组列表，非0 → 条带对`；实测样本字节=0
2. "材质/标志位 0x280000" 实为 3 个独立字段（FrameCount u32 + HasLOD u8 + TriangleList u8）与常量 0x28 的错位拼合

验证样本：玻璃.mox（6 网格）、巴祖卡火箭弹.mox（82 顶点/210 索引）、97式坦克.mox、生化兵_半硬动画.mox（17 网格）、z.mox 主角（569 帧，含 vf=569 全身形变网格）。

### 顶点动画（"半硬动画"本体）

- `VertFrameCount`（第 3 个 u32）= 逐帧顶点形变帧数；通气管=1911、主角(z.mox)全身网格=569
- 通气管对账：`(TEXTURE 偏移 − 顶点流起点) = 1911 × nv18 × 32B = 1,100,736B` 精确成立；逐帧 stride = nv×32B，UV 指纹跨帧字节级不变
- 软体节点矩阵静态、顶点逐帧形变；刚性节点矩阵逐帧变换、顶点静态——二者组合即"半硬"

### 注意事项

- **结构按 1 字节对齐读取**（packed struct），按 4 对齐解析必错位
- 头部总长随名称长度变化：非空名称时各偏移 +len(name)
- 运行时对象关键偏移（供进一步逆向）：顶点帧指针数组 +0x118、网格链表 +0x124、
  矩阵缓冲指针 +0x138、FrameCount +0x188、bbox 数 +0x19C、DetailTexture 标志 +0x29C、
  DetailTexture 4×4 纹理变换 16f +0x2A4..+0x2DC、序列化块 +0x2E0、LOD 族 +0x525..+0x538

## BoundingBox 条目（168B，二进制 ★新增★）

```
16B   定长 GBK 名称，形如 "BB_生化兵_左小腿"（不足补 \0）
152B  碰撞载荷（见下）
```

载荷已定名的字段（z.mox 主角 + 97式坦克双样本对账）：

| 载荷偏移 | 类型 | 语义 |
|---|---|---|
| +100 | vec3 f32×3 | **碰撞盒半尺寸**（坦克整体 (1.41, 0.61, 2.81) vs 子件 (0.66, 0.40, 0.78)，量级吻合 ✓） |
| +112 | u32 | 实测恒 0x100（256） |
| +116 | u32 | 实测恒 0x13（19） |
| 其余 | — | 多为 0 或 ±FLT_MAX 型默认值，语义未定名 |

文本模式每框 = 名称行 + 5 行 × 3 浮点（15 浮点）；与二进制 168B 不完全对齐，推断二进制载荷更丰富。独立标记节点形如 `parent_BB_97式坦克01`（坦克/巴祖卡样本），其 meshcount=0 / childcount=0。

## TEXTURE / MATERIAL 块（MATERIAL 69B ★已完全破解★）

MESH 尾部内嵌：

```
"TEXTURE: " <bmp 文件名>\0     如 BioChemisMan.bmp / 巴祖卡火箭炮（前视）.bmp
"MATERIAL: " <名称>\0 + 69B    名称实测为空串
```

69B 载荷逐字段（写器 `Material::Write 0x4FBCC0` 反编译钉死，与引擎文本键一一对应）：

| 偏移 | 类型 | 文本键 | 运行时驻留（MESH 对象 +0x13C 材质子对象） |
|---|---|---|---|
| +0 | 4×f32 | `Ambient` | RGBA 环境色 |
| +16 | 4×f32 | `Diffuse` | RGBA 漫反射色（贴图相乘的主色调） |
| +32 | 4×f32 | `Emissive` | RGBA 自发光色 |
| +48 | 4×f32 | `Specular` | RGBA 高光色 |
| +64 | f32 | `Power` | 高光锐化指数（样本 10.0） |
| +68 | u8 | `2Sided` | 双面渲染标志 |

64B 颜色区 + Power + 2Sided = 69B，与文件字节精确对账。文本模式的其余 MESH 键（`CanShootThrough/NoMark/CanPickThrough/MaterialType`）为运行时布尔/枚举，不写入二进制 69B 载荷。

## 文本模式（MESH 键名）

引擎支持同一格式的文本变体（写入器按标志位二选一），键名：

```
MESH: <名称>  VertCount  IndexCount  FrameCount  HasLOD  TriangleList
2Sided  CanShootThrough  NoMark  CanPickThrough  MaterialType
Index: ...  LODMinDis/LODMaxDis/LODLimit  Frame N: { ... }  DetailTexture  Texture
```

字段语义：`2Sided` 双面、`CanShootThrough` 可击穿、`NoMark` 不留弹痕、`CanPickThrough` 可穿透拾取、`MaterialType` 材质类型（影响弹孔/音效）。索引在文本模式以 `%d, %d, %d` 三元组逐行书写；`Frame N:` 块内逐顶点输出 8 浮点（对应逐帧顶点形变）。

## 引擎侧关键地址

| 项目 | 地址 | 说明 |
|---|---|---|
| MOXB/MOTX 魔数校验 | `0x50F660` | FileImage::Open，模型 Load 入口 |
| FRAME::Write | `0x4AE6C0` | FRAME 虚表 `0x594448` 首项，文本+二进制双分支 |
| FRAME::Read | `0x4AEC10` | FRAME 虚表 `[1]`，二进制分支由此钉死布局 |
| FRAME 构造器 | `0x4ADA00` | 对象 0x1BC 字节 |
| FRAME 分配器 | `0x4ADB50` | FRAME 虚表 `+0x24` |
| MESH 构造器 | `0x4E2770` | 对象 0x53C 字节 |
| MESH 块读写器 | `0x4E32B0` | 二进制/文本双模式序列化（写侧逐字段汇编解码） |
| MESH::Read | `0x4E3BC0` | MESH 虚表 `0x59458C[1]`（Ghidra 未定义为函数） |
| Material::Write | `0x4FBCC0` | 材质虚表 `0x594724[0]`，69B 载荷由此钉死 |
| 格式串 | `0x5CA30C` / `0x5CF23C` / `0x5CF248` / `0x5D0EA4` | FRAME/MESH/TEXTURE/MATERIAL |
| FRAME 格式串 | `0x5CA318` `"FRAME: %s"`；`0x5CA2F8/0x5CA2F0/0x5CA2DC` FrameCount/帧序号/矩阵行 | Write 文本分支 |

流对象虚表原语：`+0x14` 读原始块、`+0x18` 写原始块、`+0x1C` 读一行、`+0x20` 读字符串、`+0x24` 写格式化行、`+0x28` 读计数值。

## 半硬动画渲染机制 ★新增★

- 骨骼节点：FrameCount>1 时每帧一个**世界矩阵**（刚性），其挂载网格随之运动
- 软体节点：`VertFrameCount>1` 的网格逐帧形变（节点矩阵静态）
- 标记节点：`HH_开火`（枪口世界坐标）、`HH_胯部`、`HH_巴祖卡火箭弹` 等空节点供逻辑查询
- 动画帧率未存于文件；转换器默认 30fps（1911 帧 ≈ 63.7s）

## 转换脚本

**m2o.py**（本报告配套的唯一转换器，含动画；即原 mox_anim.py 更名，解析器已按上文钉死的新布局重写）：

```powershell
# 基本用法: python m2o.py 输入.mox [选项]

# 1. 导出 GLB（骨骼动画 + 顶点形变 + 69B 材质解码, 推荐 Blender/Three.js/gltf-viewer 打开）
python m2o.py 生化兵_半硬动画.mox --glb 输出.glb
#    --fps 30            动画帧率（默认 30, 文件内未存储帧率）
#    --morph-targets 100 顶点形变网格的形态键数上限（默认 100, 越大越平滑越卡）

# 2. 导出第 0 帧静态 OBJ（快速预览几何/贴图坐标）
python m2o.py 输入.mox --obj 输出.obj

# 3. 导出 OBJ 帧序列（逐帧烘焙世界变换, 适合不支持 glTF 动画的旧工具）
python m2o.py 输入.mox --obj-dir OBJ序列目录 --obj-step 32   # 每 32 帧存一个
python m2o.py 输入.mox --obj-dir OBJ序列目录 --obj-all       # 全部 1911 帧

# 4. 仅查看结构（不导出）：打印节点树、每节点 FrameCount、
#    网格 nv/nidx/VertFrameCount、矩阵正交性与 det 检查
python m2o.py 输入.mox
```

多个选项可组合，例如 `--glb 输出.glb --obj 静态.obj` 一次导出两种格式。

功能说明：

- 按 MESH 头部新布局解析（3×u32 + HasLOD/TriangleList 字节 + 常量 0x28 + 40B blob + 索引 + LOD 块）
- MATERIAL 69B 全字段解码（ambient/diffuse/emissive/specular/power/twosided），接入 GLB 材质
- 条带（TriangleList≠0）自动展开为三角列表
- 递归解析 FRAME 树（标签断言 / 全文件消耗对账 / 索引越界检查），静态与动画容器通用
- GLB：节点拍平（世界矩阵已烘焙）+ 骨骼 TRS 动画；det<0 镜像节点用 A(t)=M(t)·F 真旋转 + F 烘入顶点；顶点形变导出为 morph target（差量式）+ 权重动画
- OBJ：`world = M(t) · v_local` 已烘焙（面判断 `trilist==0` 列表 / 否则条带展开）；贴图名写进对象名便于回贴
- 贴图 bmp 不内嵌 GLB，材质名保留贴图文件名，放进同一目录即可在 Blender 里手动指定

验证记录：5 个样本全部精确解析到文件尾 —— 生化兵 GLB(1911 帧) + 第 0 帧 OBJ、巴祖卡 GLB(101 帧)、玻璃 S.glb + S_第0帧.obj、主角 z.glb + z_第0帧.obj（13 节点 FC=569）、97式坦克.obj。生化兵 GLB 37 对象、33 采样器、accessor 零越界；Blender 5.2 实导入动作 1528 帧（24fps 重映射）、通气管 100 形态键、截帧确认运动；OBJ 第 0 帧左脚首顶点 (-0.339, 0.045, 0.076) 与手算 M·v 一致。

## 模组制作要点（格式摸底结论）

1. **改模型/换贴图**：MESH 顶点流 32B/顶点定长 stride，改顶点数只需同步改 nv 与索引；贴图为外部 bmp 引用，直接替换文件即可
2. **改材质**：69B 定长，四个 RGBA + Power + 2Sided，位置固定、无需重排后续块
3. **加动画**：节点改 FrameCount 并扩矩阵流，或网格改 VertFrameCount 扩顶点流—— stride 恒定，块尺寸自洽可算
4. **碰撞**：bbox 条目 168B 定长，改半尺寸 vec3@+100 即可调整碰撞盒
5. **格式自校验**：解析器按标签+计数值链式推进，字段间无对齐/填充/校验和，手改二进制后 `(消耗==文件长)` 即合法性充分条件

## 遗留未定项

1. bbox 152B 载荷除 +100/+112/+116 外的其余字段语义
2. MESH 头部 u32 常量 0x28 的语义（恒 40，疑块尺寸/版本标记）
3. 命名网格（非空名称）/ LOD 块 / 条带索引（TriangleList≠0）的实际样本未实测（仅写器侧证据，解析器已按新布局支持）
4. 引擎 UV 的 V 轴原点方向（当前按 `1-v` 翻转导出 OBJ，未贴图实测）
5. 动画帧率权威来源（运行时逻辑侧，按 30fps 假设）

## 附：完整 8 浮点顶点结构出处

写入器逐顶点以 `"%f, %f, %f, %f, %f, %f, %f, %f"` 输出 8 个浮点（文本模式 Frame 块内），与二进制 32 字节 stride 完全对应：位置 3 + 法线 3 + UV 2。

## 声明

仅为格式分析与个人模组研究用途，版权归原厂商所有。
