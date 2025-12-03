# GPUTasker 后台手动部署指南 (Conda环境版本)

## 概述

本指南提供了几种GPUTasker的后台部署方案，专为使用conda环境的部署配置，以替代tmux的临时方案。

## 当前状态检查

✅ **已完成的任务:**
1. running_log文件夹已备份为: `running_log_backup_20250716_042434.tar.gz` (7.8M)
2. 原始running_log文件夹已删除
3. 检测到gputasker当前未运行
4. ✅ **已配置conda环境支持**

## Conda环境配置

**当前配置:**
- Conda路径: `/home/nfs/share-yjy/miniconda3`
- 环境名称: `gputasker`
- Django版本: 4.2.18

## 部署方案

### 方案一：使用Shell脚本（推荐，支持conda环境）

#### 1. 使用提供的脚本

已创建四个管理脚本：

- `start_gputasker.sh` - 启动服务（支持conda环境）
- `stop_gputasker.sh` - 停止服务  
- `status_gputasker.sh` - 检查状态（显示conda环境信息）
- `check_conda_env.sh` - 检查conda环境配置

#### 2. 使用方法

```bash
# 检查conda环境（首次使用建议）
./check_conda_env.sh

# 启动GPUTasker
./start_gputasker.sh

# 检查状态
./status_gputasker.sh

# 停止GPUTasker
./stop_gputasker.sh
```

#### 3. 特点

- ✅ 自动激活conda环境 `gputasker`
- ✅ 自动后台运行（使用nohup）
- ✅ 自动保存PID文件用于管理
- ✅ 日志文件分离（Django和调度器独立）
- ✅ 支持服务重启
- ✅ conda环境状态检查

### 方案二：使用systemd服务（生产环境推荐，支持conda环境）

#### 1. 安装systemd服务

```bash
# 复制服务文件到systemd目录
sudo cp gputasker-web.service /etc/systemd/system/
sudo cp gputasker-scheduler.service /etc/systemd/system/

# 重新加载systemd
sudo systemctl daemon-reload

# 启用服务（开机自启）
sudo systemctl enable gputasker-web.service
sudo systemctl enable gputasker-scheduler.service
```

#### 2. 服务管理

```bash
# 启动服务
sudo systemctl start gputasker-web.service
sudo systemctl start gputasker-scheduler.service

# 停止服务
sudo systemctl stop gputasker-scheduler.service
sudo systemctl stop gputasker-web.service

# 检查状态
sudo systemctl status gputasker-web.service
sudo systemctl status gputasker-scheduler.service

# 查看日志
sudo journalctl -u gputasker-web.service -f
sudo journalctl -u gputasker-scheduler.service -f
```

#### 3. 特点

- ✅ 系统级服务管理
- ✅ 开机自启动
- ✅ 自动重启（进程崩溃时）
- ✅ 完整的日志管理
- ✅ 服务依赖管理
- ✅ 自动激活conda环境

### 方案三：使用screen（类似tmux的方案，支持conda环境）

```bash
# 启动screen会话（带conda环境）
screen -dmS gputasker-web bash -c 'cd /home/nfs/d2022-yjy/gputasker && source /home/nfs/share-yjy/miniconda3/bin/activate && conda activate gputasker && python manage.py runserver --insecure 0.0.0.0:8888'
screen -dmS gputasker-scheduler bash -c 'cd /home/nfs/d2022-yjy/gputasker && source /home/nfs/share-yjy/miniconda3/bin/activate && conda activate gputasker && python main.py'

# 查看运行的会话
screen -ls

# 连接到会话
screen -r gputasker-web
screen -r gputasker-scheduler

# 停止服务
screen -S gputasker-web -X quit
screen -S gputasker-scheduler -X quit
```

## 推荐部署流程

### 1. 首次部署（使用脚本方案）

```bash
# 1. 确保在gputasker目录
cd /home/nfs/d2022-yjy/gputasker

# 2. 检查conda环境配置
./check_conda_env.sh

# 3. 启动服务
./start_gputasker.sh

# 4. 检查状态
./status_gputasker.sh

# 5. 访问Web界面
# http://your_server_ip:8888/admin
```

### 2. 日常管理

```bash
# 检查服务状态
./status_gputasker.sh

# 重启服务
./stop_gputasker.sh
./start_gputasker.sh

# 查看日志
tail -f server_log/django.log
tail -f server_log/scheduler.log
```

### 3. 升级部署到systemd（生产环境）

当测试稳定后，可以升级到systemd服务：

```bash
# 1. 停止脚本方式的服务
./stop_gputasker.sh

# 2. 安装systemd服务
sudo cp gputasker-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable gputasker-web.service gputasker-scheduler.service

# 3. 启动systemd服务
sudo systemctl start gputasker-web.service
sudo systemctl start gputasker-scheduler.service
```

## 故障排除

### 常见问题

1. **Conda环境问题**
   ```bash
   # 检查conda环境
   ./check_conda_env.sh
   
   # 如果环境不存在，创建环境
   conda create -n gputasker python=3.8
   conda activate gputasker
   pip install django django-simpleui
   ```

2. **端口被占用**
   ```bash
   # 查找占用端口8888的进程
   sudo lsof -i :8888
   # 或者
   ss -tlnp | grep :8888
   ```

3. **Python模块缺失**
   ```bash
   # 在conda环境中安装依赖
   source /home/nfs/share-yjy/miniconda3/bin/activate
   conda activate gputasker
   pip install django django-simpleui
   ```

4. **权限问题**
   ```bash
   # 确保用户有执行权限
   chmod +x *.sh
   ```

5. **数据库问题**
   ```bash
   # 在conda环境中重新初始化数据库
   source /home/nfs/share-yjy/miniconda3/bin/activate
   conda activate gputasker
   python manage.py makemigrations
   python manage.py migrate
   ```

### 日志位置

- Django日志: `server_log/django.log`
- 调度器日志: `server_log/scheduler.log`
- PID文件: `server_log/*.pid`

## 测试结果

✅ **已验证可用:**

```bash
$ ./status_gputasker.sh
=== GPU Tasker 服务状态 ===
Conda环境: gputasker
Conda路径: /home/nfs/share-yjy/miniconda3

✓ Conda环境 'gputasker' 存在

Django Web服务状态:
  ✓ 运行中 (PID: 2154070)
  ✓ 端口8888正在监听

GPU任务调度器状态:
  ✓ 运行中 (PID: 2154188)
```

## 与tmux方案的对比

| 特性 | tmux | shell脚本+conda | systemd+conda |
|------|------|-----------------|---------------|
| 设置复杂度 | 简单 | 简单 | 中等 |
| 稳定性 | 中等 | 高 | 最高 |
| 开机自启 | 需手动 | 需手动 | 自动 |
| 日志管理 | 基础 | 良好 | 完善 |
| 进程管理 | 手动 | 自动 | 系统级 |
| 环境隔离 | 需手动激活 | 自动激活 | 自动激活 |
| 生产就绪 | 否 | 是 | 是 |

推荐使用shell脚本+conda方案进行日常开发和测试，systemd+conda方案用于生产环境部署。 