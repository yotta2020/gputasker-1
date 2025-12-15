from django.db import migrations, models
import django.db.models.deletion


def create_default_project_group(apps, schema_editor):
    Project = apps.get_model('task', 'Project')
    TaskGroup = apps.get_model('task', 'TaskGroup')
    GPUTask = apps.get_model('task', 'GPUTask')

    default_project, _ = Project.objects.get_or_create(
        name='Default',
        defaults={'archived': False},
    )
    default_group, _ = TaskGroup.objects.get_or_create(
        project=default_project,
        name='Default',
        defaults={'archived': False},
    )

    GPUTask.objects.filter(group__isnull=True).update(group=default_group)


class Migration(migrations.Migration):

    dependencies = [
        ('task', '0003_task_heartbeat_and_lost_status'),
    ]

    operations = [
        migrations.CreateModel(
            name='Project',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, unique=True, verbose_name='项目名称')),
                ('archived', models.BooleanField(default=False, verbose_name='归档')),
                ('create_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('update_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '项目',
                'verbose_name_plural': '项目',
                'ordering': ('archived', 'name', 'id'),
            },
        ),
        migrations.CreateModel(
            name='TaskGroup',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='分组名称')),
                ('archived', models.BooleanField(default=False, verbose_name='归档')),
                ('create_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('update_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='groups', to='task.project', verbose_name='项目')),
            ],
            options={
                'verbose_name': '任务分组',
                'verbose_name_plural': '任务分组',
                'ordering': ('archived', 'name', 'id'),
            },
        ),
        migrations.AddConstraint(
            model_name='taskgroup',
            constraint=models.UniqueConstraint(fields=('project', 'name'), name='uniq_taskgroup_project_name'),
        ),
        migrations.AddField(
            model_name='gputask',
            name='group',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tasks', to='task.taskgroup', verbose_name='分组'),
        ),
        migrations.AddField(
            model_name='gputaskrunninglog',
            name='remark',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name='备注'),
        ),
        migrations.RunPython(create_default_project_group, migrations.RunPython.noop),
    ]
