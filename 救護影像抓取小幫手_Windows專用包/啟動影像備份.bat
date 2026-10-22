@echo off
pushd "%~dp0"
title 救護影像抓取小幫手-十九甲分隊

where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw video_backup.py
    popd
    exit
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    start "" python video_backup.py
    popd
    exit
)

echo.
echo [錯誤] 本台電腦找不到 Python！
echo --------------------------------------------------------
echo 請先安裝 Python 3（安裝時請勾選 Add Python to PATH），
echo 安裝完成後即可直接點擊使用。
echo ========================================================
pause
popd
