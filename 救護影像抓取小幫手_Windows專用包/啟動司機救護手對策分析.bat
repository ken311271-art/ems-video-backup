@echo off
chcp 65001 >nul
pushd "%~dp0"
title 司機救護手對策分析實驗室

rem 1. 檢查同目錄下是否存在主程式
set "TARGET_PYW="
if exist "%~dp0role_analyzer_lab.pyw" set "TARGET_PYW=%~dp0role_analyzer_lab.pyw"
if exist "%~dp0role_analyzer_lab.py" if "%TARGET_PYW%"=="" set "TARGET_PYW=%~dp0role_analyzer_lab.py"

if "%TARGET_PYW%"=="" (
    echo.
    echo =======================================================================
    echo  【錯誤】找不到主程式檔案 role_analyzer_lab.pyw！
    echo =======================================================================
    echo.
    echo  原因說明：
    echo    您目前只有單獨下載本「.bat 啟動捷徑」，缺少了主程式「role_analyzer_lab.pyw」。
    echo.
    echo  【解決方法（任選一種）】：
    echo.
    echo    方法一 (強烈推薦)：
    echo      請直接下載「救護影像抓取小幫手_Windows專用包.rar」(或 .zip)，
    echo      「整包解壓縮」後，直接雙擊裡面的「啟動司機救護手對策分析.bat」即可！
    echo.
    echo    方法二 (單檔放一起)：
    echo      請回到下載區同時下載「role_analyzer_lab.pyw」，
    echo      並將它與本 .bat 檔放在「同一個資料夾」！
    echo.
    echo    方法三 (免解壓單檔版)：
    echo      請下載「司機救護手對策分析_單檔獨立版.bat」，
    echo      那是把整個主程式包在裡面的單一檔案，下載後直接點即可！
    echo.
    echo =======================================================================
    echo.
    pause
    popd
    exit /b 1
)

rem 2. 檢查 Python 環境
set "PY_CMD="
where pythonw >nul 2>nul && set "PY_CMD=pythonw"
if "%PY_CMD%"=="" where python >nul 2>nul && set "PY_CMD=python"
if "%PY_CMD%"=="" where py >nul 2>nul && set "PY_CMD=py"

if "%PY_CMD%"=="" (
    echo.
    echo =======================================================================
    echo  【錯誤】電腦未安裝 Python 3！
    echo =======================================================================
    echo.
    echo  請至 Python 官網 (https://www.python.org) 安裝 Python 3。
    echo  ★ 安裝時請務必勾選最下方的：【Add python.exe to PATH】！
    echo.
    echo =======================================================================
    echo.
    pause
    popd
    exit /b 1
)

rem 3. 啟動主程式
if "%PY_CMD%"=="pythonw" (
    start "" pythonw "%TARGET_PYW%"
    popd
    exit
) else (
    "%PY_CMD%" "%TARGET_PYW%"
    if %errorlevel% neq 0 (
        echo.
        echo =======================================================================
        echo  程式執行時發生異常，請參考上方錯誤訊息。
        echo =======================================================================
        echo.
        pause
    )
    popd
    exit /b
)
