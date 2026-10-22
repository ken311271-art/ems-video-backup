@echo off
pushd "%~dp0"
title 救護影像抓取小幫手 - 診斷檢測與啟動

echo ========================================================
echo  救護影像抓取小幫手 - 系統診斷檢測工具 (十九甲分隊)
echo ========================================================
echo.
echo [1/3] 正在檢測 Python 執行環境...
python --version >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo [X 異常] 電腦找不到 Python 指令！
    echo --------------------------------------------------------
    echo 可能原因：
    echo 1. 安裝 Python 時未勾選「Add python.exe to PATH」。
    echo 2. 安裝完成後尚未重新開機或尚未生效。
    echo.
    echo 請重新執行 Python 安裝檔，並務必勾選「Add python.exe to PATH」。
    echo ========================================================
    pause
    popd
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do echo [V 正常] %%i 運作中

echo.
echo [2/3] 正在檢測 Tkinter 視窗套件支援...
python -c "import tkinter" >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo [X 異常] 您的 Python 缺少 Tkinter 視窗元件！
    echo --------------------------------------------------------
    echo 可能原因：安裝 Python 時取消了「tcl/tk and IDLE」選項。
    echo 請重新執行 Python 安裝程式，點選 Modify 並勾選 tcl/tk。
    echo ========================================================
    pause
    popd
    exit /b 1
)
echo [V 正常] Tkinter 視窗元件支援正常！

echo.
echo [3/3] 正在為您啟動主程式... (若有任何錯誤會保留在下方)
echo --------------------------------------------------------
python video_backup.py
if %errorlevel% neq 0 (
    echo.
    echo ========================================================
    echo [!] 程式中斷退出 (代碼: %errorlevel%)
    echo     若上方有顯示英文報錯訊息，請拍照或複製提供給維護人員。
    echo ========================================================
)
echo.
pause
popd
