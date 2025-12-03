#!/bin/bash

# GPUTasker停止脚本 (conda环境版本)
GPUTASKER_DIR="/home/nfs/d2022-yjy/gputasker"
LOG_DIR="$GPUTASKER_DIR/server_log"

echo "停止GPU Tasker服务..."

# 停止Django服务
if [ -f "$LOG_DIR/django.pid" ]; then
    DJANGO_PID=$(cat "$LOG_DIR/django.pid")
    if kill -0 "$DJANGO_PID" 2>/dev/null; then
        echo "停止Django服务 (PID: $DJANGO_PID)..."
        kill "$DJANGO_PID"
        rm -f "$LOG_DIR/django.pid"
    else
        echo "Django服务未运行"
        rm -f "$LOG_DIR/django.pid"
    fi
else
    echo "未找到Django PID文件"
fi

# 停止调度器服务
if [ -f "$LOG_DIR/scheduler.pid" ]; then
    SCHEDULER_PID=$(cat "$LOG_DIR/scheduler.pid")
    if kill -0 "$SCHEDULER_PID" 2>/dev/null; then
        echo "停止调度器服务 (PID: $SCHEDULER_PID)..."
        kill "$SCHEDULER_PID"
        rm -f "$LOG_DIR/scheduler.pid"
    else
        echo "调度器服务未运行"
        rm -f "$LOG_DIR/scheduler.pid"
    fi
else
    echo "未找到调度器PID文件"
fi

# 强制杀死相关进程（更精确的匹配）
echo "检查并清理残留进程..."
pkill -f "manage.py runserver.*8888"
pkill -f "gputasker.*main.py"

echo "GPU Tasker已停止" 