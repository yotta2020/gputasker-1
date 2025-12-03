#!/bin/bash

# GPUTasker状态检查脚本 (conda环境版本)
GPUTASKER_DIR="/home/nfs/d2022-yjy/gputasker"
LOG_DIR="$GPUTASKER_DIR/server_log"
CONDA_PATH="/home/nfs/share-yjy/miniconda3"
CONDA_ENV="gputasker"

echo "=== GPU Tasker 服务状态 ==="
echo "Conda环境: $CONDA_ENV"
echo "Conda路径: $CONDA_PATH"
echo ""

# 检查conda环境是否存在
if [ -d "$CONDA_PATH/envs/$CONDA_ENV" ]; then
    echo "✓ Conda环境 '$CONDA_ENV' 存在"
else
    echo "⚠ Conda环境 '$CONDA_ENV' 不存在"
fi
echo ""

# 检查Django服务
echo "Django Web服务状态:"
if [ -f "$LOG_DIR/django.pid" ]; then
    DJANGO_PID=$(cat "$LOG_DIR/django.pid")
    if kill -0 "$DJANGO_PID" 2>/dev/null; then
        echo "  ✓ 运行中 (PID: $DJANGO_PID)"
        # 检查端口
        if command -v ss >/dev/null 2>&1; then
            if ss -tlnp | grep :8888 >/dev/null; then
                echo "  ✓ 端口8888正在监听"
            else
                echo "  ⚠ 端口8888未监听"
            fi
        fi
    else
        echo "  ✗ 未运行 (PID文件存在但进程已死)"
        rm -f "$LOG_DIR/django.pid"
    fi
else
    echo "  ✗ 未运行 (无PID文件)"
fi

echo ""

# 检查调度器服务
echo "GPU任务调度器状态:"
if [ -f "$LOG_DIR/scheduler.pid" ]; then
    SCHEDULER_PID=$(cat "$LOG_DIR/scheduler.pid")
    if kill -0 "$SCHEDULER_PID" 2>/dev/null; then
        echo "  ✓ 运行中 (PID: $SCHEDULER_PID)"
    else
        echo "  ✗ 未运行 (PID文件存在但进程已死)"
        rm -f "$LOG_DIR/scheduler.pid"
    fi
else
    echo "  ✗ 未运行 (无PID文件)"
fi

echo ""

# 显示最近的日志
echo "最近的日志信息:"
if [ -f "$LOG_DIR/django.log" ]; then
    echo "Django日志 (最后5行):"
    tail -5 "$LOG_DIR/django.log" | sed 's/^/  /'
else
    echo "Django日志文件不存在"
fi

echo ""

if [ -f "$LOG_DIR/scheduler.log" ]; then
    echo "调度器日志 (最后5行):"
    tail -5 "$LOG_DIR/scheduler.log" | sed 's/^/  /'
else
    echo "调度器日志文件不存在"
fi 