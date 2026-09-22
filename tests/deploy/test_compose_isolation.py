"""Render-only regression checks: no daemon, no application test fixtures."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ComposeIsolationTests(unittest.TestCase):
    def render(self, local=False):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('HLM_', 'BAKE_'))}
        for name in ('APP', 'API', 'DB'):
            env[f'HLM_{name}_ENV_FILE'] = str(ROOT / 'deploy' / f'{name.lower()}.env.example')
        if local:
            env.update(BAKE_PROJECT='bake-astra', BAKE_BIND_IP='127.0.0.1',
                       BAKE_HTTP_PORT='18080', BAKE_HTTPS_PORT='18443')
        return json.loads(subprocess.check_output(
            ['docker', 'compose', '-f', 'deploy/compose.prod.yaml', '--env-file',
             'deploy/.env.prod.example', 'config', '--format', 'json'], cwd=ROOT, env=env))

    def test_secrets_reach_only_their_consumers(self):
        services = self.render()['services']
        for name, service in services.items():
            keys = set(service.get('environment', {}))
            self.assertFalse(any(key.startswith(('AWS_', 'S3_')) for key in keys), name)
            if name != 'api':
                self.assertTrue(keys.isdisjoint({'HLM_ADMIN_TOKEN', 'HLM_REGISTRATION_SECRET',
                                                'HLM_CURSOR_SECRET'}), name)
        self.assertEqual(set(services['db']['environment']),
                         {'POSTGRES_USER', 'POSTGRES_DB', 'POSTGRES_PASSWORD', 'POSTGRES_INITDB_ARGS'})
        self.assertIn('HLM_ADMIN_TOKEN', services['api']['environment'])

    def test_production_all_interfaces_and_http3_local_loopback(self):
        for local in (False, True):
            services = self.render(local)['services']
            ports = services['caddy']['ports']
            self.assertEqual({(p['target'], p['protocol']) for p in ports},
                             {(80, 'tcp'), (443, 'tcp'), (443, 'udp')})
            for port in ports:
                if local:
                    self.assertEqual(port['host_ip'], '127.0.0.1')
                    self.assertIn(str(port['published']), {'18080', '18443'})
                else:
                    self.assertNotIn('host_ip', port)
            for name in ('db', 'api', 'worker', 'migrate'):
                self.assertFalse(services[name].get('ports'))


if __name__ == '__main__':
    unittest.main()
