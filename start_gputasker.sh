#!/bin/bash

# GPUTasker启动脚本 (使用conda环境)
GPUTASKER_DIR="/home/nfs/d2022-yjy/gputasker"
LOG_DIR="$GPUTASKER_DIR/server_log"
CONDA_PATH="/home/nfs/share-yjy/miniconda3"
CONDA_ENV="gputasker"

# 创建日志目录
mkdir -p "$LOG_DIR"

cd "$GPUTASKER_DIR"

echo "启动GPU Tasker服务..."

# 激活conda环境的命令
ACTIVATE_CMD="source $CONDA_PATH/bin/activate && conda activate $CONDA_ENV"

# 启动Django Web服务
echo "启动Django Web服务 (端口8888)..."
nohup bash -c "$ACTIVATE_CMD && python manage.py runserver --insecure 0.0.0.0:8888" > "$LOG_DIR/django.log" 2>&1 &
DJANGO_PID=$!
echo "Django PID: $DJANGO_PID"

# 等待Django启动
sleep 5

# 启动主调度器
echo "启动GPU任务调度器..."
nohup bash -c "$ACTIVATE_CMD && python main.py" > "$LOG_DIR/scheduler.log" 2>&1 &
SCHEDULER_PID=$!
echo "Scheduler PID: $SCHEDULER_PID"

# 保存PID到文件
echo "$DJANGO_PID" > "$LOG_DIR/django.pid"
echo "$SCHEDULER_PID" > "$LOG_DIR/scheduler.pid"

echo "GPU Tasker启动完成!"
echo "Django Web服务: http://localhost:8888"
echo "日志文件:"
echo "  Django: $LOG_DIR/django.log"
echo "  Scheduler: $LOG_DIR/scheduler.log"
echo "PID文件:"
echo "  Django: $LOG_DIR/django.pid"
echo "  Scheduler: $LOG_DIR/scheduler.pid"
echo ""
echo "使用conda环境: $CONDA_ENV"
echo "检查状态: ./status_gputasker.sh" 