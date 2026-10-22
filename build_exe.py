# -*- coding: utf-8 -*-
import subprocess
import sys
import os

print("=" * 60)
print(" 正在檢查並自動安裝打包套件 (PyInstaller)...")
print("=" * 60)
subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"])

print("")
print("=" * 60)
print(" 正在將 video_backup.py 打包為【單一獨立 EXE 執行檔】...")
print(" (封裝中，大約需要 30~60 秒，請稍候...)")
print("=" * 60)

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm",
    "--onefile",
    "--windowed",
    "--name", "救護影像抓取小幫手",
    "video_backup.py"
]
res = subprocess.run(cmd)

print("")
print("=" * 60)
exe_path = os.path.join("dist", "救護影像抓取小幫手.exe")
if os.path.exists(exe_path):
    print(" [打包成功！]")
    print(" --------------------------------------------------------")
    print(" 單一獨立 EXE 檔案已產生：")
    print(f" 路徑：{exe_path}")
    print("")
    print(" 您可以直接把這顆 EXE 檔複製到桌面或任何 Windows 電腦使用，")
    print(" 其他公務電腦完全不需要再安裝 Python！")
else:
    print(" 打包可能未完成，請檢視上方提示訊息。")
print("=" * 60)
input("\n請按 Enter 鍵結束...")
