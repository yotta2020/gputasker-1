from __future__ import annotations

import os

from django.core.management.base import BaseCommand

from base.utils import get_admin_config
from gpu_info.models import GPUServer
from gpu_info.utils import start_node_agent, stop_node_agent, restart_node_agent
from gpu_info.utils import build_report_gpu_url


class Command(BaseCommand):
    help = 'Manage node GPU agent processes via SSH.'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['start', 'stop', 'restart'], help='start/stop/restart node agents')
        parser.add_argument(
            '--server-url',
            default=os.environ.get('GPUTASKER_SERVER_URL', build_report_gpu_url()),
            help='Master report endpoint URL (required for start)',
        )
        parser.add_argument(
            '--ip',
            action='append',
            default=[],
            help='Only operate on these GPUServer ip(s). Can be provided multiple times.',
        )
        parser.add_argument(
            '--ip-file',
            default='',
            help='Path to a file containing one IP per line (comments with # allowed).',
        )

    def handle(self, *args, **options):
        action = options['action']
        server_url = options['server_url']
        ip_filters = [ip.strip() for ip in (options.get('ip') or []) if ip and ip.strip()]
        ip_file = (options.get('ip_file') or '').strip()
        if ip_file:
            with open(ip_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    ip_filters.append(line)
            ip_filters = list(dict.fromkeys(ip_filters))

        ssh_user, ssh_key = get_admin_config()
        qs = GPUServer.objects.all()
        if ip_filters:
            qs = qs.filter(ip__in=ip_filters)
        servers = list(qs)

        ok = 0
        fail = 0
        for server in servers:
            try:
                if action == 'start':
                    out = start_node_agent(server, server_url, ssh_user=ssh_user, ssh_private_key_path=ssh_key)
                elif action == 'restart':
                    out = restart_node_agent(server, server_url, ssh_user=ssh_user, ssh_private_key_path=ssh_key)
                else:
                    out = stop_node_agent(server, ssh_user=ssh_user, ssh_private_key_path=ssh_key)
                self.stdout.write(f'[{server}] {out}')
                ok += 1
            except Exception as exc:  # pylint: disable=broad-except
                self.stderr.write(f'[{server}] FAILED: {exc}')
                fail += 1

        if fail:
            raise SystemExit(f'node_agents: {ok} ok, {fail} failed')
        self.stdout.write(f'node_agents: {ok} ok, {fail} failed')
