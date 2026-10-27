#!/usr/bin/env bash
# ============================================================
#  Campus Officer 一键启动（macOS / Linux）
# ============================================================
set -e

cd "$(dirname "$0")"

echo "=========================================="
echo "  Campus Officer · 校园事务官"
echo "=========================================="
echo

# 1) 检查 Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "[错误] 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

# 2) 检查依赖
if ! python3 -c "import streamlit, fastapi, langgraph, mcp, jieba, playwright" >/dev/null 2>&1; then
    echo "[提示] 缺少依赖，正在安装..."
    python3 -m pip install -r requirements.txt
fi

# 3) 检查 .env
if [ ! -f ".env" ]; then
    echo "[提示] 未找到 .env，从 .env.example 复制模板..."
    cp .env.example .env
    echo "       请编辑 .env 填入 DASHSCOPE_API_KEY（不填也能用规则降级引擎运行）"
    echo
fi

# 4) 拉起后端
echo "[1/2] 启动教务业务后端 http://127.0.0.1:8811 ..."
python3 backend/main.py &
API_PID=$!
trap "kill $API_PID 2>/dev/null" EXIT
sleep 3

# 5) 拉起界面
echo "[2/2] 启动 Streamlit 界面 http://localhost:8501 ..."
echo
echo "=========================================="
echo "  界面已启动，浏览器打开 http://localhost:8501"
echo "=========================================="
echo

python3 -m streamlit run app.py --server.port 8501 --server.address localhost
