import logging
import os
import json
import subprocess
import time
from typing import Dict, List

import requests


SERVER_API_URL = os.environ.get('GPUTASKER_SERVER_URL', 'http://127.0.0.1:8888/api/v1/report_gpu/')
TASKS_API_URL = os.environ.get('GPUTASKER_TASKS_API_URL', '').strip() or SERVER_API_URL.replace('/report_gpu/', '/report_tasks/')
AGENT_TOKEN = os.environ.get('GPUTASKER_AGENT_TOKEN', '')
REPORT_INTERVAL = int(os.environ.get('GPUTASKER_REPORT_INTERVAL', '30'))
REQUEST_TIMEOUT = float(os.environ.get('GPUTASKER_REQUEST_TIMEOUT', '5'))
EXIT_AFTER_CONSECUTIVE_FAILURES = int(os.environ.get('GPUTASKER_EXIT_AFTER_CONSECUTIVE_FAILURES', '0'))
REPORT_TASKS = (os.environ.get('GPUTASKER_REPORT_TASKS', '1') or '1').strip() not in {'0', 'false', 'False'}
RUNNING_TASKS_DIR = os.path.expanduser(os.environ.get('GPUTASKER_RUNNING_TASKS_DIR', '~/.gputasker/running_tasks'))

logging.basicConfig(
    level=os.environ.get('GPUTASKER_AGENT_LOGLEVEL', 'INFO'),
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger('gputasker.agent')


def run_local_cmd(cmd: str) -> str:
    try:
        result = subprocess.check_output(cmd, shell=True, timeout=10, stderr=subprocess.STDOUT)
        return result.decode('utf-8').strip()
    except subprocess.CalledProcessError as exc:
        logger.warning('Command failed (%s): %s', cmd, exc.output.decode('utf-8', errors='ignore'))
    except subprocess.TimeoutExpired:
        logger.warning('Command timed out: %s', cmd)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error('Unexpected error running command %s: %s', cmd, exc)
    return ''


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def collect_running_tasks() -> List[Dict]:
    tasks: List[Dict] = []
    if not os.path.isdir(RUNNING_TASKS_DIR):
        return tasks

    for name in os.listdir(RUNNING_TASKS_DIR):
        if not name.endswith('.json'):
            continue
        path = os.path.join(RUNNING_TASKS_DIR, name)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue

        try:
            log_id = int(data.get('running_log_id') or os.path.splitext(name)[0])
        except Exception:
            continue

        remote_pid = data.get('remote_pid')
        remote_pgid = data.get('remote_pgid')

        try:
            pid_int = int(remote_pid) if remote_pid is not None else -1
        except Exception:
            pid_int = -1

        if pid_int > 0 and (not _pid_alive(pid_int)):
            # 任务进程已退出：清理残留文件，避免误上报
            try:
                os.remove(path)
            except Exception:
                pass
            continue

        item: Dict = {'running_log_id': log_id}
        try:
            if remote_pid is not None:
                item['remote_pid'] = int(remote_pid)
        except Exception:
            pass
        try:
            if remote_pgid is not None:
                item['remote_pgid'] = int(remote_pgid)
        except Exception:
            pass
        tasks.append(item)

    return tasks


def _parse_gpu_lines(raw: str) -> List[Dict]:
    gpu_list = []
    for line in raw.splitlines():
        parts = [item.strip() for item in line.split(',')]
        if len(parts) < 6:
            continue
        try:
            gpu_list.append(
                {
                    'uuid': parts[0],
                    'index': int(parts[1]),
                    'name': parts[2],
                    'utilization': int(parts[3]),
                    'memory_total': int(parts[4]),
                    'memory_used': int(parts[5]),
                    'processes': [],
                }
            )
        except ValueError:
            continue
    return gpu_list


def collect_gpu_data() -> List[Dict]:
    base_cmd = (
        'nvidia-smi '
        '--query-gpu=uuid,index,gpu_name,utilization.gpu,memory.total,memory.used '
        '--format=csv,noheader,nounits'
    )
    gpu_raw = run_local_cmd(base_cmd)
    if not gpu_raw:
        return []

    gpu_list = _parse_gpu_lines(gpu_raw)
    gpu_dict = {gpu['uuid']: gpu for gpu in gpu_list}

    apps_cmd = (
        'nvidia-smi '
        '--query-compute-apps=gpu_uuid,pid,process_name,used_memory '
        '--format=csv,noheader,nounits'
    )
    apps_raw = run_local_cmd(apps_cmd)
    active_pids = set()

    if apps_raw:
        for line in apps_raw.splitlines():
            parts = [item.strip() for item in line.split(',')]
            if len(parts) < 4:
                continue
            uuid = parts[0]
            try:
                pid = int(parts[1])
                if uuid in gpu_dict:
                    gpu_dict[uuid]['processes'].append(
                        {
                            'pid': pid,
                            'command': parts[2],
                            'gpu_memory_usage': int(parts[3]),
                            'username': 'unknown',
                        }
                    )
                    active_pids.add(pid)
            except ValueError:
                continue

    if active_pids:
        pid_str = ' '.join(str(pid) for pid in active_pids)
        ps_cmd = f'ps -o user= -o pid= -p {pid_str}'
        ps_raw = run_local_cmd(ps_cmd)
        if ps_raw:
            pid_user_map = {}
            for line in ps_raw.splitlines():
                parts = line.split()
                if len(parts) < 2:
                    continue
                try:
                    pid_user_map[int(parts[1])] = parts[0]
                except ValueError:
                    continue

            for gpu in gpu_list:
                for proc in gpu['processes']:
                    proc['username'] = pid_user_map.get(proc['pid'], 'unknown')

    return gpu_list


def send_report():
    if not AGENT_TOKEN:
        logger.error('Missing agent token. Set GPUTASKER_AGENT_TOKEN in the environment.')
        return False

    if not AGENT_TOKEN:
        logger.error('Missing agent token. Set GPUTASKER_AGENT_TOKEN in the environment.')
        return False

    ok_gpu = False
    ok_tasks = True

    gpus = collect_gpu_data()
    payload = {'token': AGENT_TOKEN, 'gpus': gpus, 'timestamp': int(time.time())}
    try:
        response = requests.post(SERVER_API_URL, json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            logger.info('Reported %d GPU(s) successfully.', len(gpus))
            ok_gpu = True
        elif response.status_code in (401, 403):
            logger.error('Agent token rejected (%s). Please check GPUTASKER_AGENT_TOKEN.', response.status_code)
            raise RuntimeError('token_rejected')
        else:
            logger.warning('Server responded with %s: %s', response.status_code, response.text)
    except requests.RequestException as exc:
        logger.error('Failed to report GPU status: %s', exc)
    except RuntimeError:
        raise

    if REPORT_TASKS:
        tasks = collect_running_tasks()
        tasks_payload = {'token': AGENT_TOKEN, 'tasks': tasks, 'timestamp': int(time.time())}
        try:
            resp = requests.post(TASKS_API_URL, json=tasks_payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                logger.info('Reported %d running task(s) successfully.', len(tasks))
                ok_tasks = True
            elif resp.status_code in (401, 403):
                logger.error('Agent token rejected by tasks endpoint (%s).', resp.status_code)
                raise RuntimeError('token_rejected')
            else:
                ok_tasks = False
                logger.warning('Tasks endpoint responded with %s: %s', resp.status_code, resp.text)
        except requests.RequestException as exc:
            ok_tasks = False
            logger.error('Failed to report task heartbeats: %s', exc)
        except RuntimeError:
            raise

    return ok_gpu and ok_tasks


def main():
    if not AGENT_TOKEN:
        logger.error('Missing agent token. Set GPUTASKER_AGENT_TOKEN before starting.')
        return
    logger.info('Starting GPU agent. Reporting to %s every %ss.', SERVER_API_URL, REPORT_INTERVAL)
    if REPORT_TASKS:
        logger.info('Task heartbeats enabled. Reporting to %s (dir=%s).', TASKS_API_URL, RUNNING_TASKS_DIR)

    consecutive_failures = 0
    while True:
        start = time.time()
        try:
            ok = send_report()
        except RuntimeError:
            # token 被拒绝：直接退出（exit code 0），便于 systemd Restart=on-failure 不重启
            logger.error('Exiting due to token rejection.')
            return

        if ok:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if EXIT_AFTER_CONSECUTIVE_FAILURES > 0 and consecutive_failures >= EXIT_AFTER_CONSECUTIVE_FAILURES:
                logger.error(
                    'Exiting after %d consecutive failures (GPUTASKER_EXIT_AFTER_CONSECUTIVE_FAILURES=%d).',
                    consecutive_failures,
                    EXIT_AFTER_CONSECUTIVE_FAILURES,
                )
                return
        elapsed = time.time() - start
        sleep_time = max(REPORT_INTERVAL - elapsed, 0)
        time.sleep(sleep_time)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info('Agent interrupted, exiting.')
