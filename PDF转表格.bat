@echo off
title PDF 课表转表格
cd /d "%~dp0"
chcp 65001 >nul

if "%~1"=="" (
  echo ============================================================
  echo  请把 PDF 课表文件直接拖到本文件图标上
  echo  会生成  PDF同名.csv / PDF同名.xlsx / PDF同名_课表.txt
  echo ============================================================
  echo.
  pause
  exit /b
)

set "PY="
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY if exist "C://Python311//python.exe" set "PY=C://Python311//python.exe"
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PY set "PY=python"

%PY% -c "import pymupdf" 2>nul || (
  echo 第一次使用，正在安装 PDF 解析库 pymupdf ...
  %PY% -m pip install --no-input pymupdf openpyxl
)
%PY% pdf转表格.py "%~1"
echo.
pause
