@echo off
chcp 65001 >nul
title Campus Officer · 校园事务官

REM ============================================================
REM  Campus Officer 一键启动（Windows）
REM  1) 检查 Python 与依赖
REM  2) 拉起教务业务后端 (FastAPI, 端口 8811)
REM  3) 拉起 Streamlit 界面 (端口 8501)
REM ============================================================

cd /d "%~dp0"

echo.
echo ==========================================
echo   Campus Officer · 校园事务官
echo ==========================================
echo.

REM --- 1. 检查 Python ---
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python 命令，请先安装 Python 3.10+ 并加入 PATH。
    pause
    exit /b 1
)

REM --- 2. 检查依赖 ---
python -c "import streamlit, fastapi, langgraph, mcp, jieba, playwright" >nul 2>nul
if errorlevel 1 (
    echo [提示] 缺少依赖，正在安装（首次运行需要几分钟）...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

REM --- 3. 检查 .env ---
if not exist ".env" (
    echo [提示] 未找到 .env，正在从 .env.example 复制一份模板...
    copy ".env.example" ".env" >nul
    echo        请打开 .env 填入 DASHSCOPE_API_KEY（不填也能用规则降级引擎运行）。
    echo.
)

REM --- 4. 拉起后端 ---
echo [1/2] 启动教务业务后端 http://127.0.0.1:8811 ...
start "Campus-API" cmd /k "python backend\main.py"
timeout /t 4 /nobreak >nul

REM --- 5. 拉起界面 ---
echo [2/2] 启动 Streamlit 界面 http://localhost:8501 ...
echo.
echo ==========================================
echo   界面已启动，浏览器打开 http://localhost:8501
echo   关闭本窗口或按 Ctrl+C 即可停止
echo ==========================================
echo.

python -m streamlit run app.py --server.port 8501 --server.address localhost --server.headless true

pause
