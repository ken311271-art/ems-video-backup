@echo off
pushd "%~dp0"
title 司機救護手對策分析實驗室 - 啟動與診斷

echo ========================================================
echo  司機救護手對策分析實驗室 - 啟動與診斷工具
echo ========================================================
echo.

rem 1. 檢查主程式檔案
set "TARGET_PY="
if exist "%~dp0role_analyzer_lab.pyw" set "TARGET_PY=%~dp0role_analyzer_lab.pyw"
if exist "%~dp0role_analyzer_lab.py" if "%TARGET_PY%"=="" set "TARGET_PY=%~dp0role_analyzer_lab.py"

if "%TARGET_PY%"=="" (
    echo [X 異常] 找不到主程式檔案 role_analyzer_lab.pyw！
    echo --------------------------------------------------------
    echo 請確認本批次檔與 role_analyzer_lab.pyw 放在同一個資料夾。
    echo 若您是單獨下載，建議下載「救護影像抓取小幫手_Windows專用包.rar」並解壓縮使用。
    echo ========================================================
    pause
    popd
    exit /b 1
)
echo [V 正常] 已定位主程式：%TARGET_PY%

rem 2. 檢測 Python 環境
echo.
echo 正在檢測 Python 執行環境...
set "PY_CMD="
where python >nul 2>nul && set "PY_CMD=python"
if "%PY_CMD%"=="" where py >nul 2>nul && set "PY_CMD=py -3"

if "%PY_CMD%"=="" (
    echo.
    echo [X 異常] 電腦找不到 Python 指令！
    echo --------------------------------------------------------
    echo 可能原因：
    echo 1. 安裝 Python 時未勾選「Add python.exe to PATH」。
    echo 2. 電腦尚未安裝 Python 3。
    echo.
    echo 請至 https://www.python.org 下載安裝 Python 3，
    echo 安裝時請務必勾選最下方的「Add python.exe to PATH」！
    echo ========================================================
    pause
    popd
    exit /b 1
)
echo [V 正常] Python 環境正常！

rem 3. 檢測 Tkinter 視窗套件
echo.
echo 正在檢測視窗元件支援 (Tkinter)...
%PY_CMD% -c "import tkinter" >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo [X 異常] 您的 Python 缺少 Tkinter 視窗元件！
    echo --------------------------------------------------------
    echo 請重新執行 Python 安裝程式，點選 Modify 並勾選 tcl/tk。
    echo ========================================================
    pause
    popd
    exit /b 1
)
echo [V 正常] 視窗元件支援正常！

rem 4. 啟動主程式
echo.
echo 正在啟動司機救護手對策分析主程式...
echo (若程式有任何報錯訊息會保留在下方)
echo --------------------------------------------------------
%PY_CMD% "%TARGET_PY%" %*
if %errorlevel% neq 0 (
    echo.
    echo ========================================================
    echo [!] 程式中斷退出 (代碼: %errorlevel%)
    echo     若上方有顯示英文報錯訊息，請拍照或複製提供給維護人員。
    echo ========================================================
    echo.
    pause
)

popd
exit /b
