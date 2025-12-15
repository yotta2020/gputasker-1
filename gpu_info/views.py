import json
import time

from django.http import JsonResponse, HttpResponseNotAllowed
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import GPUServer, GPUInfo


def _compact_json_lines(items):
	if not items:
		return ''
	return '\n'.join(json.dumps(item, ensure_ascii=False, separators=(',', ':')) for item in items)


@csrf_exempt
def report_gpu(request):
	if request.method != 'POST':
		return HttpResponseNotAllowed(['POST'])

	try:
		payload = json.loads(request.body.decode('utf-8') or '{}')
	except Exception:
		return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)

	token = payload.get('token')
	gpus = payload.get('gpus')
	if not token or not isinstance(token, str):
		return JsonResponse({'ok': False, 'error': 'missing_token'}, status=401)
	if gpus is None:
		return JsonResponse({'ok': False, 'error': 'missing_gpus'}, status=400)
	if not isinstance(gpus, list):
		return JsonResponse({'ok': False, 'error': 'invalid_gpus'}, status=400)

	try:
		server = GPUServer.objects.get(report_token=token)
	except GPUServer.DoesNotExist:
		return JsonResponse({'ok': False, 'error': 'invalid_token'}, status=403)

	server.valid = True
	server.last_report_at = timezone.now()
	server.save()

	updated = 0
	for gpu in gpus:
		if not isinstance(gpu, dict):
			continue
		uuid = gpu.get('uuid')
		if not uuid:
			continue

		try:
			index = int(gpu.get('index', 0))
			name = str(gpu.get('name', ''))
			utilization = int(gpu.get('utilization', 0))
			memory_total = int(gpu.get('memory_total', 0))
			memory_used = int(gpu.get('memory_used', 0))
		except Exception:
			continue

		processes = gpu.get('processes')
		if not isinstance(processes, list):
			processes = []

		processes_str = _compact_json_lines(processes)
		complete_free = len(processes) == 0

		obj, created = GPUInfo.objects.get_or_create(
			uuid=uuid,
			defaults={
				'index': index,
				'name': name,
				'utilization': utilization,
				'memory_total': memory_total,
				'memory_used': memory_used,
				'processes': processes_str,
				'complete_free': complete_free,
				'server': server,
			},
		)

		if not created:
			obj.index = index
			obj.name = name
			obj.utilization = utilization
			obj.memory_total = memory_total
			obj.memory_used = memory_used
			obj.processes = processes_str
			obj.complete_free = complete_free
			obj.server = server
			obj.save()
		updated += 1

	return JsonResponse({'ok': True, 'updated': updated, 'server': str(server), 'ts': int(time.time())})

