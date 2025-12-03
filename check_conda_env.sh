#!/bin/bash

# Conda环境检查脚本
CONDA_PATH="/home/nfs/share-yjy/miniconda3"
CONDA_ENV="gputasker"

echo "=== Conda环境检查 ==="

# 检查conda路径是否存在
if [ -d "$CONDA_PATH" ]; then
    echo "✓ Conda安装路径存在: $CONDA_PATH"
else
    echo "✗ Conda安装路径不存在: $CONDA_PATH"
    exit 1
fi

# 检查指定环境是否存在
if [ -d "$CONDA_PATH/envs/$CONDA_ENV" ]; then
    echo "✓ Conda环境存在: $CONDA_ENV"
else
    echo "✗ Conda环境不存在: $CONDA_ENV"
    echo "可用的conda环境:"
    ls -la "$CONDA_PATH/envs/" 2>/dev/null || echo "  无法列出环境"
    exit 1
fi

# 测试激活环境并检查Django
echo "测试激活conda环境并检查Django..."
source "$CONDA_PATH/bin/activate"
conda activate "$CONDA_ENV"

if python -c "import django; print('Django version:', django.get_version())" 2>/dev/null; then
    echo "✓ Django在conda环境中可用"
else
    echo "✗ Django在conda环境中不可用"
    echo "请在conda环境中安装Django:"
    echo "  conda activate $CONDA_ENV"
    echo "  pip install django django-simpleui"
    exit 1
fi

echo "✓ Conda环境检查通过" 