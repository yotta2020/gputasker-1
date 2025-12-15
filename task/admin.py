from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.utils.html import format_html
from .models import GPUTask, GPUTaskRunningLog
from .utils import kill_running_log


class GPUTaskRunningLogInline(admin.TabularInline):
    model = GPUTaskRunningLog
    fields = ('index', 'server', 'gpus', 'log_file_path', 'color_status', 'start_at', 'update_at',)
    readonly_fields = ('index', 'server', 'gpus', 'log_file_path', 'color_status', 'start_at', 'update_at',)

    show_change_link = True

    verbose_name = '运行记录'
    verbose_name_plural = '运行记录'

    def get_extra(self, request, obj, **kwargs):
        return 0

    def has_add_permission(self, request, obj):
        return False

    def has_change_permission(self, request, obj):
        return False

    def color_status(self, obj):
        if obj.status == -1:
            status = '运行失败'
            color_code = 'red'
        elif obj.status == -2:
            status = '节点失联'
            color_code = 'gray'
        elif obj.status == 1:
            status = '运行中'
            color_code = '#ecc849'
        elif obj.status == 2:
            status = '已完成'
            color_code = 'green'
        else:
            status = '未知状态'
            color_code = 'red'
        return format_html('<span style="color:{};">{}</span>', color_code, status)

    color_status.short_description = '状态'
    color_status.admin_order_field = 'status'


@admin.register(GPUTask)
class GPUTaskAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'workspace', 'gpu_requirement', 'exclusive_gpu', 'memory_requirement', 'utilization_requirement', 'assign_server', 'priority', 'color_status', 'create_at', 'update_at',)
    list_filter = ('gpu_requirement', 'status', 'assign_server', 'priority')
    search_fields = ('name', 'status',)
    list_display_links = ('name',)
    readonly_fields = ('create_at', 'update_at', 'user',)
    inlines = (GPUTaskRunningLogInline,)
    actions = ('copy_task', 'restart_task',)

    class Media:
        # custom css
        css = {
            'all': ('css/admin/custom.css', )
        }

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(user=request.user)

    def has_add_permission(self, request):
        return True

    def save_model(self, request, obj, form, change):
        if not change:
            obj.user = request.user
        # format cmd
        obj.cmd = obj.cmd.replace('\r\n', '\n')
        if obj.cmd[-1] != '\n':
            obj.cmd = obj.cmd + '\n'
        super().save_model(request, obj, form, change)

    def color_status(self, obj):
        if obj.status == -2:
            status = '未就绪'
            color_code = 'gray'
        elif obj.status == -1:
            status = '运行失败'
            color_code = 'red'
        elif obj.status == -4:
            status = '节点失联'
            color_code = 'gray'
        elif obj.status == -3:
            status = '调度中'
            color_code = '#ecc849'
        elif obj.status == 0:
            status = '准备就绪'
            color_code = 'blue'
        elif obj.status == 1:
            status = '运行中'
            color_code = '#ecc849'
        elif obj.status == 2:
            status = '已完成'
            color_code = 'green'
        else:
            status = '未知状态'
            color_code = 'red'
        return format_html('<span style="color:{};">{}</span>', color_code, status)

    color_status.short_description = '状态'
    color_status.admin_order_field = 'status'

    def delete_queryset(self, request, queryset):
        for task in queryset:
            for running_task in task.task_logs.all():
                running_task.delete_log_file()
            task.delete()

    def copy_task(self, request, queryset):
        for task in queryset:
            new_task = GPUTask(
                name=task.name + '_copy',
                user=task.user,
                workspace=task.workspace,
                cmd=task.cmd,
                exclusive_gpu=task.exclusive_gpu,
                gpu_requirement=task.gpu_requirement,
                memory_requirement=task.memory_requirement,
                utilization_requirement=task.utilization_requirement,
                assign_server=task.assign_server,
                priority=task.priority,
                status=-2
            )
            new_task.save()

    copy_task.short_description = '复制任务'
    copy_task.icon = 'el-icon-document-copy'
    copy_task.type = 'success'

    def restart_task(self, request, queryset):
        for task in queryset:
            task.status = 0
            task.save()

    restart_task.short_description = '重新开始'
    restart_task.icon = 'el-icon-refresh-left'
    restart_task.type = 'success'


@admin.register(GPUTaskRunningLog)
class GPUTaskRunningLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'index', 'task', 'server', 'gpus', 'log_file_path', 'color_status', 'start_at', 'update_at',)
    list_filter = ('task', 'server', 'status')
    search_fields = ('task', 'server',)
    list_display_links = ('task',)
    readonly_fields = ('start_at', 'update_at', 'log', 'task', 'index', 'server', 'gpus', 'status', 'log_file_path', 'pid', 'remote_pid', 'remote_pgid')
    fieldsets = (
        ('基本信息', {'fields': ['task', 'index', 'server', 'gpus', 'pid', 'remote_pid', 'remote_pgid']}),
        ('状态信息', {'fields': ['status', 'start_at', 'update_at']}),
        ('日志', {'fields': ['log_file_path', 'log']})
    )
    actions = ('kill_button',)

    change_form_template = 'admin/task/gputaskrunninglog/change_form.html'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(task__user=request.user)

    def has_add_permission(self, request):
        return False

    def delete_queryset(self, request, queryset):
        for running_task in queryset:
            running_task.delete_log_file()
            running_task.delete()

    def color_status(self, obj):
        if obj.status == -1:
            status = '运行失败'
            color_code = 'red'
        elif obj.status == -2:
            status = '节点失联'
            color_code = 'gray'
        elif obj.status == 1:
            status = '运行中'
            color_code = '#ecc849'
        elif obj.status == 2:
            status = '已完成'
            color_code = 'green'
        else:
            status = '未知状态'
            color_code = 'red'
        return format_html('<span style="color:{};">{}</span>', color_code, status)

    color_status.short_description = '状态'
    color_status.admin_order_field = 'status'

    def log(self, obj):
        try:
            with open(obj.log_file_path, 'r') as f:
                return f.read()
        except Exception:
            return 'Error: Cannot open log file'

    log.short_description = '日志'

    def kill_button(self, request, queryset):
        for running_task in queryset:
            if running_task.status in (1, -2):
                kill_running_log(running_task)

    def response_change(self, request, obj):
        if '_kill_running_log' in request.POST:
            has_perm = request.user.is_superuser or (obj.task_id is not None and obj.task.user_id == request.user.id)
            if not has_perm:
                self.message_user(request, '无权限结束该运行记录的进程。', level=messages.ERROR)
                return HttpResponseRedirect(request.path)

            if obj.status not in (1, -2):
                self.message_user(request, '当前状态不允许结束进程（仅支持“运行中/节点失联”）。', level=messages.WARNING)
                return HttpResponseRedirect(request.path)

            try:
                kill_running_log(obj)
                self.message_user(request, '已发送结束进程请求（仅针对当前运行记录）。', level=messages.SUCCESS)
            except Exception:
                self.message_user(request, '结束进程失败，请查看服务端日志。', level=messages.ERROR)

            return HttpResponseRedirect(request.path)

        return super().response_change(request, obj)

    kill_button.short_description = '结束进程'
    kill_button.icon = 'el-icon-error'
    kill_button.type = 'danger'
    kill_button.confirm = '是否执意结束选中进程？'
