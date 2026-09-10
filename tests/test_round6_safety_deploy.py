import os
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


class FrontendDeploymentSafetyTests(unittest.TestCase):
    def _run_deploy(self, fail_asset='', inject_runtime=False, manifest=True, stale_previous=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        checkout, data, binaries = root / 'checkout', root / 'data', root / 'bin'
        dist = checkout / 'frontend/dist'
        static = data / 'static'
        for path in (dist / 'assets', static / 'assets', binaries):
            path.mkdir(parents=True)
        index = '<html><head><script type="module" src="/assets/index-new.js"></script><link rel="stylesheet" href="/assets/style-new.css"><link rel="icon" href="/logo.svg"></head><body>Omoide</body></html>'
        (dist / 'index.html').write_text(index)
        for name in ('index-new.js', 'style-new.css', 'lazy-current.js'):
            (dist / 'assets' / name).write_text('current asset')
        (dist / 'logo.svg').write_text('<svg/>')
        (static / 'index.html').write_text('old index')
        (static / 'assets/old-tab.js').write_text('still needed by an open tab')
        (static / 'assets/ancient.js').write_text('expired')
        if manifest:
            (static / '.omoide-current-assets.json').write_text(json.dumps(['assets/old-tab.js']))
        stale_time = time.time() - 9 * 86400
        os.utime(static / 'assets/ancient.js', (stale_time, stale_time))
        if stale_previous:
            os.utime(static / 'assets/old-tab.js', (stale_time, stale_time))
        os.utime(dist / 'assets/index-new.js', (stale_time, stale_time))
        os.utime(dist / 'assets/lazy-current.js', (stale_time, stale_time))
        scripts = {
            'npm': '#!/usr/bin/env bash\nexit 0\n',
            'rsync': '''#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
source, target = map(Path, sys.argv[-2:])
if '--delete-after' in sys.argv:
    for p in target.rglob('*'):
        if p.is_file() and not (source / p.relative_to(target)).exists():
            p.unlink()
if source.is_file():
    if target.is_dir():
        target = target / source.name
    shutil.copy2(source, target)
else:
    for p in source.rglob('*'):
        if '--exclude=index.html' in sys.argv and p.name == 'index.html':
            continue
        if p.is_file():
            out = target / p.relative_to(source)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, out)
''',
            'curl': '''#!/usr/bin/env python3
import os, sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
args = sys.argv[1:]
url = next(arg for arg in args if arg.startswith('http'))
path = unquote(urlsplit(url).path).lstrip('/') or 'index.html'
with open(os.environ['REQUEST_LOG'], 'a') as log:
    log.write(path + '\\n')
file = Path(os.environ['HOST_DATA_DIR']) / 'static' / path
content = file.read_bytes() if file.exists() else b''
if path == 'index.html' and os.environ.get('INJECT_RUNTIME') == '1':
    content = content.replace(b'</head>', b'<script>window.runtimeConfig = {"VITE_API_ENABLE_PEOPLE": "true"};</script></head>', 1)
ok = file.exists() and path != os.environ.get('FAIL_ASSET')
if '-w' in args or '--write-out' in args:
    print('200' if ok else '404', end='')
    raise SystemExit(0)
if not ok:
    raise SystemExit(22)
if '-o' in args:
    output = args[args.index('-o') + 1]
    if output != '/dev/null':
        Path(output).write_bytes(content)
else:
    sys.stdout.buffer.write(content)
''',
        }
        for name, content in scripts.items():
            script = binaries / name
            script.write_text(content)
            script.chmod(0o755)
        log = root / 'requests.log'
        env = {**os.environ, 'PATH': str(binaries) + os.pathsep + os.environ['PATH'], 'HOST_DATA_DIR': str(data), 'PORT': '8123', 'REQUEST_LOG': str(log), 'FAIL_ASSET': fail_asset, 'INJECT_RUNTIME': '1' if inject_runtime else '0'}
        script = Path(__file__).resolve().parents[1] / 'scripts/deploy-frontend.sh'
        result = subprocess.run(['bash', str(script), str(checkout)], env=env, capture_output=True, text=True, timeout=20)
        return result, static, log.read_text() if log.exists() else ''

    def test_previous_chunks_survive_and_only_expired_unused_assets_are_pruned(self):
        result, static, _ = self._run_deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((static / 'assets/old-tab.js').exists())
        self.assertFalse((static / 'assets/ancient.js').exists())
        self.assertTrue((static / 'assets/index-new.js').exists())
        self.assertTrue((static / 'assets/lazy-current.js').exists())

    def test_verifies_all_index_references_and_reports_a_missing_stylesheet(self):
        result, _, requests = self._run_deploy(fail_asset='assets/style-new.css')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('assets/style-new.css', requests)

    def test_verifies_script_stylesheet_and_root_icon(self):
        result, _, requests = self._run_deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for asset in ('assets/index-new.js', 'assets/style-new.css', 'logo.svg'):
            self.assertIn(asset, requests)

    def test_live_runtime_configuration_injection_is_accepted(self):
        result, _, _ = self._run_deploy(inject_runtime=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_outgoing_generation_gets_retention_grace_even_after_a_long_release(self):
        result, static, _ = self._run_deploy(stale_previous=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((static / 'assets/old-tab.js').exists())
        self.assertGreater((static / 'assets/index-new.js').stat().st_mtime, time.time() - 60)
        self.assertGreater((static / 'assets/lazy-current.js').stat().st_mtime, time.time() - 60)

    def test_first_deploy_without_manifest_gives_all_existing_chunks_grace(self):
        result, static, _ = self._run_deploy(manifest=False, stale_previous=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((static / 'assets/ancient.js').exists())
        self.assertTrue((static / 'assets/old-tab.js').exists())
