@echo off
cd /d "%~dp0"
set PORT=18765
rem 只在本机用保持 127.0.0.1;部署到服务器供多人或远程采集端访问时改成 0.0.0.0
set HOST=127.0.0.1
rem 首次启动会生成管理员密码,写在 data\初始管理员密码.txt;也可以取消下一行注释直接指定
rem set ADMIN_PASSWORD=your-password

if not exist .venv\Scripts\python.exe python -m venv .venv
if not exist data\.deps_v2 (
  echo Installing dependencies...
  .venv\Scripts\python -m pip install -r requirements.txt || goto :fail
  if not exist data mkdir data
  echo ok> data\.deps_v2
)

start "" http://127.0.0.1:%PORT%
echo User:  http://127.0.0.1:%PORT%
echo Admin: http://127.0.0.1:%PORT%/admin
.venv\Scripts\python -m uvicorn app.main:app --host %HOST% --port %PORT%
pause
exit /b

:fail
echo.
echo Install failed. Please send the error above to Claude.
pause
