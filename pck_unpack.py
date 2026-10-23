#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PCK 资源包解包脚本
参考: https://www.52pojie.cn/thread-1976963-1-1.html

格式结构:
  [数据区 0..data_size] [目录区] [特征区 272 字节]
特征区:  首 4 字节 = 03 00 01 00, +4 处 u32 = 数据区长度, 文件末尾 -8 处 u32 = 文件数量
目录项:  路径长度 u32(含结尾 00) | 路径字符串(GBK) | 文件偏移 u32 | 解压前大小 u32 | 压缩后大小 u32
数据:    Zlib 压缩(78 01/9C/DA 头); 压缩前后大小相等时为明文不压缩

用法:
  python pck_unpack.py 输入.pck [-o 输出目录]
"""
import argparse
import os
import struct
import sys
import zlib

SIGNATURE_SIZE = 272
MAGIC = b"\x03\x00\x01\x00"
U32 = struct.Struct("<I").unpack_from


def read_u32(buf, off):
    return U32(buf, off)[0]


def parse_pck(pck_path):
    """解析 PCK, 返回 (entries, header_info)。entries 为 dict 列表。"""
    file_size = os.path.getsize(pck_path)
    if file_size <= SIGNATURE_SIZE:
        raise ValueError("文件过小, 不是有效的 PCK")

    with open(pck_path, "rb") as f:
        # ---- 特征区校验与信息读取 ----
        f.seek(file_size - SIGNATURE_SIZE)
        sig = f.read(SIGNATURE_SIZE)
        if sig[:4] != MAGIC or sig[-4:] != MAGIC:
            print("[!] 警告: 特征区首尾标记不是 03 00 01 00, 可能不是同款 PCK, 继续尝试解析...")

        data_size = read_u32(sig, 4)          # 数据区长度
        file_count = read_u32(sig, SIGNATURE_SIZE - 8)  # 文件数量

        dir_start = data_size
        dir_len = file_size - data_size - SIGNATURE_SIZE
        if dir_len <= 0:
            raise ValueError("目录区长度异常")

        print(f"PCK 大小: {file_size} 字节")
        print(f"数据区长度: {data_size} 字节 (0x{data_size:X})")
        print(f"目录区偏移: {dir_start} (0x{dir_start:X}), 长度 {dir_len} 字节")
        print(f"文件数量: {file_count}")

        f.seek(dir_start)
        idx = f.read(dir_len)

        # ---- 解析目录表 ----
        entries = []
        pos = 0
        for i in range(file_count):
            if pos + 4 > len(idx):
                print(f"[!] 目录区在第 {i} 项处越界, 提前结束解析")
                break
            name_len = read_u32(idx, pos) - 1  # 存储值含结尾 00, 实际字符串长度 = 值 - 1
            pos += 4
            if name_len < 0 or pos + name_len > len(idx):
                print(f"[!] 第 {i} 项路径长度异常 ({name_len}), 停止解析")
                break
            raw_path = idx[pos:pos + name_len]
            pos += name_len + 1  # 跳过字符串 + 1 字节 00 填充

            if pos + 12 > len(idx):
                print(f"[!] 第 {i} 项偏移/大小字段越界, 停止解析")
                break
            offset = read_u32(idx, pos)
            actual_size = read_u32(idx, pos + 4)
            comp_size = read_u32(idx, pos + 8)
            pos += 12

            # 路径按 GBK 解码 (原 C# 用 Encoding.Default)
            try:
                path_str = raw_path.decode("gbk")
            except UnicodeDecodeError:
                path_str = raw_path.decode("gbk", errors="replace")

            entries.append({
                "path": path_str,
                "offset": offset,
                "actual": actual_size,
                "comp": comp_size,
            })

        return entries, f


def extract_all(pck_path, entries, out_dir):
    """按目录表把全部文件解包到 out_dir, 保留原始目录结构。"""
    root = os.path.join(out_dir, os.path.splitext(os.path.basename(pck_path))[0] + "_unpacked")
    os.makedirs(root, exist_ok=True)

    ok = fail = raw = 0
    with open(pck_path, "rb") as f:
        for i, e in enumerate(entries):
            # 规范化路径: 统一分隔符, 去掉盘符/..等危险成分
            rel = e["path"].replace("\\", "/").lstrip("/")
            parts = [p for p in rel.split("/") if p not in ("", ".")]
            if not parts:
                parts = [f"file_{i:05d}.bin"]
            final_path = os.path.join(root, *parts)

            f.seek(e["offset"])
            data = f.read(e["comp"])

            if e["actual"] == e["comp"]:
                out = data          # 未压缩, 原样写出
                raw += 1
            else:
                try:
                    # 用 decompressobj 容忍尾部多余字节
                    out = zlib.decompressobj().decompress(data)
                    if len(out) != e["actual"]:
                        print(f"[!] {rel}: 解压大小 {len(out)} 与目录表 {e['actual']} 不符")
                except zlib.error as ex:
                    print(f"[x] {rel}: Zlib 解压失败 ({ex}), 原样写出压缩数据")
                    out = data
                    fail += 1
                    os.makedirs(os.path.dirname(final_path), exist_ok=True)
                    with open(final_path + ".z", "wb") as w:
                        w.write(out)
                    continue

            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            with open(final_path, "wb") as w:
                w.write(out)
            ok += 1

            if (i + 1) % 100 == 0 or i == len(entries) - 1:
                print(f"进度: {i + 1}/{len(entries)}")

    print(f"\n完成: 成功 {ok} 个 (其中未压缩 {raw} 个), 解压失败 {fail} 个")
    print(f"输出目录: {root}")


def main():
    ap = argparse.ArgumentParser(description="PCK 资源包解包工具")
    ap.add_argument("pck", help="待解包的 .pck 文件路径")
    ap.add_argument("-o", "--out", default=".", help="输出目录 (默认当前目录)")
    args = ap.parse_args()

    if not os.path.isfile(args.pck):
        sys.exit(f"文件不存在: {args.pck}")

    entries, _ = parse_pck(args.pck)
    print(f"解析到 {len(entries)} 个文件条目")
    extract_all(args.pck, entries, args.out)


if __name__ == "__main__":
    main()
