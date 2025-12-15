import json
import time

from django.http import JsonResponse, HttpResponseNotAllowed
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from gpu_info.models import GPUServer
from .models import GPUTaskRunningLog


@csrf_exempt
def report_tasks(request):
	"""Node 侧定期上报“运行中任务心跳”。

	鉴权：使用 GPUServer.report_token（与 report_gpu 相同）。
	"""
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])

	try:
		payload = json.loads(request.body.decode('utf-8') or '{}')
	except Exception:
		return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)

	token = payload.get('token')
	tasks = payload.get('tasks')
	if not token or not isinstance(token, str):
		return JsonResponse({'ok': False, 'error': 'missing_token'}, status=401)
	if tasks is None:
		return JsonResponse({'ok': False, 'error': 'missing_tasks'}, status=400)
	if not isinstance(tasks, list):
		return JsonResponse({'ok': False, 'error': 'invalid_tasks'}, status=400)

	try:
		server = GPUServer.objects.get(report_token=token)
	except GPUServer.DoesNotExist:
		return JsonResponse({'ok': False, 'error': 'invalid_token'}, status=403)

	now = timezone.now()
	# 任务心跳同样可作为节点存活信号
	server.valid = True
	server.last_report_at = now
	server.save(update_fields=['valid', 'last_report_at'])

	updated = 0
	revived = 0
	for item in tasks:
		if not isinstance(item, dict):
			continue
		log_id = item.get('running_log_id') or item.get('log_id')
		if log_id is None:
			continue
		try:
			log_id = int(log_id)
		except Exception:
			continue

		try:
			running_log = GPUTaskRunningLog.objects.select_related('task', 'server').get(id=log_id)
		except GPUTaskRunningLog.DoesNotExist:
			continue

		# 防止跨节点伪造心跳
		if running_log.server_id != server.id:
			continue

		fields = ['last_heartbeat_at', 'update_at']
		running_log.last_heartbeat_at = now

		# 允许 agent 回传 remote pid/pgid（仅在 DB 未记录时补齐）
		remote_pid = item.get('remote_pid')
		remote_pgid = item.get('remote_pgid')
		if running_log.remote_pid is None and remote_pid is not None:
			try:
				running_log.remote_pid = int(remote_pid)
				fields.append('remote_pid')
			except Exception:
				pass
		if running_log.remote_pgid is None and remote_pgid is not None:
			try:
				running_log.remote_pgid = int(remote_pgid)
				fields.append('remote_pgid')
			except Exception:
				pass

		# 被标记为“节点失联”的任务，收到心跳后恢复为运行中
		if running_log.status != 1:
			running_log.status = 1
			fields.append('status')
			revived += 1

		running_log.save(update_fields=fields)

		if running_log.task_id and running_log.task.status != 1:
			# 只把“节点失联”类恢复为运行中；避免覆盖已完成/失败
			if running_log.task.status == -4:
				running_log.task.status = 1
				running_log.task.save(update_fields=['status', 'update_at'])

		updated += 1

	return JsonResponse({'ok': True, 'updated': updated, 'revived': revived, 'ts': int(time.time())})
