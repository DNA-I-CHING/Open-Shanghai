# Shanghai Reverse Engineer

## INI

游戏的 `.ini`文件以乱码形式存储，首字节为 `0x00`，后续字节呈 26 字节周期性重复，其中两处字节逐记录递增 `0x20`。本文通过静态逆向还原其加密与加载流程。


### 解密入口

`CIniFile::Open`（`FUN_00402A50`）读入整个文件后，调用对象虚表 `+8` 的虚方法处理缓冲区：

```c
// FUN_00402A50 (简化)
buffer = FileSystem.Read(path, &size);   // this+0x14 处的文件系统对象
buffer[size] = '\0';
(**(vtable + 8))(buffer, size);          // ← 解密在此发生
```

虚表 `0x58D4B0` 的第 3 项（`+8`）为 `0x402BA0`。该地址未被 Ghidra 自动识别为函数，通过 capstone 直接反汇编确认其逻辑：

```c
// 0x402BA0 (简化)
if (buf == NULL || size < 0) assert_fail();
if (size == 0) return TRUE;
if (buf[0] == 0x00) {                    // ← 加密标志
    copy = operator_new(size);
    memcpy(copy, buf, size);
    FUN_004027E0(copy, size);            // ← 真正的解密
    buf = copy;
}                                        // 否则：明文直通
// ... 后续为逐行 INI 解析循环
```

**关键发现：首字节 `0x00` 是加密标志。非 `0x00` 开头的文件被引擎按明文直接解析，加密是可选项。**

## 解密算法

`FUN_004027E0` 反编译结果：

```c
void FUN_004027E0(byte *buf, int size)
{
    do {
        *buf = buf[1] << 3 | buf[1] >> 5;   // ROL8(buf[1])：下一字节循环左移 3 位
        buf++;
        size--;
    } while (size != 0 && size > 0);
}
```

即：

```
plain[i] = ROL3(cipher[i+1])
```

- 明文的每个字节 = 密文**下一个**字节循环左移 3 位
- 密文首字节 `0x00` 为标志位，解密过程中被覆盖丢弃
- 循环第 `size` 次会读取 `buf[size]`（`Open` 已在文件末尾补写 `'\0'`，因此最后一字节恒为 `0`，无越界风险）

**加密为逆运算：**

```
cipher[0] = 0x00
cipher[i+1] = ROR3(plain[i])
```

无密钥、无轮次、无校验，强度接近于零。

## 明文直通特性

`0x402BCF` 处的 `cmp byte [edi],0 / jne 0x402C16` 分支意味着：

> 编辑后的 INI **无需重新加密**，以明文（首字节为 `[`，即 `0x5B ≠ 0`）保存放回原位置即可被游戏正常读取。

注意事项：

- 文件必须为 **GBK/ANSI 编码**。UTF-8 BOM（`EF BB BF`）虽仍走明文分支，但会导致节名解析失败；
- 该逻辑位于引擎层 `CIniFile`，推测适用于本引擎读取的全部加密 INI，而非仅 `gamebkmusic.ini`。

## 解密脚本

完整工具 `shde.py`（需 Python 3）：

```text
python shde.py de input.ini [output.ini]   解密 → 生成 *_decrypted.ini
python shde.py en input.ini [output.ini]   加密 → 生成 *_encrypted.ini
python shde.py show input.ini              仅打印内容，不写文件
```

- 不带参数运行时弹出文件选择对话框，适合手动操作
- `de` 游戏原文件（首字节 `0x00`），`en` 编辑后的明文
- 明文编辑保存请用 ANSI/GBK 编码（推荐直接放回游戏目录，无需 `en` 加密）
