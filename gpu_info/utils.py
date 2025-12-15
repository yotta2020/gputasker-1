import os
import subprocess
import json
import logging
import base64
import hashlib
from typing import Optional

from .models import GPUServer, GPUInfo

from django.conf import settings

task_logger = logging.getLogger('django.task')


def build_report_gpu_url():
    """生成 Node 上报接口 URL。

    优先级：
    1) 环境变量 GPUTASKER_SERVER_URL
    2) 环境变量 GPUTASKER_MASTER_IP / GPUTASKER_MASTER_PORT
    3) 默认：222.20.126.169:8888
    """
    server_url = (os.environ.get('GPUTASKER_SERVER_URL') or '').strip()
    if server_url:
        return server_url
    master_ip = (os.environ.get('GPUTASKER_MASTER_IP') or '222.20.126.169').strip()
    master_port = (os.environ.get('GPUTASKER_MASTER_PORT') or '8888').strip()
    return f'http://{master_ip}:{master_port}/api/v1/report_gpu/'


def _ssh_run(host, user, remote_cmd, port=22, private_key_path=None, timeout=60):
    """执行 ssh 命令（不做 '$' 转义，避免影响远端脚本）。

    remote_cmd 会作为 ssh 的“远端命令参数”直接传递，不经过本地 shell。
    """
    args = ['ssh', '-o', 'StrictHostKeyChecking=no', '-p', str(int(port))]
    if private_key_path:
        args += ['-i', private_key_path]
    args += [f'{user}@{host}', remote_cmd]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    stdout = (proc.stdout or '').strip()
    stderr = (proc.stderr or '').strip()
    if proc.returncode != 0:
        raise RuntimeError(f'ssh failed rc={proc.returncode}: {stderr or stdout or "(no output)"}')
    return stdout


def _ssh_timeout_seconds():
    try:
        return int(os.getenv('GPUTASKER_SSH_TIMEOUT_SECONDS', '60'))
    except Exception:
        return 60


def _remote_python_heredoc(py_body: str) -> str:
    """生成远端可执行命令：优先 python3，否则 python。"""
    return (
        "(command -v python3 >/dev/null 2>&1 && PYBIN=python3 || PYBIN=python; "
        "$PYBIN - <<'PY'\n"
        + py_body +
        "\nPY\n)"
    )


def _local_agent_source() -> str:
    # 读取 Master 本地仓库中的 agent 脚本内容，用于推送到远端
    base_dir = str(getattr(settings, 'BASE_DIR', '') or '')
    candidate = os.path.join(base_dir, 'agent', 'gpu_agent.py') if base_dir else None
    if not candidate or not os.path.isfile(candidate):
        raise RuntimeError('local agent/gpu_agent.py not found; cannot push to node')
    with open(candidate, 'r', encoding='utf-8') as f:
        return f.read()


def _sha256_hex(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _remote_file_sha256(server: GPUServer, ssh_user: str, ssh_private_key_path: Optional[str], path: str) -> str:
    payload_b64 = base64.b64encode(json.dumps({'path': path}, ensure_ascii=False).encode('utf-8')).decode('ascii')
    py_body = f"""import base64, json, os, hashlib
p=json.loads(base64.b64decode('{payload_b64}').decode('utf-8'))
path=os.path.expanduser(p['path'])
if not os.path.isfile(path):
    print('MISSING')
else:
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    print('SHA256=' + h.hexdigest())
"""
    out = _ssh_run(
        server.ip,
        ssh_user,
        _remote_python_heredoc(py_body),
        port=server.port,
        private_key_path=ssh_private_key_path,
        timeout=_ssh_timeout_seconds(),
    )
    out = (out or '').strip()
    if out.startswith('SHA256='):
        return out.split('=', 1)[1].strip()
    return 'MISSING'


def _ensure_remote_agent_present(server: GPUServer, ssh_user: str, ssh_private_key_path: Optional[str], agent_path: str):
    """确保远端存在 agent 脚本。

    - 如果远端 agent_path 已存在：直接返回 agent_path
    - 否则：推送到 ~/.gputasker/agent/gpu_agent.py 并返回该路径

    说明：默认假设可能是 NFS 同路径；若不是，会自动推送。
    """
    push_enabled = (os.getenv('GPUTASKER_REMOTE_PUSH_AGENT', '1') or '1').strip() not in {'0', 'false', 'False'}
    if not push_enabled:
        return agent_path

    # 推送策略：
    # - missing: 仅当远端不存在时推送（旧行为）
    # - update: 远端不存在或内容不同则推送（默认）
    # - always: 每次都覆盖推送
    push_mode = (os.getenv('GPUTASKER_REMOTE_PUSH_AGENT_MODE', 'update') or 'update').strip().lower()
    if push_mode not in {'missing', 'update', 'always'}:
        push_mode = 'update'

    source = _local_agent_source()
    local_sha = _sha256_hex(source.encode('utf-8'))

    # 如果允许复用 NFS 同路径 agent：先检查远端 agent_path 是否已是最新
    try:
        remote_sha = _remote_file_sha256(server, ssh_user, ssh_private_key_path, agent_path)
        if remote_sha != 'MISSING' and remote_sha == local_sha and push_mode != 'always':
            return agent_path
        if push_mode == 'missing' and remote_sha != 'MISSING':
            return agent_path
    except Exception:
        # hash 检查失败：继续走推送（尽量自愈）
        if push_mode == 'missing':
            return agent_path

    remote_agent_path = '~/.gputasker/agent/gpu_agent.py'
    source_b64 = base64.b64encode(source.encode('utf-8')).decode('ascii')
    payload_b64 = base64.b64encode(
        json.dumps({'path': remote_agent_path, 'content_b64': source_b64}, ensure_ascii=False).encode('utf-8')
    ).decode('ascii')

    py_body = f"""import base64, json, os
p=json.loads(base64.b64decode('{payload_b64}').decode('utf-8'))
path=os.path.expanduser(p['path'])
os.makedirs(os.path.dirname(path), exist_ok=True)
data=base64.b64decode(p['content_b64'].encode('ascii'))
with open(path,'wb') as f:
    f.write(data)
print('pushed ' + path)
"""
    cmd = _remote_python_heredoc(py_body)
    _ssh_run(
        server.ip,
        ssh_user,
        cmd,
        port=server.port,
        private_key_path=ssh_private_key_path,
        timeout=_ssh_timeout_seconds(),
    )
    return remote_agent_path


def _remote_agent_defaults():
    # 默认假设 Master 的仓库路径在 Node 上可用（例如 NFS 共享挂载）
    base_dir = str(getattr(settings, 'BASE_DIR', ''))
    default_workdir = os.environ.get('GPUTASKER_REMOTE_WORKDIR', base_dir or '~')
    default_agent_path = os.environ.get(
        'GPUTASKER_REMOTE_AGENT_PATH',
        os.path.join(base_dir, 'agent', 'gpu_agent.py') if base_dir else '~/gputasker/agent/gpu_agent.py',
    )
    return default_workdir, default_agent_path


def _remote_agent_paths():
    return {
        'dir': '~/.gputasker',
        'pid_json': '~/.gputasker/gpu_agent.json',
        'log': '~/.gputasker/gpu_agent.log',
        'env': '~/.gputasker/agent.env',
    }


def start_node_agent(server: GPUServer, server_url: str, ssh_user: str, ssh_private_key_path: Optional[str]):
    """通过 SSH 在 node 上启动 agent（若已运行则不重复启动）。"""
    workdir, agent_path = _remote_agent_defaults()
    agent_path = _ensure_remote_agent_present(server, ssh_user, ssh_private_key_path, agent_path)
    paths = _remote_agent_paths()
    payload = {
        'action': 'start',
        'server_url': server_url,
        'token': server.report_token,
        'agent_path': agent_path,
        'workdir': workdir,
        'base_dir': paths['dir'],
        'pid_path': paths['pid_json'],
        'log_path': paths['log'],
        'env_path': paths['env'],
    }
    b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode('utf-8')).decode('ascii')

    py_body_template = """import base64, json, os, sys, time, subprocess

p = json.loads(base64.b64decode('__GPUTASKER_B64__').decode('utf-8'))
base_dir = os.path.expanduser(p.get('base_dir') or '~/.gputasker')
pid_path = os.path.expanduser(p['pid_path'])
log_path = os.path.expanduser(p['log_path'])
env_path = os.path.expanduser(p.get('env_path') or (base_dir + '/agent.env'))
os.makedirs(os.path.dirname(pid_path), exist_ok=True)
os.makedirs(os.path.dirname(env_path), exist_ok=True)

def alive(x):
    try:
        os.kill(int(x), 0)
        return True
    except Exception:
        return False

pid = -1
if os.path.isfile(pid_path):
    try:
        d = json.load(open(pid_path))
        pid = int(d.get('pid', -1))
    except Exception:
        pid = -1

env = os.environ.copy()
env['GPUTASKER_SERVER_URL'] = p['server_url']
env['GPUTASKER_AGENT_TOKEN'] = p['token']
cwd = os.path.expanduser(p.get('workdir') or '~')
if not os.path.isdir(cwd):
    cwd = os.path.expanduser('~')

# 始终把 token/server_url 落盘到 node 配置文件（即使 agent 已在跑）
with open(env_path, 'w') as f:
    f.write('export GPUTASKER_SERVER_URL="%s"\\n' % p['server_url'])
    f.write('export GPUTASKER_AGENT_TOKEN="%s"\\n' % p['token'])

if pid > 0 and alive(pid):
    print('already_running pid=%d (env_updated)' % pid)
    raise SystemExit(0)

log = open(log_path, 'a', buffering=1)
proc = subprocess.Popen(
    ['nohup', sys.executable, os.path.expanduser(p['agent_path'])],
    cwd=cwd,
    env=env,
    stdout=log,
    stderr=log,
    preexec_fn=os.setsid,
)
pgid = os.getpgid(proc.pid)
json.dump({'pid': proc.pid, 'pgid': pgid, 'started_at': int(time.time())}, open(pid_path, 'w'))
print('started pid=%d pgid=%d' % (proc.pid, pgid))
"""

    py_body = py_body_template.replace('__GPUTASKER_B64__', b64)
    cmd = _remote_python_heredoc(py_body)
    return _ssh_run(
        server.ip,
        ssh_user,
        cmd,
        port=server.port,
        private_key_path=ssh_private_key_path,
        timeout=_ssh_timeout_seconds(),
    )


def stop_node_agent(server: GPUServer, ssh_user: str, ssh_private_key_path: Optional[str]):
    """通过 SSH 在 node 上停止 agent（优先按 pidfile kill 进程组）。"""
    payload = {
        'action': 'stop',
        'pid_path': '~/.gputasker/gpu_agent.json',
    }
    b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode('utf-8')).decode('ascii')

    py_body_template = """import base64, json, os, signal

p = json.loads(base64.b64decode('__GPUTASKER_B64__').decode('utf-8'))
pid_path = os.path.expanduser(p['pid_path'])

def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False

if not os.path.isfile(pid_path):
    print('not_running')
    raise SystemExit(0)

try:
    d = json.load(open(pid_path))
    pid = int(d.get('pid', -1))
    pgid = int(d.get('pgid', -1))
except Exception:
    pid = -1
    pgid = -1

killed = False
if pgid > 0:
    try:
        os.killpg(pgid, signal.SIGTERM)
        killed = True
    except Exception:
        pass
if (not killed) and pid > 0 and alive(pid):
    try:
        os.kill(pid, signal.SIGTERM)
        killed = True
    except Exception:
        pass

try:
    os.remove(pid_path)
except Exception:
    pass

print('stopped' if killed else 'not_running')
"""

    py_body = py_body_template.replace('__GPUTASKER_B64__', b64)
    cmd = _remote_python_heredoc(py_body)
    return _ssh_run(
        server.ip,
        ssh_user,
        cmd,
        port=server.port,
        private_key_path=ssh_private_key_path,
        timeout=_ssh_timeout_seconds(),
    )


def restart_node_agent(server: GPUServer, server_url: str, ssh_user: str, ssh_private_key_path: Optional[str]):
    """通过 SSH 重启 node agent（先 stop 再 start）。"""
    stop_node_agent(server, ssh_user=ssh_user, ssh_private_key_path=ssh_private_key_path)
    return start_node_agent(server, server_url, ssh_user=ssh_user, ssh_private_key_path=ssh_private_key_path)


def ssh_execute(host, user, exec_cmd, port=22, private_key_path=None):
    exec_cmd = exec_cmd.replace('\r\n', '\n').replace('$', '\\$')
    if exec_cmd[-1] != '\n':
        exec_cmd = exec_cmd + '\n'
    if private_key_path is None:
        cmd = "ssh -o StrictHostKeyChecking=no -p {:d} {}@{} \"{}\"".format(port, user, host, exec_cmd)
    else:
        cmd = "ssh -o StrictHostKeyChecking=no -p {:d} -i {} {}@{} \"{}\"".format(port, private_key_path, user, host, exec_cmd)
    return subprocess.check_output(cmd, timeout=60, shell=True)


def get_hostname(host, user, port=22, private_key_path=None):
    cmd = "hostname"
    return str(ssh_execute(
        host,
        user,
        cmd,
        port,
        private_key_path
    ).replace(b'\n', b'')).replace('b\'', '').replace('\'', '')


def add_hostname(server, user, private_key_path=None):
    hostname = get_hostname(server.ip, user, server.port, private_key_path)
    server.hostname = hostname
    server.save()


def get_gpu_status(host, user, port=22, private_key_path=None):
    gpu_info_list = []
    query_gpu_cmd = 'nvidia-smi --query-gpu=uuid,gpu_name,utilization.gpu,memory.total,memory.used --format=csv | grep -v \'uuid\''
    gpu_info_raw = ssh_execute(host, user, query_gpu_cmd, port, private_key_path).decode('utf-8')

    if gpu_info_raw.find('Error') != -1:
        raise RuntimeError(gpu_info_raw)

    gpu_info_dict = {}
    for index, gpu_info_line in enumerate(gpu_info_raw.split('\n')):
        try:
            gpu_info_items = gpu_info_line.split(',')
            gpu_info = {}
            gpu_info['index'] = index
            gpu_info['uuid'] = gpu_info_items[0].strip()
            gpu_info['name'] = gpu_info_items[1].strip()
            gpu_info['utilization.gpu'] = int(gpu_info_items[2].strip().split(' ')[0])
            gpu_info['memory.total'] = int(gpu_info_items[3].strip().split(' ')[0])
            gpu_info['memory.used'] = int(gpu_info_items[4].strip().split(' ')[0])
            gpu_info['processes'] = []
            gpu_info_list.append(gpu_info)
            gpu_info_dict[gpu_info['uuid']] = gpu_info
        except Exception:
            continue

    pid_set = set([])
    if len(gpu_info_list) != 0:
        query_apps_cmd = 'nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv'
        app_info_raw = ssh_execute(host, user, query_apps_cmd, port, private_key_path).decode('utf-8')

        for app_info_line in app_info_raw.split('\n')[1:]:
            try:
                app_info_items = app_info_line.split(',')
                app_info = {}
                uuid = app_info_items[0].strip()
                app_info['pid'] = int(app_info_items[1].strip())
                app_info['command'] = app_info_items[2].strip()
                app_info['gpu_memory_usage'] = int(app_info_items[3].strip().split(' ')[0])
                if app_info['gpu_memory_usage'] != 0:
                    gpu_info_dict[uuid]['processes'].append(app_info)
                    pid_set.add(app_info['pid'])
            except Exception:
                continue

    pid_username_dict = {}
    if len(pid_set) != 0:
        query_pid_cmd = 'ps -o ruser=userForLongName -o pid -p ' + ' '.join(map(str, pid_set)) + ' | awk \'{print $1, $2}\' | grep -v \'PID\''
        pid_raw = ssh_execute(host, user, query_pid_cmd, port, private_key_path).decode('utf-8')
        for pid_line in pid_raw.split('\n'):
            try:
                username, pid = pid_line.split(' ')
                pid = int(pid.strip())
                pid_username_dict[pid] = username.strip()
            except Exception:
                continue
    for gpu_info in gpu_info_list:
        for process in gpu_info['processes']:
            process['username'] = pid_username_dict.get(process['pid'], '')

    return gpu_info_list


class GPUInfoUpdater:
    def __init__(self, user, private_key_path=None):
        self.user = user
        self.private_key_path = private_key_path
        self.utilization_history = {}
    
    def update_utilization(self, uuid, utilization):
        if self.utilization_history.get(uuid) is None:
            self.utilization_history[uuid] = [utilization]
            return utilization
        else:
            self.utilization_history[uuid].append(utilization)
            if len(self.utilization_history[uuid]) > 10:
                self.utilization_history[uuid].pop(0)
            return max(self.utilization_history[uuid])

    def update_gpu_info(self):
        server_list = GPUServer.objects.all()
        for server in server_list:
            try:
                if server.hostname is None or server.hostname == '':
                    add_hostname(server, self.user, self.private_key_path)
                gpu_info_json = get_gpu_status(server.ip, self.user, server.port, self.private_key_path)
                if not server.valid:
                    server.valid = True
                    server.save()
                for gpu in gpu_info_json:
                    if GPUInfo.objects.filter(uuid=gpu['uuid']).count() == 0:
                        gpu_info = GPUInfo(
                            uuid=gpu['uuid'],
                            name=gpu['name'],
                            index=gpu['index'],
                            utilization=self.update_utilization(gpu['uuid'], gpu['utilization.gpu']),
                            memory_total=gpu['memory.total'],
                            memory_used=gpu['memory.used'],
                            processes='\n'.join(map(lambda x: json.dumps(x), gpu['processes'])),
                            complete_free=len(gpu['processes']) == 0,
                            server=server
                        )
                        gpu_info.save()
                    else:
                        gpu_info = GPUInfo.objects.get(uuid=gpu['uuid'])
                        gpu_info.utilization = self.update_utilization(gpu['uuid'], gpu['utilization.gpu'])
                        gpu_info.memory_total = gpu['memory.total']
                        gpu_info.memory_used = gpu['memory.used']
                        gpu_info.complete_free = len(gpu['processes']) == 0
                        gpu_info.processes = '\n'.join(map(lambda x: json.dumps(x), gpu['processes']))
                        gpu_info.save()
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError):
                task_logger.error('Update ' + server.ip + ' failed')
                server.valid = False
                server.save()
