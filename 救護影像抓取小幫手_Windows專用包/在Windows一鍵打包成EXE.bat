@echo off
pushd "%~dp0"
python build_exe.py
if %errorlevel% neq 0 (
    echo.
    echo Python execution failed. Please make sure Python is installed and in PATH.
    pause
)
popd
