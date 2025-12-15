# GPU Tasker

轻量好用的GPU机群任务调度工具


## 介绍

GPU Tasker是一款GPU任务调度工具，适用于GPU机群或单机环境，科学地调度每一项任务，深度学习工作者的福音。


## 开始使用

### 环境准备

在机群环境下，将GPU Tasker安装在机群环境下的一台服务器或PC，安装GPU Tasker的服务器成为Master，其余服务器称为Node，Master可以通过ssh连接所有Node服务器。**建议Node服务器连接NAS或拥有共享目录，并连接LDAP。**

安装django、django-simpleui

```shell
pip install django django-simpleui
```

### 部署GPU Tasker

GPU Tasker支持手动部署与Docker部署。

#### 手动部署

* 在Master服务器clone本项目

```shell
git clone https://github.com/cnstark/gputasker.git
cd gputasker
```

* 编辑`gpu_tasker/settings.py`，编辑数据库等django基本设置，如果是单用户使用或机群规模较小时（或者服务器安装MySQL困难），使用sqlite即可。

* 初始化项目数据库

```shell
python manage.py makemigrations
python manage.py migrate
```

如果你从旧版本升级到包含“节点上报”功能的新版本，请务必执行 `python manage.py migrate` 应用新增迁移。

* 创建超级用户

```shell
python manage.py createsuperuser
```

根据提示输入信息，完成创建。

* 启动服务

```shell
python manage.py runserver --insecure 0.0.0.0:8888
```

* 启动主进程

```shell
python main.py
```

默认模式下，主进程会通过 SSH 扫描各个节点获取 GPU 状态。若你希望避免中心端频繁扫描（更像“节点定时汇报”模式），请参考下方“节点上报模式（推荐）”。

#### systemd 部署（手动部署的守护方式）

仓库提供了两个 systemd unit：

* `gputasker-web.service`：Django Web（8888）
* `gputasker-scheduler.service`：调度器（main.py）

安装与启动：

```shell
sudo cp gputasker-web.service /etc/systemd/system/
sudo cp gputasker-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gputasker-web.service
sudo systemctl enable --now gputasker-scheduler.service
sudo systemctl status gputasker-web.service gputasker-scheduler.service
```

切换到“节点上报模式”时：把 `gputasker-scheduler.service` 里的 `GPUTASKER_GPU_UPDATE_MODE` 改为 `report`，然后：

```shell
sudo systemctl daemon-reload
sudo systemctl restart gputasker-scheduler.service
```

#### 无 sudo 启动（推荐：普通用户部署）

如果你没有 sudo 权限（或不方便安装 systemd unit），可以直接使用仓库自带的脚本以 `nohup` 方式后台运行 Web 与 Scheduler。

在 Master 上：

```shell
cd /home/nfs/d2022-yjy/gputasker

# 启动
./start_gputasker.sh

# 查看状态（含端口/日志尾部）
./status_gputasker.sh

# 停止
./stop_gputasker.sh
```

说明：`stop_gputasker.sh` 会在停止本机 Web/Scheduler 前，先尝试通过 SSH 批量停止所有已登记的 Node agent（依赖后台配置的 SSH 账号/私钥）。

可选环境变量（影响 Scheduler `main.py`）：

```shell
# GPU 信息更新模式：ssh（默认，中心端扫描） / report（节点上报）
export GPUTASKER_GPU_UPDATE_MODE=report

# 主循环间隔（秒），默认 10
export GPUTASKER_LOOP_INTERVAL_SECONDS=30
```

可选环境变量（影响“Web 增删 Node 自动启停 agent”）：

```shell
# 是否在 Admin 保存/删除 Node 时自动通过 SSH 启动/停止 agent（默认 1）
export GPUTASKER_AUTO_NODE_AGENT=1

# Node 上 agent 的工作目录/脚本路径（默认假设 Master 的仓库路径在 Node 可见，比如 NFS）
export GPUTASKER_REMOTE_WORKDIR=/home/nfs/d2022-yjy/gputasker
export GPUTASKER_REMOTE_AGENT_PATH=/home/nfs/d2022-yjy/gputasker/agent/gpu_agent.py

# Node 多久未上报算“不可用”（秒，默认 180）；Admin 列表会显示红叉
export GPUTASKER_NODE_STALE_SECONDS=180

# Master 上报接口地址生成：优先用 GPUTASKER_SERVER_URL；否则用 master-ip/master-port 组装
# 默认 master-ip=222.20.126.169, master-port=8888
export GPUTASKER_MASTER_IP=222.20.126.169
export GPUTASKER_MASTER_PORT=8888
```

补充：当 Master 通过 SSH 启动 agent 时，会把配置写入 Node 的 `~/.gputasker/agent.env`，并把 agent 日志写入 `~/.gputasker/gpu_agent.log`。

批量管理（Master 上执行）：

```shell
python manage.py node_agents stop
python manage.py node_agents start --server-url http://<master-ip>:8888/api/v1/report_gpu/
python manage.py node_agents restart --server-url http://<master-ip>:8888/api/v1/report_gpu/
```

### 并发安全与远端 kill（重要行为说明）

* Scheduler 会先把任务从 `准备就绪(0)` 原子认领为 `调度中(-3)`，避免并发/多实例下重复启动。
* GPU 占用使用数据库层条件更新实现互斥，并记录占用归属（避免误释放）。
* `GPU任务运行记录` 的“结束进程”会优先通过 SSH kill 远端进程组（PGID），并释放该任务占用的 GPU。

#### Docker部署

* 安装[Docker](https://docs.docker.com/get-docker/)与[docker-compose](https://docs.docker.com/compose/install/)

* 在Master服务器clone本项目

```shell
git clone https://github.com/cnstark/gputasker.git
cd gputasker
```

* 启动GPUTasker

```shell
sudo docker-compose up -d
```

* 创建超级用户

注意：初次使用时需要等待初始化完成后才能创建超级用户，等待时间约30秒。当`http://your_server:8888/admin`可以正常访问后再执行：

```shell
sudo docker exec -it gputasker_django python manage.py createsuperuser
```

根据提示输入信息，完成创建。

### 基本设置

访问`http://your_server:8888/admin`，登录管理后台。

![home](.assets/home.png)

添加`用户设置`，输入服务器用户名与私钥。Master通过私钥登录Node服务器，需要将私钥添加至Node服务器`authorized_keys`。

暂只支持每个服务器使用相同的用户名，后续版本迭代可能会支持。

![home](.assets/user_config.png)

### 添加Node节点

点击`GPU服务器`，添加Node节点ip或域名，点击保存。保存后会自动更新node节点信息，包括hostname以及GPU信息

说明：

* 在“SSH 扫描模式”下，保存后会由 Master 通过 SSH 获取 hostname/GPU 信息。
* 在“节点上报模式”下，保存后不会立即刷新 GPU 信息，需要等 Node 上报后才会更新。

![home](.assets/add_server.png)

选项说明

* 是否可用：服务器当前状态是否可用。若连接失败或无法获取GPU状态则会被自动置为False并不再被调度。
* 是否可调度：服务器是否参与任务调度。若服务器有其他用途（被人独占等），手动设置此项为False，该服务器不再被调度。

### 添加任务

点击`GPU任务`，输入任务信息并保存。状态为`准备就绪`的任务会在服务器满足需求时执行。

![home](.assets/add_task.png)

选项说明

* 工作目录：执行命令时所在的工作目录。
* 命令：执行的命令。支持多行命令，如：

```shell
source venv/pytorch/bin/activate
python train.py
```

注意：使用conda环境时，由于ssh远程执行无法获取conda环境变量导致`conda activate`失败，需要先激活conda再激活虚拟环境。或者使用`python`绝对路径。例如：

```shell
source /path/to/anaconda3/bin/activate
conda activate pytorch
python train.py

# 或

/path/to/anaconda3/envs/pytorch/bin/python train.py
```

* GPU数量需求：任务所需的GPU数量。当任务被调度时，会根据所需GPU数量自动设置`CUDA_VISIBLE_DEVICES`环境变量，因此任务命令中不要手动设置`CUDA_VISIBLE_DEVICES`，避免调度失败。
* 独占显卡：当该选项为True时，只会调度没有进程占用的显卡。
* 显存需求：任务在单GPU上需要的显存。设置时保证任务可以运行即可，不需要准确。
* 利用率需求：任务在单GPU上需要的空闲利用率。

注意：显存需求和利用率需求只在`独占显卡`为False时生效，当GPU满足显存需求和利用率时会参与调度。仅用于GPU全被占满需要强占的情况，一般情况下建议勾选`独占显卡`。

* 指定服务器：选择任务运行的服务器。若该选项为空，则在所有可调度服务器中寻找满足需求的服务器；否则只在指定服务器上等待GPU满足条件时调度。

* 优先级：任务调度的优先级。功能尚未支持。
* 状态：当前任务状态。状态为`准备就绪`时，任务会被调度。

任务运行后可以通过`GPU任务运行记录`查看任务状态与Log。

## 通知设置

GPUTasker支持邮件通知，任务开始运行和结束时向用户发送邮件提醒。

### 开启邮箱SMTP功能

进入邮箱后台，开启SMTP功能，并获取SMTP密钥。不同邮件服务商配置方式不同，具体开启方法参考邮箱帮助。

### 配置邮件通知

复制`email_settings_sample.py`为`email_settings.py`。

```shell
cd gpu_tasker
cp email_settings_sample.py email_settings.py
```

编辑`email_settings.py`，填写SMTP服务器、端口、邮箱名和密码：

```python
# 以163邮箱为例

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
# SMTP服务器
EMAIL_HOST = 'smtp.163.com'
# SMTP服务器端口
EMAIL_PORT = 465

# 邮箱名
EMAIL_HOST_USER = 'xxx@163.com'
# SMTP密钥（部分邮箱与邮箱密码相同）
EMAIL_HOST_PASSWORD = 'xxx'

EMAIL_USE_SSL = True
EMAIL_USE_LOCALTIME = True
DEFAULT_FROM_EMAIL = 'GPUTasker<{}>'.format(EMAIL_HOST_USER)
SERVER_EMAIL = EMAIL_HOST_USER
```

### 配置收信邮箱

收信邮箱为Django用户`电子邮件地址`，在后台进行配置。

![user_email](.assets/user_email.png)

## 更新GPUTasker

GPUTasker可能包含数据表的改动，更新后请务必更新数据表以及**重新启动main.py**。

```shell
# 拉取最新代码
git pull

# 更新数据表
python manage.py makemigrations
python manage.py migrate

# 重新启动main.py
# 1. CTRL + C结束main.py
# 2. 重新启动
python main.py
```

## 节点上报模式（推荐）

如果你不希望 Master 频繁 SSH 扫描所有 Node（可能更像“探测行为”，也会带来密钥分发成本），可以改为让每个 Node 定时把 GPU 状态上报给 Master。

### Master 配置

1. 升级后先迁移数据库：

```shell
python manage.py migrate
```

2. 启动 Web 与 Scheduler：

```shell
python manage.py runserver --insecure 0.0.0.0:8888

# 关闭 SSH 扫描，仅使用节点上报数据
export GPUTASKER_GPU_UPDATE_MODE=report
# 可选：降低主循环频率
export GPUTASKER_LOOP_INTERVAL_SECONDS=30
python main.py
```

如果你使用 systemd 部署，可以在 scheduler 单元中设置环境变量（见 gputasker-scheduler.service）。

3. 在管理后台添加 GPU 服务器（Node）：

进入 `GPU服务器` 添加每台 Node 的 IP/port 保存后，会生成一个 `report_token`（上报鉴权用）。

### Node 部署（每台 GPU 机器）

Node 只需要能运行 `nvidia-smi` 和 Python3。

1. 准备依赖：

```shell
pip install requests
```

2. 拷贝 agent 脚本与 systemd unit：

```shell
# 假设把仓库放到 /opt/gputasker
sudo mkdir -p /opt/gputasker
sudo rsync -a <your_repo_path>/agent /opt/gputasker/

sudo mkdir -p /etc/gputasker
sudo cp /opt/gputasker/agent/agent.env.sample /etc/gputasker/agent.env
sudo nano /etc/gputasker/agent.env

sudo cp /opt/gputasker/agent/gputasker-agent.service /etc/systemd/system/gputasker-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now gputasker-agent.service
sudo systemctl status gputasker-agent.service
```

3. 在 `/etc/gputasker/agent.env` 填写：

- `GPUTASKER_SERVER_URL=http://<master_host>:8888/api/v1/report_gpu/`
- `GPUTASKER_AGENT_TOKEN=<Master后台该Node的report_token>`

#### 常见问题（Node 上报模式）

1）Node 部署必须要 sudo 吗？能不能用普通用户？有什么缺点？

不必须。上面的 systemd 安装方式需要 sudo（写入 `/etc/...`、`/etc/systemd/system`），但你也可以用普通用户权限部署：

* 方式 A：普通用户 `nohup` 后台运行（最通用，无需 sudo）

```shell
pip install requests

# 假设仓库/脚本在当前目录的 agent/
export GPUTASKER_SERVER_URL=http://<master_host>:8888/api/v1/report_gpu/
export GPUTASKER_AGENT_TOKEN=<report_token>

nohup python3 agent/gpu_agent.py > agent.log 2>&1 &
echo $! > agent.pid
```

停止：

```shell
kill $(cat agent.pid)
```

缺点：默认不会“自动重启/开机自启”，需要你自己用 crontab/screen/tmux 等方式兜底。

* 方式 B：用户级 systemd（`systemctl --user`，无需 sudo）

如果机器支持 systemd user service，你可以把 service 放到 `~/.config/systemd/user/`，然后 `systemctl --user enable --now ...`。

注意：想“开机自启”通常需要开启 lingering（`loginctl enable-linger <user>`），这一步可能需要管理员协助。

2）换成节点汇报后，页面里的“刷新”按钮是不是没用了？

不是没用，但语义变了：

* 在 `GPUTASKER_GPU_UPDATE_MODE=report` 下，GPU 数据的来源是“Node 最新一次上报写入数据库”。
* 页面“刷新”只是重新从数据库读取并显示最新记录，并不会触发 Master 去 SSH 扫描（因为扫描已经关闭）。

如果你希望“立刻更新某台 Node 的 GPU 信息”，可以在 Node 上手动运行一次 agent（或把 `GPUTASKER_REPORT_INTERVAL` 调小）。

3）如果以后 gputasker 不用了/服务关闭，各节点汇报能不能自动停？

默认不会自动停：agent 会继续循环上报，Master 不可达时会打印失败日志并重试。

你有三种处理方式：

* 手动停：
	- systemd（root 级）：`sudo systemctl stop gputasker-agent.service`
	- nohup：`kill $(cat agent.pid)`
* 配置 token 失效即退出：如果 Master 返回 401/403（token 无效），agent 会直接退出。
* 可选：连续失败自动退出：在 Node 环境变量里设置 `GPUTASKER_EXIT_AFTER_CONSECUTIVE_FAILURES=N`（例如 20），连续失败 N 次后 agent 会正常退出（exit code 0）。
	- 若你用 systemd，建议配合 `Restart=on-failure`（仓库示例已是该配置），这样“正常退出”不会被自动拉起。

### 安全建议

* 建议把上报接口放在可信内网，或使用 HTTPS；`report_token` 属于共享密钥，避免明文公网传输。
* 需要轮换 token 时，可在 Master 后台将 `report_token` 清空并保存（会自动生成新 token），然后同步更新 Node 的 env。

## 多用户说明（当前实现）

* 每个用户只能在后台看到/管理自己的 `GPU任务` 与 `用户设置`。
* `GPU服务器/GPU信息` 属于全局资源，仅管理员可见、可配置。

