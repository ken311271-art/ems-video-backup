@echo off
pushd "%~dp0"
title 司機救護手對策分析實驗室

where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw role_analyzer_lab.pyw
    popd
    exit
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    start "" python role_analyzer_lab.pyw
    popd
    exit
)

echo.
echo [錯誤] 系統找不到 Python！
echo --------------------------------------------------------
echo 請先安裝 Python 3（安裝時請務必勾選 Add Python to PATH），
echo 安裝完成後重新點擊即可啟動。
echo ========================================================
pause
popd
