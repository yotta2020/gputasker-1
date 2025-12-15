from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from .models import GPUTask, GPUTaskRunningLog, Project, TaskGroup
from .utils import kill_running_log


class TaskGroupInline(admin.TabularInline):
    model = TaskGroup
    fields = ('name', 'archived', 'create_at', 'update_at')
    readonly_fields = ('create_at', 'update_at')
    extra = 0
    show_change_link = True


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'archived', 'create_at', 'update_at')
    list_filter = ('archived',)
    search_fields = ('name',)
    readonly_fields = ('create_at', 'update_at')
    inlines = (TaskGroupInline,)

    def get_model_perms(self, request):
        # 仅从“GPU任务 Dashboard”入口访问，避免 SimpleUI 菜单混淆。
        return {}


class GPUTaskInline(admin.TabularInline):
    model = GPUTask
    fields = (
        'name',
        'workspace',
        'cmd',
        'gpu_requirement',
        'exclusive_gpu',
        'memory_requirement',
        'utilization_requirement',
        'assign_server',
        'priority',
        'status',
        'create_at',
        'update_at',
    )
    readonly_fields = ('create_at', 'update_at')
    extra = 0
    show_change_link = True


@admin.register(TaskGroup)
class TaskGroupAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'project', 'archived', 'create_at', 'update_at')
    list_filter = ('archived', 'project')
    search_fields = ('name', 'project__name')
    readonly_fields = ('create_at', 'update_at')
    inlines = (GPUTaskInline,)

    def get_model_perms(self, request):
        # 仅从“GPU任务 Dashboard / Project页”入口访问。
        return {}

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        project_id = request.GET.get('project')
        if project_id:
            initial['project'] = project_id
        return initial

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, GPUTask):
                if not obj.user_id:
                    obj.user = request.user
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()


class GPUTaskRunningLogInline(admin.TabularInline):
    model = GPUTaskRunningLog
    fields = ('index', 'server', 'gpus', 'log_file_path', 'remark', 'color_status', 'start_at', 'update_at',)
    readonly_fields = ('index', 'server', 'gpus', 'log_file_path', 'color_status', 'start_at', 'update_at',)

    show_change_link = True

    verbose_name = '运行记录'
    verbose_name_plural = '运行记录'

    def get_extra(self, request, obj, **kwargs):
        return 0

    def has_add_permission(self, request, obj):
        return False

    def has_change_permission(self, request, obj):
        return True

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
    list_filter = ('group', 'gpu_requirement', 'status', 'assign_server', 'priority')
    search_fields = ('name', 'status',)
    list_display_links = ('name',)
    readonly_fields = ('create_at', 'update_at', 'user',)
    inlines = (GPUTaskRunningLogInline,)
    actions = ('move_to_group', 'copy_task', 'restart_task',)

    class MoveToGroupForm(forms.Form):
        taskgroup = forms.ModelChoiceField(
            label='目标分组',
            queryset=TaskGroup.objects.filter(archived=False).select_related('project').order_by('project__name', 'name', 'id'),
            required=True,
        )

    class Media:
        # custom css
        css = {
            'all': ('css/admin/custom.css', )
        }

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            base = qs
        else:
            base = qs.filter(user=request.user)

        fixed_group_id = getattr(request, '_fixed_taskgroup_id', None) or request.GET.get('taskgroup')
        if fixed_group_id:
            return base.filter(group_id=fixed_group_id)
        return base

    def has_add_permission(self, request):
        return False

    def get_urls(self):
        urls = super().get_urls()
        extra = [
            path(
                'project/<int:project_id>/',
                self.admin_site.admin_view(self.project_view),
                name='task_gputask_project_view',
            ),
            path(
                'group/<int:taskgroup_id>/',
                self.admin_site.admin_view(self.group_view),
                name='task_gputask_group_view',
            ),
        ]
        return extra + urls

    def project_view(self, request, project_id: int):
        """Project -> Group 列表页（不用 query 传参，兼容 SimpleUI hash 路由）。"""
        try:
            project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            project = None

        groups = TaskGroup.objects.none()
        if project is not None:
            groups = TaskGroup.objects.filter(project=project).order_by('archived', 'name', 'id')

        context = {
            **self.admin_site.each_context(request),
            'title': 'Task Groups',
            'project': project,
            'groups': groups,
        }
        return TemplateResponse(request, 'admin/task/gputask/project.html', context)

    def group_view(self, request, taskgroup_id: int):
        """按 Group 进入任务列表（不用 query 传参，兼容 SimpleUI hash 路由）。"""
        request._fixed_taskgroup_id = str(taskgroup_id)
        extra_context = {}
        try:
            fixed_group = TaskGroup.objects.select_related('project').get(id=taskgroup_id)
            extra_context['fixed_project'] = fixed_group.project
            extra_context['fixed_group'] = fixed_group
        except TaskGroup.DoesNotExist:
            pass
        return super().changelist_view(request, extra_context=extra_context)

    def changelist_view(self, request, extra_context=None):
        project_id = request.GET.get('project')
        taskgroup_id = request.GET.get('taskgroup')

        if taskgroup_id:
            request._fixed_taskgroup_id = taskgroup_id
            extra_context = extra_context or {}
            try:
                fixed_group = TaskGroup.objects.select_related('project').get(id=taskgroup_id)
                extra_context['fixed_project'] = fixed_group.project
                extra_context['fixed_group'] = fixed_group
            except TaskGroup.DoesNotExist:
                pass
            return super().changelist_view(request, extra_context=extra_context)

        if project_id:
            try:
                project = Project.objects.get(id=project_id)
            except Project.DoesNotExist:
                project = None

            groups = TaskGroup.objects.none()
            if project is not None:
                groups = TaskGroup.objects.filter(project=project).order_by('archived', 'name', 'id')

            context = {
                **self.admin_site.each_context(request),
                'title': 'Task Groups',
                'project': project,
                'groups': groups,
            }
            return TemplateResponse(request, 'admin/task/gputask/project.html', context)

        projects = Project.objects.all().order_by('archived', 'name', 'id')
        recent_tasks = (
            self.get_queryset(request)
            .select_related('group__project')
            .order_by('-update_at', '-id')[:20]
        )
        context = {
            **self.admin_site.each_context(request),
            'title': 'GPU任务',
            'projects': projects,
            'recent_tasks': recent_tasks,
            'add_project_url': reverse('admin:task_project_add'),
            'task_changelist_url': reverse('admin:task_gputask_changelist'),
        }
        return TemplateResponse(request, 'admin/task/gputask/dashboard.html', context)

    def move_to_group(self, request, queryset):
        if 'apply' in request.POST:
            form = self.MoveToGroupForm(request.POST)
            if form.is_valid():
                target_group = form.cleaned_data['taskgroup']
                updated = queryset.update(group=target_group, update_at=timezone.now())
                self.message_user(request, f'已移动 {updated} 个任务到分组：{target_group}', level=messages.SUCCESS)
                return HttpResponseRedirect(request.get_full_path())
        else:
            form = self.MoveToGroupForm()

        context = {
            **self.admin_site.each_context(request),
            'title': '批量移动到分组',
            'objects': queryset,
            'form': form,
            'action_name': 'move_to_group',
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'opts': self.model._meta,
        }
        return TemplateResponse(request, 'admin/task/gputask/move_to_group.html', context)

    move_to_group.short_description = '批量移动到分组'
    move_to_group.icon = 'el-icon-folder'
    move_to_group.type = 'primary'

    def save_model(self, request, obj, form, change):
        if not change:
            obj.user = request.user
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
                group=task.group,
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
    list_display = ('id', 'index', 'task', 'server', 'gpus', 'log_file_path', 'remark', 'color_status', 'start_at', 'update_at',)
    list_filter = ('task', 'server', 'status')
    search_fields = ('task', 'server',)
    list_display_links = ('task',)
    readonly_fields = ('start_at', 'update_at', 'log', 'task', 'index', 'server', 'gpus', 'status', 'log_file_path', 'pid', 'remote_pid', 'remote_pgid')
    fieldsets = (
        ('基本信息', {'fields': ['task', 'index', 'server', 'gpus', 'pid', 'remote_pid', 'remote_pgid']}),
        ('状态信息', {'fields': ['status', 'start_at', 'update_at']}),
        ('备注', {'fields': ['remark']}),
        ('日志', {'fields': ['log_file_path', 'log']}),
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
