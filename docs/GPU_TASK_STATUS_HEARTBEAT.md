# GPU任务状态与心跳上报机制说明

本文档解释：master 如何获取 GPU 任务状态、当前实现的工作方式、以及改造为“像 GPU 信息一样由 node 定期主动上报”的心跳机制后，状态如何更新与在 node 失联时如何处理。

## 1. 现状：任务状态如何产生（改造前/基础逻辑）

### 1.1 调度与启动

- master 侧的 scheduler 入口是 [main.py](main.py)
- scheduler 每个循环会：
  1) （可选）更新 GPU 信息（仅当 `GPUTASKER_GPU_UPDATE_MODE=ssh` 时通过 SSH 扫描；默认 `report` 不扫描）
  2) 从数据库挑选 `GPUTask.status=准备就绪(0)` 的任务
  3) 通过原子更新把任务置为 `调度中(-3)`，避免并发重复启动
  4) 启动线程执行 `task.utils.run_task(task_id)`

### 1.2 运行中/已完成/失败如何判定

- `run_task()` 会在 master 上通过 SSH 连接到目标 node，并在 node 上启动实际训练命令。
- master 侧会等待 SSH 子进程退出，拿到返回码 `return_code`：
  - `return_code==0` => 标记 `已完成(2)`
  - `return_code!=0` => 标记 `运行失败(-1)`
- 当任务刚启动成功后，master 立即把：
  - `GPUTask.status` 置为 `运行中(1)`
  - 创建/更新一条 `GPUTaskRunningLog` 作为运行记录

### 1.3 现状痛点（为什么需要心跳）

现有模式下，master 主要依据“SSH 进程是否结束”推断任务结束。

但是实际训练进程在 node 上往往会被 `setsid`/进程组方式脱离当前 SSH 会话：
- node 侧训练进程可能继续运行
- master 侧 SSH 连接可能因为网络、网卡、路由、master 重启等原因中断

这会导致一种风险：**master 将任务误判为失败/结束，但 node 上任务还在跑**。

因此，需要补上“运行中任务心跳”：让 node 主动、定期告诉 master “我这个任务仍在运行”。

## 2. 改造后：node 主动上报任务心跳（与 GPU 信息上报类似）

### 2.1 总体数据流

1) master 仍负责调度并通过 SSH 启动任务
2) 任务在 node 启动时，会写入一个“运行任务元数据文件”到 node 本地目录
3) node 上的 `agent/gpu_agent.py`（原本只上报 GPU）会额外扫描这些元数据文件，并定期上报给 master
4) master 收到心跳后更新 `GPUTaskRunningLog.last_heartbeat_at`

### 2.2 node 侧：运行任务元数据文件

- 写入位置：`~/.gputasker/running_tasks/<running_log_id>.json`
- 写入时机：任务启动脚本最前置（在训练命令执行前）
- 删除时机：任务退出时（通过 `trap EXIT` 自动删除）

文件内容示例（字段可能是字符串，agent 会转成 int 上报）：

```json
{"running_log_id":123,"remote_pid":"4567","remote_pgid":"4567","timestamp":"1734220000"}
```

### 2.3 node 侧：agent 定期上报

- agent 仍按 `GPUTASKER_REPORT_INTERVAL` 秒循环
- 每轮循环会：
  - 上报 GPU：`POST /api/v1/report_gpu/`
  - 上报任务心跳：`POST /api/v1/report_tasks/`

环境变量：
- `GPUTASKER_TASKS_API_URL`：任务心跳接口（默认由 `GPUTASKER_SERVER_URL` 把 `/report_gpu/` 替换成 `/report_tasks/`）
- `GPUTASKER_REPORT_TASKS`：是否启用任务心跳（默认 `1`）
- `GPUTASKER_RUNNING_TASKS_DIR`：运行任务元数据目录（默认 `~/.gputasker/running_tasks`）

### 2.4 master 侧：心跳接收接口

- 接口：`POST /api/v1/report_tasks/`
- 鉴权：复用 `GPUServer.report_token`（与 `report_gpu` 相同）
- 作用：
  - 更新 `GPUServer.last_report_at`（可视为节点存活信号之一）
  - 更新 `GPUTaskRunningLog.last_heartbeat_at = now`
  - 若运行记录被标记为“节点失联”，收到心跳会自动恢复为“运行中”

请求示例：

```json
{
  "token": "<server_report_token>",
  "tasks": [
    {"running_log_id": 123, "remote_pid": 4567, "remote_pgid": 4567}
  ],
  "timestamp": 1734220000
}
```

## 3. node 失联时：状态应该是什么？如何处理？

### 3.1 状态定义

新增状态：
- `GPUTask.status = -4`：节点失联
- `GPUTaskRunningLog.status = -2`：节点失联

语义：
- “节点失联” **不是** “运行失败”，表示 master 暂时拿不到可靠心跳/连接信息，无法确认任务是否仍在跑。

### 3.2 失联判定策略（默认）

master scheduler 每轮循环会检查运行中任务：

- 若某条 `GPUTaskRunningLog.status==运行中(1)`
- 且 `now - last_heartbeat_at > GPUTASKER_TASK_HEARTBEAT_STALE_SECONDS`（默认 180 秒）

则：
- 将该 running_log 标记为 `节点失联(-2)`
- 若对应 task 仍为 `运行中(1)`，则 task 标记为 `节点失联(-4)`

兼容性：
- 为避免升级瞬间误判，**仅当 `last_heartbeat_at` 已有值**（说明该任务进入了心跳体系）才会触发失联迁移。

### 3.3 失联后的处理策略（默认实现）

- 不自动释放 GPU 锁（`GPUInfo.use_by_self/busy_by_log_id`），避免 node 实际仍在跑时发生 GPU 复用冲突
- 若 node 恢复并继续上报心跳：
  - running_log 自动恢复为 `运行中(1)`
  - task 若处于 `节点失联(-4)` 会恢复为 `运行中(1)`
- 人工处置：管理员可在运行记录页执行“结束进程”操作（会尝试 SSH kill 远端 pgid/pid；node 真离线时可能失败）

可调参数：
- `GPUTASKER_TASK_HEARTBEAT_STALE_SECONDS`：心跳超时秒数

## 4. 关键代码位置

- scheduler 主循环： [main.py](main.py)
- 任务启动/状态更新： [task/utils.py](task/utils.py)
- 任务心跳接收接口： [task/views.py](task/views.py)
- node agent（GPU + 任务心跳）： [agent/gpu_agent.py](agent/gpu_agent.py)
- GPU 上报接口（鉴权 token/last_report_at）： [gpu_info/views.py](gpu_info/views.py)

