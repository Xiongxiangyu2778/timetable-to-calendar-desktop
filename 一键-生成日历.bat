@echo off
title 早八自动化 - 课表转日历
cd /d "%~dp0"
chcp 65001 >nul

set "PY="
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY if exist "C://Python311//python.exe" set "PY=C://Python311//python.exe"
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PY set "PY=python"

echo ============================================================
echo  TIMETABLE TO CALENDAR
echo  1) fill in the file  课表.txt      (text mode)
echo  2) OR drag a .pdf timetable onto this .bat file
echo  3) this script builds  我的课表.ics
echo  4) send the ics to your phone, open it with Calendar
echo ============================================================
echo.
if /i "%~x1"==".pdf" (
  %PY% -c "import pymupdf" 2>nul || (
    echo 第一次处理 PDF，正在安装解析库 pymupdf ...
    %PY% -m pip install --no-input pymupdf
  )
)
%PY% 课表转日历.py %*
echo.
pause
