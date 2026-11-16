# 《血战上海滩》.mox 3D 模型格式分析

逆向对象：血战上海滩（Enya 引擎，`shanghai.exe`）
分析环境：Ghidra 12.1.4 + 十六进制对照验证（玻璃.mox / 巴祖卡火箭弹.mox）

## 结论速览

`.mox` 是 **ASCII 标签驱动的分块（chunk）格式**，二进制模式数据为裸 float32 / uint16，无压缩、无加密、1 字节对齐。每个文件由 `MOXB` 魔数开头，包含若干 `FRAME / MESH / TEXTURE / MATERIAL` 块，引擎按标签递归解析构建"节点树 + 网格 + 材质"。

坐标系为 **Y 轴向上**。

## 文件顶层结构

```
MOXB                          魔数（4 字节）
FRAME: <名称>\0 { ... }       骨骼/节点块（4x3 变换矩阵 + 子节点计数，递归）
MESH:  <名称>\0 ...           网格块（见下）
TEXTURE: <名称>               贴图引用
MATERIAL: <名称>\0 { ... }    材质块（浮点参数组）
```

块标签均为明文 ASCII（`FRAME: %s\n{`、`MESH: %s\n{` 等），名称为 GBK 编码（如 `电话亭_玻璃02`）。

## MESH 块布局（二进制模式，已实测验证）

```
"MESH: " + 名称\0             名称可为空串
u32  顶点数 (nv)
u32  索引数 (nidx)            uint16 个数，非三角形数
u32  TriangleList             1 = 三角形列表（另有三角形条带变体）
u32  材质/标志位              实测样本恒为 0x00280000
...杂项字段...                共 65 字节（名称为空串时，从 "MESH: " 起算）
u16 × nidx                    三角形索引数组（每 3 个为一三角形）
32B × nv                      顶点流：
                              [0]  pos.x/y/z    (3 × float32)
                              [12] normal.x/y/z (3 × float32)
                              [24] u/v          (2 × float32)
```

验证样本：玻璃.mox（6 网格 × 4 顶点/6 索引）、巴祖卡火箭弹.mox（1 网格 × 82 顶点/210 索引 → 70 三角形）。

### 注意事项

- **结构按 1 字节对齐读取**（packed struct），顶点流起点不在 4 对齐地址上，按 4 对齐解析必错位
- 写入器存在 **HasLOD 变体**：顶点区前会多写一段 LOD 索引缓冲（u32 + nidx×2 字节），顶点起点整体后移
- **TriangleStrip 变体**（字段 +0x115）：索引为条带式，绕序逐三角形翻转
- 头部总长随名称长度变化：非空名称时 65 + len(name)

## 文本模式

引擎支持同一格式的**文本变体**（写入器按标志位二选一），键名：

```
MESH: <名称>  VertCount  IndexCount  FrameCount  HasLOD  TriangleList
2Sided  CanShootThrough  NoMark  CanPickThrough  MaterialType
Index: ...  LODMinDis/LODMaxDis/LODLimit  Frame N: { ... }  DetailTexture  Texture
```

字段语义：`2Sided` 双面、`CanShootThrough` 可击穿、`NoMark` 不留弹痕、`CanPickThrough` 可穿透拾取、`MaterialType` 材质类型（影响弹孔/音效）。索引在文本模式以 `%d, %d, %d` 三元组逐行书写。

## 引擎侧关键地址

| 项目 | 地址 | 说明 |
|---|---|---|
| MOXB 魔数校验 | `0x50F660` | 内联 cmp + jne，模型 Load 入口 |
| FRAME 块解析 | `0x4AE6C0` | 模型类虚方法（虚表 `0x594448`） |
| MESH 块读写器 | `0x4E32B0` | 二进制/文本双模式序列化 |
| 格式串 | `0x5CA30C` / `0x5CF23C` / `0x5CF248` / `0x5D0EA4` | FRAME/MESH/TEXTURE/MATERIAL |

## 转换脚本

`mox2obj.py` — 提取全部 MESH 网格（顶点 + 三角面）导出 Wavefront OBJ：

```powershell
python mox2obj.py 输入.mox 输出.obj
```

当前版本适用范围：空名称、无 LOD 的三角形列表网格（已双样本验证）。命名网格 / LOD / 条带索引需按上文注意事项扩展。

## 附：完整 8 浮点顶点结构出处

写入器逐顶点以 `"%f, %f, %f, %f, %f, %f, %f, %f"` 输出 8 个浮点（文本模式 Frame 块内），与二进制 32 字节 stride 完全对应：位置 3 + 法线 3 + UV 2。

## 声明

仅为格式分析与个人模组研究用途，版权归原厂商所有。
