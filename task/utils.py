import os
import signal
import subprocess
import json
import time
import traceback
import logging
import base64
import threading
import re
from django.utils import timezone

from gpu_tasker.settings import RUNNING_LOG_DIR
from .models import GPUTask, GPUTaskRunningLog
from notification.email_notification import \
    send_task_start_email, send_task_finish_email, send_task_fail_email

from gpu_info.models import GPUServer
from gpu_info.models import try_lock_gpus, release_gpus


task_logger = logging.getLogger('django.task')


def generate_ssh_cmd(host, user, exec_cmd, port=22, private_key_path=None):
    exec_cmd = exec_cmd.replace('$', '\\$')
    exec_cmd = exec_cmd.replace('"', '\\"')
    if private_key_path is None:
        cmd = "ssh -o StrictHostKeyChecking=no -p {:d} {}@{} \"{}\"".format(port, user, host, exec_cmd)
    else:
        cmd = "ssh -o StrictHostKeyChecking=no -p {:d} -i {} {}@{} \"{}\"".format(port, private_key_path, user, host, exec_cmd)
    return cmd


class RemoteProcess:
    def __init__(self, user, host, cmd, workspace="~", port=22, private_key_path=None, output_file=None):
        self.cmd = generate_ssh_cmd(host, user, "cd {} && {}".format(workspace, cmd), port, private_key_path)
        task_logger.info('cmd:\n' + self.cmd)
        self.output_file = output_file
        self._stream_thread = None
        self._first_line = None

        if output_file is not None:
            # 需要解析远端 PID/PGID，同时持续把输出写入 log 文件
            self.proc = subprocess.Popen(
                self.cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace',
            )
        else:
            self.proc = subprocess.Popen(self.cmd, shell=True)

    def pid(self):
        return self.proc.pid

    def first_line(self):
        return self._first_line

    def kill(self):
        # os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        os.kill(self.proc.pid, signal.SIGKILL)

    def get_return_code(self):
        self.proc.wait()
        return self.proc.returncode


class RemoteGPUProcess(RemoteProcess):
    def __init__(self, user, host, gpus, cmd, workspace="~", port=22, private_key_path=None, output_file=None):
        env = 'export CUDA_VISIBLE_DEVICES={}'.format(','.join(map(str, gpus)))
        cmd = 'bash -c \'{}\n{}\n\''.format(env, cmd)
        super(RemoteGPUProcess, self).__init__(user, host, cmd, workspace, port, private_key_path, output_file)


class RemoteGPUProcessGroup(RemoteProcess):
    """在远端通过 setsid 建立独立进程组，并回传 pid/pgid。

    远端进程会前置输出一行：
      __GPUTASKER_REMOTE__ pid=<pid> pgid=<pgid>
    """

    MARKER_PREFIX = '__GPUTASKER_REMOTE__'

    def __init__(self, user, host, gpus, cmd, workspace='~', port=22, private_key_path=None, output_file=None, running_log_id=None):
        env = 'export CUDA_VISIBLE_DEVICES={}'.format(','.join(map(str, gpus)))
        # 在 node 上写入“运行中任务元数据”，供 agent 扫描并上报心跳
        # 文件会在任务退出时自动删除（trap EXIT）。
        meta_prefix = ''
        if running_log_id is not None:
            try:
                rid = int(running_log_id)
            except Exception:
                rid = None
            if rid and rid > 0:
                meta_prefix = (
                    'META_DIR="$HOME/.gputasker/running_tasks"\n'
                    'mkdir -p "$META_DIR"\n'
                    'META_PATH="$META_DIR/{rid}.json"\n'
                    'REMOTE_PID="$$"\n'
                    'REMOTE_PGID="$(ps -o pgid= -p $$ | tr -d " ")"\n'
                    'cat > "$META_PATH" <<EOF\n'
                    '{{"running_log_id":{rid},"remote_pid":' + '"$REMOTE_PID"' + ',"remote_pgid":' + '"$REMOTE_PGID"' + ',"timestamp":' + '"$(date +%s)"' + '}}\n'
                    'EOF\n'
                    'trap "rm -f \"$META_PATH\"" EXIT\n'
                ).format(rid=rid)

        script = '{}\n{}\n{}\n'.format(env, meta_prefix, cmd)
        payload = base64.b64encode(script.encode('utf-8')).decode('ascii')

        # 注意：这里用单引号包裹 python -c，因此 python 代码内部避免单引号。
        py_code = (
            "import os,sys,base64; "
            "os.setsid(); "
            "script=base64.b64decode(sys.argv[1]).decode(\"utf-8\"); "
            "print(\"{} pid=%d pgid=%d\" % (os.getpid(), os.getpgrp()), flush=True); "
            "os.execv(\"/bin/bash\", [\"bash\",\"-lc\", script])"
        ).format(self.MARKER_PREFIX)

        # 不能依赖 $ 变量展开（generate_ssh_cmd 会转义 $），因此用顺序 fallback
        remote_cmd = "python3 -c '{}' {} || python -c '{}' {}".format(py_code, payload, py_code, payload)
        super(RemoteGPUProcessGroup, self).__init__(user, host, remote_cmd, workspace, port, private_key_path, output_file)

    def start_streaming(self):
        if self.output_file is None or self.proc.stdout is None:
            return
        os.makedirs(os.path.dirname(self.output_file), exist_ok=True)

        # 1) 同步读首行，便于解析远端 pid/pgid
        try:
            self._first_line = self.proc.stdout.readline()
        except Exception:
            self._first_line = None

        # 2) 启动后台线程持续 drain stdout，并写入日志文件
        def _stream_rest(stdout, path, first_line):
            with open(path, 'a', encoding='utf-8', errors='replace') as out:
                if first_line:
                    out.write(first_line)
                    if not first_line.endswith('\n'):
                        out.write('\n')
                    out.flush()
                for line in stdout:
                    out.write(line)
                    if not line.endswith('\n'):
                        out.write('\n')
                    out.flush()

        self._stream_thread = threading.Thread(
            target=_stream_rest,
            args=(self.proc.stdout, self.output_file, self._first_line),
            daemon=True,
        )
        self._stream_thread.start()

    def get_return_code(self):
        rc = super().get_return_code()
        if self._stream_thread is not None:
            try:
                self._stream_thread.join(timeout=2)
            except Exception:
                pass
        return rc


def _parse_remote_marker(line: str):
    if not line:
        return None, None
    line = line.strip()
    if not line.startswith(RemoteGPUProcessGroup.MARKER_PREFIX):
        return None, None
    remote_pid = None
    remote_pgid = None
    for part in line.split():
        if part.startswith('pid='):
            try:
                remote_pid = int(part.split('=', 1)[1])
            except Exception:
                pass
        if part.startswith('pgid='):
            try:
                remote_pgid = int(part.split('=', 1)[1])
            except Exception:
                pass
    return remote_pid, remote_pgid


def _parse_gpu_list(gpus: str):
    if not gpus:
        return []
    res = []
    for item in gpus.split(','):
        item = item.strip()
        if not item:
            continue
        try:
            res.append(int(item))
        except Exception:
            continue
    return res


def kill_running_log(running_log: GPUTaskRunningLog):
    """通过 ssh kill 远端进程组，并释放 GPU。

    设计目标：不依赖 sudo；节点账号共享时也能精确 kill 本任务。
    """
    if running_log.status not in (1, -2):
        return

    task = running_log.task
    server = running_log.server
    gpu_list = _parse_gpu_list(running_log.gpus)
    try:
        if server is not None and running_log.remote_pgid:
            # 先 TERM 再 KILL
            term_cmd = 'kill -TERM -{} 2>/dev/null || true'.format(int(running_log.remote_pgid))
            kill_cmd = 'kill -KILL -{} 2>/dev/null || true'.format(int(running_log.remote_pgid))
            p1 = RemoteProcess(
                task.user.config.server_username,
                server.ip,
                "bash -lc '{}'".format(term_cmd),
                task.workspace,
                server.port,
                task.user.config.server_private_key_path,
                output_file=None,
            )
            try:
                p1.get_return_code()
            except Exception:
                pass
            time.sleep(1)
            p2 = RemoteProcess(
                task.user.config.server_username,
                server.ip,
                "bash -lc '{}'".format(kill_cmd),
                task.workspace,
                server.port,
                task.user.config.server_private_key_path,
                output_file=None,
            )
            try:
                p2.get_return_code()
            except Exception:
                pass
        elif server is not None and running_log.remote_pid:
            cmd = 'kill -TERM {} 2>/dev/null || true; sleep 1; kill -KILL {} 2>/dev/null || true'.format(
                int(running_log.remote_pid),
                int(running_log.remote_pid),
            )
            p = RemoteProcess(
                task.user.config.server_username,
                server.ip,
                "bash -lc '{}'".format(cmd),
                task.workspace,
                server.port,
                task.user.config.server_private_key_path,
                output_file=None,
            )
            try:
                p.get_return_code()
            except Exception:
                pass
        else:
            # 最后兜底：杀 master 本地 ssh pid
            if running_log.pid and running_log.pid > 0:
                os.kill(running_log.pid, signal.SIGKILL)
    except Exception:
        task_logger.error(traceback.format_exc())
    finally:
        try:
            running_log.status = -1
            running_log.save(update_fields=['status', 'update_at'])
        except Exception:
            pass
        try:
            if task.status == 1:
                task.status = -1
                task.save(update_fields=['status', 'update_at'])
        except Exception:
            pass
        try:
            if server is not None and gpu_list:
                release_gpus(server, gpu_list, busy_by_log_id=running_log.id)
        except Exception:
            task_logger.error(traceback.format_exc())


def run_task(task_id, _available_server_unused=None):
    # 线程里重新加载，避免主线程的对象过期
    task = GPUTask.objects.select_related('user', 'assign_server', 'user__config').get(id=task_id)

    # 仅调度“准备就绪”的任务；否则清理认领锁避免卡死。
    if task.status != 0:
        try:
            GPUTask.objects.filter(id=task.id).update(dispatching_at=None)
        except Exception:
            pass
        return

    def _safe_filename(name: str, limit: int = 80) -> str:
        if not name:
            return 'task'
        s = (name or '').replace(os.sep, '_')
        s = re.sub(r'[^0-9A-Za-z._-]+', '_', s)
        s = s.strip('._-') or 'task'
        return s[:limit]

    # 选 server + GPU，并尝试原子占用
    candidate_servers = []
    if task.assign_server is not None:
        candidate_servers = [task.assign_server]
    else:
        candidate_servers = list(GPUServer.objects.all())

    server = None
    gpus = None
    running_log = None

    # 先创建 running_log 拿到 id，用于 GPU busy_by_log_id 归属
    index = task.task_logs.all().count()
    try:
        for s in candidate_servers:
            available_gpus = s.get_available_gpus(
                task.gpu_requirement,
                task.exclusive_gpu,
                task.memory_requirement,
                task.utilization_requirement,
            )
            if available_gpus is None:
                continue
            chosen = available_gpus[:task.gpu_requirement]
            log_file_path = os.path.join(
                RUNNING_LOG_DIR,
                '{:d}_{:s}_{:s}_{:d}_{:d}.log'.format(task.id, _safe_filename(task.name), s.ip, index, int(time.time()))
            )

            tmp_log = GPUTaskRunningLog(
                index=index,
                task=task,
                server=s,
                pid=-1,
                remote_pid=None,
                remote_pgid=None,
                gpus=','.join(map(str, chosen)),
                log_file_path=log_file_path,
                remark='',
                status=1,
            )
            tmp_log.save()

            locked = try_lock_gpus(s, chosen, busy_by_log_id=tmp_log.id)
            if locked == len(chosen):
                server = s
                gpus = chosen
                running_log = tmp_log
                break

            # 可能部分占用成功，需要按 busy_by_log_id 精确释放
            try:
                release_gpus(s, chosen, busy_by_log_id=tmp_log.id)
            except Exception:
                task_logger.error(traceback.format_exc())

            # 没抢到：删掉临时 log，继续尝试别的 server
            try:
                tmp_log.delete()
            except Exception:
                pass
    except Exception:
        # 选 GPU/写运行记录阶段异常：清理认领锁，避免任务卡住
        try:
            GPUTask.objects.filter(id=task.id, status=0).update(dispatching_at=None)
        except Exception:
            pass
        raise

    if server is None or gpus is None or running_log is None:
        # 没有可用 GPU：保持“准备就绪”，并释放认领锁
        GPUTask.objects.filter(id=task.id, status=0).update(dispatching_at=None)
        return

    log_file_path = running_log.log_file_path
    try:
        # 标记为运行中（只从准备就绪切换，避免并发覆盖），并清理认领锁
        started = GPUTask.objects.filter(id=task.id, status=0).update(status=1, dispatching_at=None)
        if started != 1:
            try:
                running_log.status = -1
                running_log.save(update_fields=['status', 'update_at'])
            except Exception:
                pass
            try:
                release_gpus(server, gpus, busy_by_log_id=running_log.id)
            except Exception:
                task_logger.error(traceback.format_exc())
            return

        # run process (remote process group)
        process = RemoteGPUProcessGroup(
            task.user.config.server_username,
            server.ip,
            gpus,
            task.cmd,
            task.workspace,
            server.port,
            task.user.config.server_private_key_path,
            log_file_path,
            running_log_id=running_log.id,
        )
        # 同步读取首行并开始落盘输出
        process.start_streaming()

        pid = process.pid()
        first_line = process.first_line() or ''
        remote_pid, remote_pgid = _parse_remote_marker(first_line)
        task_logger.info(
            'Task {:d}-{:s} is running, ssh_pid: {:d}, remote_pid: {}, remote_pgid: {}'.format(
                task.id,
                task.name,
                pid,
                remote_pid if remote_pid is not None else '-',
                remote_pgid if remote_pgid is not None else '-',
            )
        )

        # save process status
        running_log.pid = pid
        running_log.remote_pid = remote_pid
        running_log.remote_pgid = remote_pgid
        running_log.last_heartbeat_at = timezone.now()
        running_log.save(update_fields=['pid', 'remote_pid', 'remote_pgid', 'last_heartbeat_at', 'update_at'])

        # send email
        send_task_start_email(running_log)

        # wait for return
        return_code = process.get_return_code()
        task_logger.info('Task {:d}-{:s} stopped, return_code: {:d}'.format(task.id, task.name, return_code))

        # save process status
        running_log.refresh_from_db()
        task.refresh_from_db()

        if running_log.status == 1:
            running_log.status = 2 if return_code == 0 else -1
            running_log.save(update_fields=['status', 'update_at'])

        if task.status == 1:
            task.status = 2 if return_code == 0 else -1
            task.save(update_fields=['status', 'update_at'])

        # send email
        if return_code == 0:
            send_task_finish_email(running_log)
        else:
            send_task_fail_email(running_log)
    except Exception:
        es = traceback.format_exc()
        task_logger.error(es)
        # 异常兜底：如果任务仍是“准备就绪”，清理认领锁，避免卡死
        try:
            GPUTask.objects.filter(id=task.id, status=0).update(dispatching_at=None)
        except Exception:
            pass
        try:
            running_log.status = -1
            running_log.save(update_fields=['status', 'update_at'])
        except Exception:
            pass
        try:
            task.status = -1
            task.save(update_fields=['status', 'update_at'])
        except Exception:
            pass
        with open(log_file_path, 'a') as f:
            f.write('\n')
            f.write(es)
    finally:
        try:
            release_gpus(server, gpus, busy_by_log_id=running_log.id)
        except Exception:
            task_logger.error(traceback.format_exc())


def mark_stale_running_tasks_as_lost():
    """将“运行中但心跳超时”的任务标记为“节点失联”。

    说明：失联并不等于任务失败；默认不自动释放 GPU，避免节点仍在跑时发生资源复用。
    """
    stale_seconds = int(os.getenv('GPUTASKER_TASK_HEARTBEAT_STALE_SECONDS', '180'))
    now = timezone.now()

    qs = (
        GPUTaskRunningLog.objects
        .select_related('task', 'server')
        .filter(status=1)
    )
    for running_log in qs:
        # 仅处理“已经进入心跳体系”的任务。
        # 老任务（未写入 last_heartbeat_at）不自动标记失联，避免升级瞬间大面积误判。
        if running_log.last_heartbeat_at is None:
            continue
        last = running_log.last_heartbeat_at
        delta = now - last
        if delta.total_seconds() <= stale_seconds:
            continue

        # 标记运行记录失联
        try:
            running_log.status = -2
            running_log.save(update_fields=['status', 'update_at'])
        except Exception:
            task_logger.error(traceback.format_exc())

        # 仅当 task 仍是“运行中”时才迁移，避免覆盖“已完成/失败”
        try:
            task = running_log.task
            if task and task.status == 1:
                task.status = -4
                task.save(update_fields=['status', 'update_at'])
        except Exception:
            task_logger.error(traceback.format_exc())
