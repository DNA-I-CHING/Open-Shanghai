# -*- coding: utf-8 -*-
"""
Shanghai 1937 (shanghai.exe) INI file decrypt/encrypt tool

Algorithm (from shanghai.exe FUN_004027E0, engine E:\\dev\\Enya):
    decrypt: plain[i] = ROL3(cipher[i+1])  -- each plain byte = next cipher byte rotated left 3
    encrypt: cipher[0] = 0x00 (encrypted marker), cipher[i+1] = ROR3(plain[i])
    An INI whose first byte is not 0x00 is treated by the game as plain text.

Usage:
    python shanghai_ini_tool.py de input.ini [output.ini]    # decrypt
    python shanghai_ini_tool.py en input.ini [output.ini]    # re-encrypt
    python shanghai_ini_tool.py show input.ini               # print only
    no args = file picker dialog
"""
import sys
import os

ROL3 = lambda b: ((b << 3) | (b >> 5)) & 0xFF
ROR3 = lambda b: ((b >> 3) | (b << 5)) & 0xFF


def decrypt(data: bytes) -> bytes:
    if not data:
        raise ValueError("empty file")
    if data[0] != 0x00:
        print("[i] first byte is not 0x00, file may already be plain text, returning as-is")
        return data
    return bytes(ROL3(b) for b in data[1:])


def encrypt(plain: bytes) -> bytes:
    return bytes([0x00]) + bytes(ROR3(b) for b in plain)


def show(data: bytes):
    plain = decrypt(data)
    for enc in ("gbk", "utf-8"):
        try:
            print(plain.decode(enc))
            return
        except UnicodeDecodeError:
            continue
    print(plain.decode("gbk", errors="replace"))


def main():
    argv = sys.argv[1:]
    if not argv:
        import tkinter as tk
        from tkinter import filedialog, simpledialog
        root = tk.Tk()
        root.withdraw()
        src = filedialog.askopenfilename(
            title="Select INI file",
            filetypes=[("INI files", "*.ini"), ("All files", "*.*")])
        if not src:
            return
        choice = simpledialog.askstring(
            "Action", "1=decrypt  2=encrypt  3=show", initialvalue="1")
        if choice == "1":
            out = os.path.splitext(src)[0] + "_decrypted.ini"
            open(out, "wb").write(decrypt(open(src, "rb").read()))
            print(f"decrypted -> {out}")
        elif choice == "2":
            out = os.path.splitext(src)[0] + "_encrypted.ini"
            open(out, "wb").write(encrypt(open(src, "rb").read()))
            print(f"encrypted -> {out}")
        elif choice == "3":
            show(open(src, "rb").read())
        else:
            print("invalid action")
        return

    cmd, src = argv[0].lower(), argv[1]
    data = open(src, "rb").read()

    if cmd in ("de", "decrypt", "d", "1"):
        out = argv[2] if len(argv) > 2 else os.path.splitext(src)[0] + "_decrypted.ini"
        out_data = decrypt(data)
        tag = "decrypted"
    elif cmd in ("en", "encrypt", "e", "2"):
        out = argv[2] if len(argv) > 2 else os.path.splitext(src)[0] + "_encrypted.ini"
        out_data = encrypt(data)
        tag = "encrypted"
    elif cmd in ("show", "v", "3"):
        show(data)
        return
    else:
        print(__doc__)
        return

    open(out, "wb").write(out_data)
    print(f"{tag} -> {out}  ({len(out_data)} bytes)")


if __name__ == "__main__":
    main()
