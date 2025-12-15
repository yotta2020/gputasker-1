import os

from django.contrib import admin, messages
from django.urls import reverse
from django.utils import timezone

from base.utils import get_admin_config
from .models import GPUServer, GPUInfo
from .utils import start_node_agent, stop_node_agent, restart_node_agent
from .utils import build_report_gpu_url


class GPUInfoInline(admin.TabularInline):
    model = GPUInfo
    fields = ('index', 'name', 'utilization', 'memory_usage', 'usernames', 'complete_free', 'update_at')
    readonly_fields = ('index', 'name', 'utilization', 'memory_usage', 'usernames', 'complete_free', 'update_at')

    show_change_link = True

    def usernames(self, obj):
        return obj.usernames()

    def memory_usage(self, obj):
        memory_total = obj.memory_total
        memory_used = obj.memory_used
        return '{:d} / {:d} MB ({:.0f}%)'.format(memory_used, memory_total, memory_used / memory_total * 100)

    memory_usage.short_description = '显存占用率'
    usernames.short_description = '使用者'

    def get_extra(self, request, obj, **kwargs):
        return 0

    def has_add_permission(self, request, obj):
        return False

    def has_change_permission(self, request, obj):
        return False

    def has_delete_permission(self, request, obj):
        return False


@admin.register(GPUServer)
class GPUServerAdmin(admin.ModelAdmin):
    list_display = ('ip', 'hostname', 'port', 'available_status', 'can_use', 'last_report_at', 'report_token')
    list_editable = ('can_use',)
    search_fields = ('ip', 'hostname', 'port', 'valid', 'can_use')
    list_display_links = ('ip',)
    inlines = (GPUInfoInline,)
    ordering = ('ip',)
    readonly_fields = ('hostname',)

    class Media:
        # custom css
        css = {
            'all': ('css/admin/custom.css', )
        }

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def available_status(self, obj: GPUServer):
        update_mode = (os.getenv('GPUTASKER_GPU_UPDATE_MODE', 'report') or 'report').strip().lower()
        if update_mode != 'report':
            return bool(obj.valid)
        stale_seconds = int(os.getenv('GPUTASKER_NODE_STALE_SECONDS', '180'))
        if obj.last_report_at is None:
            return False
        delta = timezone.now() - obj.last_report_at
        return delta.total_seconds() <= stale_seconds

    available_status.boolean = True
    available_status.short_description = '是否可用'

    actions = ('restart_selected_agents',)

    def restart_selected_agents(self, request, queryset):
        ssh_user, ssh_key = get_admin_config()
        server_url = build_report_gpu_url()
        ok = 0
        fail = 0
        for obj in queryset:
            try:
                out = restart_node_agent(obj, server_url, ssh_user=ssh_user, ssh_private_key_path=ssh_key)
                messages.info(request, f'{obj} 重启 node agent: {out}')
                ok += 1
            except Exception as exc:
                messages.error(request, f'{obj} 重启 node agent 失败：{exc}')
                fail += 1
        if ok and not fail:
            messages.success(request, f'已重启 {ok} 个Node agent')

    restart_selected_agents.short_description = '重启'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        # 需求：在 Web 添加/保存 node 后，Master 通过 SSH 自动启动 agent。
        if (os.getenv('GPUTASKER_AUTO_NODE_AGENT', '1') or '1').strip() not in {'0', 'false', 'False'}:
            try:
                ssh_user, ssh_key = get_admin_config()
                server_url = build_report_gpu_url()
                out = start_node_agent(obj, server_url, ssh_user=ssh_user, ssh_private_key_path=ssh_key)
                messages.info(request, f'已尝试启动 node agent: {out}')
            except Exception as exc:
                messages.warning(request, f'自动启动 node agent 失败（不影响保存）：{exc}')

    def delete_model(self, request, obj):
        # 删除前尽力停止 agent；失败也允许删除，避免无法删除的死锁。
        if (os.getenv('GPUTASKER_AUTO_NODE_AGENT', '1') or '1').strip() not in {'0', 'false', 'False'}:
            try:
                ssh_user, ssh_key = get_admin_config()
                out = stop_node_agent(obj, ssh_user=ssh_user, ssh_private_key_path=ssh_key)
                messages.info(request, f'已尝试停止 node agent: {out}')
            except Exception as exc:
                messages.warning(request, f'停止 node agent 失败（仍将删除）：{exc}')
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        if (os.getenv('GPUTASKER_AUTO_NODE_AGENT', '1') or '1').strip() not in {'0', 'false', 'False'}:
            ssh_user, ssh_key = get_admin_config()
            for obj in queryset:
                try:
                    out = stop_node_agent(obj, ssh_user=ssh_user, ssh_private_key_path=ssh_key)
                    messages.info(request, f'{obj} 停止 node agent: {out}')
                except Exception as exc:
                    messages.warning(request, f'{obj} 停止 node agent 失败（仍将删除）：{exc}')
        super().delete_queryset(request, queryset)


@admin.register(GPUInfo)
class GPUInfoAdmin(admin.ModelAdmin):
    list_display = ('index', 'name', 'server', 'utilization', 'memory_usage', 'usernames', 'complete_free', 'update_at')
    list_filter = ('server', 'name', 'complete_free')
    search_fields = ('uuid', 'name', 'memory_used', 'server',)
    list_display_links = ('name',)
    ordering = ('server', 'index')
    readonly_fields = ('uuid', 'name', 'index', 'utilization', 'memory_total', 'memory_used','server', 'processes', 'use_by_self', 'complete_free', 'update_at')

    def usernames(self, obj):
        return obj.usernames()

    def has_add_permission(self, request):
        return False

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def memory_usage(self, obj):
        memory_total = obj.memory_total
        memory_used = obj.memory_used
        return '{:d} / {:d} MB ({:.0f}%)'.format(memory_used, memory_total, memory_used / memory_total * 100)
    
    memory_usage.short_description = '显存占用率'
    usernames.short_description = '使用者'
