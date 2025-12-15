import os
import time
import threading
import logging
from datetime import timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gpu_tasker.settings")
django.setup()

from base.utils import get_admin_config
from task.models import GPUTask
from task.utils import run_task, mark_stale_running_tasks_as_lost
from gpu_info.utils import GPUInfoUpdater
from django.db.models import Q
from django.utils import timezone

task_logger = logging.getLogger('django.task')


def _get_loop_interval_seconds():
    try:
        return max(1, int(os.getenv('GPUTASKER_LOOP_INTERVAL_SECONDS', '10')))
    except ValueError:
        return 10


def _get_gpu_update_mode():
    mode = (os.getenv('GPUTASKER_GPU_UPDATE_MODE', 'report') or 'report').strip().lower()
    return mode if mode in {'ssh', 'report'} else 'report'


if __name__ == '__main__':
    while True:
        start_time = time.time()
        loop_interval_seconds = _get_loop_interval_seconds()
        gpu_update_mode = _get_gpu_update_mode()
        try:
            server_username, server_private_key_path = get_admin_config()
            gpu_updater = GPUInfoUpdater(server_username, server_private_key_path)

            task_logger.info('Running processes: {:d}'.format(
                threading.active_count() - 1
            ))

            # 运行中任务心跳超时处理（节点失联）
            try:
                mark_stale_running_tasks_as_lost()
            except Exception as exc:
                task_logger.error('mark_stale_running_tasks_as_lost failed: %s', exc)

            if gpu_update_mode == 'ssh':
                gpu_updater.update_gpu_info()

            # 兼容清理：旧版本会把任务置为 -3(调度中)。新版本已移除该状态，统一回收到“准备就绪”。
            try:
                GPUTask.objects.filter(status=-3).update(status=0, dispatching_at=None)
            except Exception:
                pass
            # 任务原子认领：避免并发/多实例重复启动。
            # 说明：历史上用 status=-3(调度中) 做中间态，容易在异常时卡死；现在用 dispatching_at 替代。
            try:
                claim_stale_seconds = max(5, int(os.getenv('GPUTASKER_DISPATCH_CLAIM_STALE_SECONDS', '60')))
            except ValueError:
                claim_stale_seconds = 60
            now = timezone.now()
            stale_before = now - timedelta(seconds=claim_stale_seconds)

            task_ids = list(
                GPUTask.objects.filter(status=0)
                .filter(Q(dispatching_at__isnull=True) | Q(dispatching_at__lt=stale_before))
                .order_by('-priority', 'create_at')
                .values_list('id', flat=True)
            )
            for task_id in task_ids:
                claimed = (
                    GPUTask.objects.filter(id=task_id, status=0)
                    .filter(Q(dispatching_at__isnull=True) | Q(dispatching_at__lt=stale_before))
                    .update(dispatching_at=now)
                )
                if claimed != 1:
                    continue
                t = threading.Thread(target=run_task, args=(task_id,))
                t.start()
                time.sleep(1)
        except Exception as e:
            task_logger.error(str(e))
        finally:
            end_time = time.time()
            # 确保至少间隔 N 秒，减少服务器负担
            duration = end_time - start_time
            if duration < loop_interval_seconds:
                time.sleep(loop_interval_seconds - duration)
