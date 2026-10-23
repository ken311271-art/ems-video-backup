# -*- coding: utf-8 -*-
import subprocess
import sys
import os

print("=" * 65)
print(" 救護影像抓取小幫手 - Windows 執行檔 (EXE) 自動打包工具")
print("=" * 65)
print(" 正在檢查並自動安裝打包套件 (PyInstaller)...")
subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"])

print("")
print("請選擇您希望打包的模式：")
print("  [1] 【極速啟動版 - 目錄模式】(強烈推薦！點兩下 1 秒內秒開，公務機防毒不卡頓)")
print("  [2] 【單一 EXE 檔模式】(單一檔案方便攜帶，但每次打開需解壓 5~10 秒)")
print("=" * 65)

choice = input("請輸入選項 [預設 1]: ").strip()
if choice == "2":
    mode_name = "【單一獨立 EXE 執行檔】"
    extra_flags = ["--onefile"]
else:
    mode_name = "【極速啟動目錄版】"
    extra_flags = ["--onedir"]

print(f"\n正在將 video_backup.py 打包為 {mode_name}...")
print("封裝中，大約需要 30~60 秒，請稍候...\n")

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm",
    "--windowed",
    "--name", "救護影像抓取小幫手",
] + extra_flags + ["video_backup.py"]

res = subprocess.run(cmd)

print("")
print("=" * 65)
if choice == "2":
    target_path = os.path.join("dist", "救護影像抓取小幫手.exe")
else:
    target_path = os.path.join("dist", "救護影像抓取小幫手")

if os.path.exists(target_path):
    print(" [打包成功！]")
    print(" ---------------------------------------------------------------")
    print(f" 產出路徑：{target_path}")
    if choice != "2":
        print(" 提示：進入該資料夾，點擊『救護影像抓取小幫手.exe』即可 1 秒極速啟動！")
        print(" 亦可對該 exe 點右鍵 -> 傳送到桌面建立捷徑。")
    else:
        print(" 提示：您可以直接複製此單一 EXE 檔至任何 Windows 公務機使用。")
else:
    print(" 打包可能未完成，請檢視上方提示訊息。")
print("=" * 65)
input("\n請按 Enter 鍵結束...")
