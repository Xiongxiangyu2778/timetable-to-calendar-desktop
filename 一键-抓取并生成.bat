@echo off
title 早八自动化 - 抓课表并生成日历
cd /d "%~dp0"
chcp 65001 >nul

rem ===== 1. 浏览器（调试端口 9222）=====
if not defined CHX_TEST_URL (
  netstat -ano | findstr ":9222" >nul
  if errorlevel 1 (
    echo [1/3] 正在打开浏览器，请登录教务系统并打开课表页面...
    if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
      start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\edge-timetable-profile" --no-first-run about:blank
    ) else (
      start "" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\edge-timetable-profile" --no-first-run about:blank
    )
    timeout /t 5 >nul
  ) else (
    echo [1/3] 浏览器已在运行，直接使用
  )
) else (
  echo [1/3] 测试模式，跳过浏览器启动
)

rem ===== 2. node 与 playwright-core =====
set "NODE="
if exist "%~dp0..\学习通作业助手\刷课软件\node\node.exe" set "NODE=%~dp0..\学习通作业助手\刷课软件\node\node.exe"
if not defined NODE where node >nul 2>nul && set "NODE=node"
if not defined NODE (
  echo [X] 没找到 Node.js，请先安装 Node.js 22+
  goto end
)

set "NODE_PATH="
if exist "%~dp0node_modules\playwright-core" set "NODE_PATH=%~dp0node_modules"
if not defined NODE_PATH if exist "%~dp0..\学习通作业助手\刷章节\node_modules" set "NODE_PATH=%~dp0..\学习通作业助手\刷章节\node_modules"
if not defined NODE_PATH if exist "%USERPROFILE%\.workbuddy\binaries\node\workspace\node_modules" set "NODE_PATH=%USERPROFILE%\.workbuddy\binaries\node\workspace\node_modules"

set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"

echo [2/3] 正在抓取课表...
"%NODE%" 抓课表.js
if errorlevel 1 goto end

rem ===== 3. 生成 ics =====
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY if exist "C:/Python311/python.exe" set "PY=C:/Python311/python.exe"
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PY set "PY=python"

echo [3/3] 正在生成日历文件...
%PY% 课表转日历.py 课表_抓取.txt

:end
echo.
pause
