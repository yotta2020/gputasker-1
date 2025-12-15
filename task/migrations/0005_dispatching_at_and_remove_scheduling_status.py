from django.db import migrations, models


def migrate_scheduling_to_ready(apps, schema_editor):
    GPUTask = apps.get_model('task', 'GPUTask')
    # 历史遗留：status=-3("调度中") 与 "准备就绪" 语义重叠，统一合并。
    GPUTask.objects.filter(status=-3).update(status=0, dispatching_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ('task', '0004_project_taskgroup_and_remark'),
    ]

    operations = [
        migrations.AddField(
            model_name='gputask',
            name='dispatching_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='调度认领时间'),
        ),
        migrations.RunPython(migrate_scheduling_to_ready, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='gputask',
            name='status',
            field=models.SmallIntegerField(
                choices=[(-2, '未就绪'), (-1, '运行失败'), (-4, '节点失联'), (0, '准备就绪'), (1, '运行中'), (2, '已完成')],
                default=0,
                verbose_name='状态',
            ),
        ),
    ]
