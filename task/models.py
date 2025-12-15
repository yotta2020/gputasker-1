import os
import signal

from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator

from gpu_info.models import GPUServer, GPUInfo
from django.contrib.auth.models import User


class Project(models.Model):
    name = models.CharField('项目名称', max_length=200, unique=True)
    archived = models.BooleanField('归档', default=False)
    create_at = models.DateTimeField('创建时间', auto_now_add=True)
    update_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '项目'
        verbose_name_plural = '项目'
        ordering = ('archived', 'name', 'id')

    def __str__(self):
        return self.name


class TaskGroup(models.Model):
    project = models.ForeignKey(Project, verbose_name='项目', on_delete=models.CASCADE, related_name='groups')
    name = models.CharField('分组名称', max_length=200)
    archived = models.BooleanField('归档', default=False)
    create_at = models.DateTimeField('创建时间', auto_now_add=True)
    update_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '任务分组'
        verbose_name_plural = '任务分组'
        ordering = ('archived', 'name', 'id')
        constraints = [
            models.UniqueConstraint(fields=['project', 'name'], name='uniq_taskgroup_project_name'),
        ]

    def __str__(self):
        return f'{self.project.name} / {self.name}'


class GPUTask(models.Model):
    STATUS_CHOICE = (
        (-2, '未就绪'),
        (-1, '运行失败'),
        (-4, '节点失联'),
        (0, '准备就绪'),
        (1, '运行中'),
        (2, '已完成'),
    )
    name = models.CharField('任务名称', max_length=100)
    user = models.ForeignKey(User, verbose_name='用户', on_delete=models.CASCADE, related_name='tasks')
    group = models.ForeignKey(
        TaskGroup,
        verbose_name='分组',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='tasks'
    )
    workspace = models.CharField('工作目录', max_length=200)
    cmd = models.TextField('命令')
    gpu_requirement = models.PositiveSmallIntegerField(
        'GPU数量需求',
        default=1,
        validators=[MaxValueValidator(8), MinValueValidator(0)]
    )
    exclusive_gpu = models.BooleanField('独占显卡', default=False)
    memory_requirement = models.PositiveSmallIntegerField('显存需求(MB)', default=0)
    utilization_requirement = models.PositiveSmallIntegerField('利用率需求(%)', default=0)
    assign_server = models.ForeignKey(GPUServer, verbose_name='指定服务器', on_delete=models.SET_NULL, blank=True, null=True)
    priority = models.SmallIntegerField('优先级', default=0)
    status = models.SmallIntegerField('状态', choices=STATUS_CHOICE, default=0)
    dispatching_at = models.DateTimeField('调度认领时间', blank=True, null=True)
    create_at = models.DateTimeField('创建时间', auto_now_add=True)
    update_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = 'GPU任务'
        verbose_name_plural = 'GPU任务'

    def __str__(self):
        return self.name

    @property
    def project(self):
        if self.group_id is None:
            return None
        return self.group.project

    def _normalize_cmd(self):
        if not isinstance(self.cmd, str):
            return
        self.cmd = self.cmd.replace('\r\n', '\n')
        if self.cmd and self.cmd[-1] != '\n':
            self.cmd = self.cmd + '\n'

    def save(self, *args, **kwargs):
        self._normalize_cmd()
        super().save(*args, **kwargs)

    def find_available_server(self):
        # TODO(Yuhao Wang): 优化算法，找最优server
        available_server = None
        if self.assign_server is None:
            for server in GPUServer.objects.all():
                available_gpus = server.get_available_gpus(
                    self.gpu_requirement,
                    self.exclusive_gpu,
                    self.memory_requirement,
                    self.utilization_requirement
                )
                if available_gpus is not None:
                    available_server = {
                        'server': server,
                        'gpus': available_gpus[:self.gpu_requirement]
                    }
                    break
        else:
            available_gpus = self.assign_server.get_available_gpus(
                self.gpu_requirement,
                self.exclusive_gpu,
                self.memory_requirement,
                self.utilization_requirement
            )
            if available_gpus is not None:
                available_server = {
                    'server': self.assign_server,
                    'gpus': available_gpus[:self.gpu_requirement]
                }

        return available_server


class GPUTaskRunningLog(models.Model):
    STATUS_CHOICE = (
        (-1, '运行失败'),
        (-2, '节点失联'),
        (1, '运行中'),
        (2, '已完成'),
    )
    index = models.PositiveSmallIntegerField('序号')
    task = models.ForeignKey(GPUTask, verbose_name='任务', on_delete=models.CASCADE, related_name='task_logs')
    server = models.ForeignKey(GPUServer, verbose_name='服务器', on_delete=models.SET_NULL, related_name='task_logs', null=True)
    pid = models.IntegerField('SSH PID')
    remote_pid = models.IntegerField('远端PID', blank=True, null=True)
    remote_pgid = models.IntegerField('远端PGID', blank=True, null=True)
    gpus = models.CharField('GPU', max_length=20)
    log_file_path = models.FilePathField(path='running_log', match='.*\.log$', verbose_name="日志文件")
    remark = models.CharField('备注', max_length=200, blank=True, default='')
    status = models.SmallIntegerField('状态', choices=STATUS_CHOICE, default=1)
    last_heartbeat_at = models.DateTimeField('最近心跳时间', blank=True, null=True)
    start_at = models.DateTimeField('开始时间', auto_now_add=True)
    update_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        ordering = ('-id',)
        verbose_name = 'GPU任务运行记录'
        verbose_name_plural = 'GPU任务运行记录'

    def __str__(self):
        return self.task.name + '-' + str(self.index)

    def kill(self):
        # 兼容旧逻辑：默认只杀 master 本地 ssh 进程（不保证远端训练被终止）。
        os.kill(self.pid, signal.SIGKILL)
    
    def delete_log_file(self):
        if os.path.isfile(self.log_file_path):
            os.remove(self.log_file_path)
