import json

import secrets
import os

from django.db import models
from django.utils import timezone


class GPUServer(models.Model):
    ip = models.CharField('IP地址', max_length=50)
    hostname = models.CharField('主机名', max_length=50, blank=True, null=True)
    port = models.PositiveIntegerField('端口', default=22)
    valid = models.BooleanField('是否可用', default=True)
    can_use = models.BooleanField('是否可调度', default=True)
    report_token = models.CharField('上报Token', max_length=128, blank=True, null=True, unique=True)
    last_report_at = models.DateTimeField('最近上报时间', blank=True, null=True)
    # TODO(Yuhao Wang): CPU使用率

    class Meta:
        ordering = ('ip',)
        verbose_name = 'GPU服务器'
        verbose_name_plural = 'GPU服务器'
        unique_together = (('ip', 'port'),)

    def __str__(self):
        return '{}:{:d}'.format(self.ip, self.port)

    def save(self, *args, **kwargs):
        if not self.report_token:
            # token 仅用于鉴权节点上报接口，避免中心端 SSH 扫描。
            self.report_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def is_reporting_alive(self):
        """节点上报模式下：根据最近上报时间判断是否可用。"""
        stale_seconds = int(os.getenv('GPUTASKER_NODE_STALE_SECONDS', '180'))
        if self.last_report_at is None:
            return False
        delta = timezone.now() - self.last_report_at
        return delta.total_seconds() <= stale_seconds

    def get_available_gpus(self, gpu_num, exclusive, memory, utilization):
        available_gpu_list = []
        update_mode = (os.getenv('GPUTASKER_GPU_UPDATE_MODE', 'report') or 'report').strip().lower()
        if update_mode == 'report':
            available = self.is_reporting_alive() and self.can_use
        else:
            available = self.valid and self.can_use

        if available:
            for gpu in self.gpus.all():
                if gpu.check_available(exclusive, memory, utilization):
                    available_gpu_list.append(gpu.index)
            if len(available_gpu_list) >= gpu_num:
                return available_gpu_list
            else:
                return None
        else:
            return None
    
    def set_gpus_busy(self, gpu_list):
        self.gpus.filter(index__in=gpu_list).update(use_by_self=True)

    def set_gpus_free(self, gpu_list):
        self.gpus.filter(index__in=gpu_list).update(use_by_self=False)


class GPUInfo(models.Model):
    uuid = models.CharField('UUID', max_length=40, primary_key=True)
    index = models.PositiveSmallIntegerField('序号')
    name = models.CharField('名称', max_length=40)
    utilization = models.PositiveSmallIntegerField('利用率')
    memory_total = models.PositiveIntegerField('总显存')
    memory_used = models.PositiveIntegerField('已用显存')
    processes = models.TextField('进程')
    server = models.ForeignKey(GPUServer, verbose_name='服务器', on_delete=models.CASCADE, related_name='gpus')
    use_by_self = models.BooleanField('是否被gputasker进程占用', default=False)
    busy_by_log_id = models.IntegerField('占用运行记录ID', blank=True, null=True)
    complete_free = models.BooleanField('完全空闲', default=False)
    update_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        ordering = ('server', 'index',)
        verbose_name = 'GPU信息'
        verbose_name_plural = 'GPU信息'

    def __str__(self):
        return self.name + '[' + str(self.index) + '-' + self.server.ip + ']'
    
    @property
    def memory_available(self):
        return self.memory_total - self.memory_used

    @property
    def utilization_available(self):
        return 100 - self.utilization

    def check_available(self, exclusive, memory, utilization):
        if exclusive:
            return not self.use_by_self and self.complete_free
        else:
            return not self.use_by_self and self.memory_available > memory and self.utilization_available > utilization

    def usernames(self):
        r"""
        convert processes string to usernames string array.
        :return: string array of usernames.
        """
        if self.processes != '':
            arr = self.processes.split('\n')
            # only show first two usernames
            username_arr = [json.loads(item)['username'] for item in arr[:2]]
            res = ', '.join(username_arr)
            # others use ... to note
            if len(arr) > 2:
                res = res + ', ...'
            return res
        else:
            return '-'


def _normalize_gpu_indices(gpu_list):
    if gpu_list is None:
        return []
    if isinstance(gpu_list, str):
        gpu_list = [item.strip() for item in gpu_list.split(',') if item.strip() != '']
    res = []
    for item in gpu_list:
        try:
            res.append(int(item))
        except Exception:
            continue
    return res


def try_lock_gpus(server, gpu_list, busy_by_log_id):
    """原子占用指定 GPU：仅当当前 use_by_self=False 时才会占用。

    返回成功占用的行数。
    """
    gpu_indices = _normalize_gpu_indices(gpu_list)
    if not gpu_indices:
        return 0
    return GPUInfo.objects.filter(
        server=server,
        index__in=gpu_indices,
        use_by_self=False,
    ).update(use_by_self=True, busy_by_log_id=busy_by_log_id)


def release_gpus(server, gpu_list, busy_by_log_id=None):
    """释放指定 GPU。

    - busy_by_log_id 给定时：仅释放属于该运行记录的占用，避免误释放。
    - busy_by_log_id 为空：退化为旧行为（无条件释放）。
    """
    gpu_indices = _normalize_gpu_indices(gpu_list)
    if not gpu_indices:
        return 0
    qs = GPUInfo.objects.filter(server=server, index__in=gpu_indices)
    if busy_by_log_id is not None:
        qs = qs.filter(busy_by_log_id=busy_by_log_id)
    return qs.update(use_by_self=False, busy_by_log_id=None)
