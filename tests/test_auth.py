import base64
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit, urlencode

from codine.credentials import CredentialStore
from codine.openrouter_auth import Callback, login
from codine.auth_exchange import exchange, NoRedirect
from codine.auth_cli import main


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.store = CredentialStore(self.home / 'credentials')

    def test_store_roundtrip_preserves_other_provider_and_private_modes(self):
        self.assertEqual(self.store.get('openrouter'), '')
        self.assertFalse(self.store.root.exists())
        self.store.set('openrouter', 'secret-openrouter-test')
        self.store.set('nvidia', 'secret-nvidia-test')
        self.assertEqual(self.store.get('openrouter'), 'secret-openrouter-test')
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.store.root/'keys.json').stat().st_mode & 0o777, 0o600)
        self.store.delete('openrouter')
        self.assertEqual(self.store.get('openrouter'), '')
        self.assertEqual(self.store.get('nvidia'), 'secret-nvidia-test')

    def test_unsafe_file_directory_link_and_busy_lock(self):
        self.store.set('openrouter', 'secret-test-value')
        path = self.store.root/'keys.json'
        path.chmod(0o644)
        with self.assertRaises(ValueError): self.store.get('openrouter')
        path.chmod(0o600)
        os.link(path, self.home/'alias')
        with self.assertRaises(ValueError): self.store.get('openrouter')
        (self.home/'alias').unlink()
        content = path.read_bytes(); path.unlink(); path.symlink_to(self.home/'outside')
        with self.assertRaises(OSError): self.store.set('openrouter', 'other-secret-value')
        path.unlink(); path.write_bytes(content); path.chmod(0o600)
        with (self.store.root/'lock').open('r+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'busy'): self.store.get('openrouter')
        self.store.root.chmod(0o755)
        with self.assertRaises(ValueError): self.store.get('openrouter')

    def test_duplicate_and_malformed_secrets_do_not_appear_in_errors(self):
        self.store.set('openrouter', 'secret-test-value')
        path = self.store.root/'keys.json'
        for body in ('{"version":1,"keys":{"openrouter":"secret-test-value","openrouter":"another"}}',
                     '{"version":1,"keys":{"openrouter":{"token":"secret-test-value"}}}'):
            path.write_text(body)
            with self.assertRaises(ValueError) as error: self.store.get('openrouter')
            self.assertNotIn('secret-test-value', str(error.exception))

    def test_callback_validation(self):
        callback = Callback(.2); self.addCleanup(callback.close)
        target = callback.path + '?' + urlencode({'code':'auth-code-test','state':callback.state})
        request = lambda t=target, host=callback.host: f'GET {t} HTTP/1.1\r\nHost: {host}\r\n\r\n'.encode()
        self.assertEqual(callback.parse(request()), 'auth-code-test')
        for raw in (request(host='evil.example'), request(t=target+'&state=duplicate'),
                    request(t=target.replace(callback.state, 'wrong')),
                    request(t=target.replace(callback.state, '%C3%A9')), request(t='/wrong?'+target.split('?')[1]),
                    request(t='http://evil.example'+target), request().replace(b'GET',b'POST'),
                    request().replace(b'\r\n\r\n', b'\r\nHost: evil\r\n\r\n'), request()+b'body'):
            self.assertIsNone(callback.parse(raw))

    def test_real_loopback_login_verifier_binding_and_single_exchange(self):
        threads = []; observed = {}; fake_exchange = Mock(return_value='secret-test-key')
        def browser(url):
            query = parse_qs(urlsplit(url).query); observed.update(query)
            callback = urlsplit(query['callback_url'][0])
            target = callback.path + '?' + urlencode({'state':query['state'][0], 'code':'one-time-code'})
            def respond():
                bad_target=callback.path+'?'+urlencode({'state':'é','code':'one-time-code'})
                from tests.local_network import allow_loopback
                with allow_loopback((callback.hostname, callback.port)):
                    with socket.create_connection((callback.hostname, callback.port), timeout=2) as connection:
                        connection.sendall(f'GET {bad_target} HTTP/1.1\r\nHost: {callback.netloc}\r\n\r\n'.encode())
                        observed['bad_response']=connection.recv(4096)
                    with socket.create_connection((callback.hostname, callback.port), timeout=2) as connection:
                        connection.sendall(f'GET {target} HTTP/1.1\r\nHost: {callback.netloc}\r\n\r\n'.encode())
                        observed['response'] = connection.recv(4096)
            thread=threading.Thread(target=respond);thread.start();threads.append(thread)
            return True
        result=login(open_browser=browser, exchange_code=fake_exchange, timeout=2)
        for thread in threads: thread.join(3)
        self.assertEqual(result, 'secret-test-key'); fake_exchange.assert_called_once()
        code, verifier = fake_exchange.call_args.args
        self.assertEqual(code, 'one-time-code')
        self.assertEqual(observed['code_challenge'][0], base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode())
        self.assertEqual(observed['code_challenge_method'], ['S256'])
        self.assertNotIn(b'one-time-code', observed['response'])
        self.assertIn(b'no-store', observed['response'])
        self.assertIn(b'400 Bad Request',observed['bad_response'])

    def test_timeout_and_cancel_never_exchange(self):
        fake = Mock()
        with self.assertRaisesRegex(ValueError, 'timed out'): login(open_browser=lambda _:True, exchange_code=fake, timeout=.03)
        with self.assertRaises(KeyboardInterrupt): login(open_browser=Mock(side_effect=KeyboardInterrupt), exchange_code=fake)
        fake.assert_not_called()

    def test_exchange_fixed_destination_no_auth_redirect_or_error_body(self):
        response = Mock(); response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.status=200;response.read.return_value=b'{"key":"secret-returned-key"}'
        opener=Mock();opener.open.return_value=response
        with patch('codine.auth_exchange.build_opener', return_value=opener):
            self.assertEqual(exchange('private-code','private-verifier'), 'secret-returned-key')
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://openrouter.ai/api/v1/auth/keys')
        self.assertIsNone(request.get_header('Authorization'))
        self.assertNotIn('private-code', request.full_url)
        self.assertEqual(json.loads(request.data)['code_verifier'],'private-verifier')
        with self.assertRaises(ValueError): NoRedirect().redirect_request(None,None,None,None,None,None)
        opener.open.side_effect=RuntimeError('private-code private-verifier secret-returned-key')
        with patch('codine.auth_exchange.build_opener', return_value=opener):
            with self.assertRaises(ValueError) as error: exchange('private-code','private-verifier')
        self.assertNotIn('private-code',str(error.exception))

    def test_cli_no_secret_echo_noninteractive_and_logout(self):
        out=io.StringIO();err=io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch('sys.stdin.isatty', return_value=True):
            self.assertEqual(main(['set-key','openrouter'],store=self.store,input_key=lambda _: 'secret-entered-key'),0)
            self.assertEqual(main(['logout','openrouter'],store=self.store),0)
        self.assertNotIn('secret-entered-key',out.getvalue()+err.getvalue())
        self.assertIn('Remote API key remains valid',out.getvalue())
        fake=Mock()
        with contextlib.redirect_stderr(err), patch('sys.stdin.isatty',return_value=False):
            self.assertEqual(main(['login'],store=self.store,login=fake),1)
        fake.assert_not_called()

    def test_model_filetools_protect_all_credentials_even_inactive(self):
        from codine.workspace import Workspace
        self.store.set('nvidia','inactive-secret-key')
        with patch('codine.credentials.AUTH_ROOT', self.store.root):
            workspace=Workspace(self.home, lambda _:True)
            with self.assertRaises(ValueError): workspace.read_file('credentials/keys.json')
            with self.assertRaises(ValueError): workspace.resolve('credentials/pending-new',operation='write')

    def test_exchange_hard_deadline_cleans_child_and_excludes_ambient_secrets(self):
        import subprocess
        from codine.openrouter_auth import exchange as bounded_exchange
        process=Mock(returncode=0)
        process.communicate.return_value=(b'{"key":"test-returned-api-key"}',None)
        with patch('subprocess.Popen', return_value=process) as spawn:
            self.assertEqual(bounded_exchange('test-private-code','test-private-verifier'),'test-returned-api-key')
        args, kwargs=spawn.call_args
        self.assertNotIn('test-private-code',str(args));self.assertEqual(kwargs['env'],{'LANG':'C.UTF-8'})
        self.assertIn(b'test-private-verifier',process.communicate.call_args.args[0])
        self.assertEqual(process.communicate.call_args.kwargs['timeout'],20)
        process.communicate.side_effect=[subprocess.TimeoutExpired('private-code',20),(b'',None)]
        with patch('subprocess.Popen',return_value=process):
            with self.assertRaises(ValueError) as error: bounded_exchange('private-code','private-verifier')
        process.kill.assert_called_once();self.assertNotIn('private-code',str(error.exception))

    def test_config_fallback_explicit_empty_and_redaction(self):
        from codine.config import Config
        project=self.home/'project';project.mkdir()
        self.store.set('openrouter','stored-provider-secret')
        # Keep every input isolated from the real user's credentials.
        with patch('codine.credentials.AUTH_ROOT',self.store.root), patch.dict(os.environ,{'HOME':str(self.home)},clear=True), \
             patch('codine.settings.USER_ENV',self.home/'user.env'), patch('codine.settings.LEGACY_ENV',None):
            config=Config.load(project,provider='openrouter',model='test/model')
            self.assertEqual(config.api_key,'stored-provider-secret')
            self.assertEqual(config.setting_sources['api_key'],'user credential store')
            self.assertEqual(config.redactor().text('stored-provider-secret'),'[REDACTED]')
            (project/'.env').write_text('OPENROUTER_API_KEY=\n')
            self.assertEqual(Config.load(project,provider='openrouter',model='test/model').api_key,'')
            with patch.dict(os.environ,{'OPENROUTER_API_KEY':'shell-secret-key'}):
                self.assertEqual(Config.load(project,provider='openrouter',model='test/model').api_key,'shell-secret-key')

    def test_custom_endpoint_binding_and_no_builtin_key_reuse(self):
        from codine.custom_endpoint import configure
        from codine.settings import Settings
        project=self.home/'project';project.mkdir()
        self.store.set('custom','custom-private-key','https://example.test/v1/')
        with patch('codine.credentials.AUTH_ROOT',self.store.root), \
             patch('codine.settings.USER_ENV',self.home/'user.env'), patch('codine.settings.LEGACY_ENV',None):
            settings=Settings(project,{})
            config=configure(settings,'https://EXAMPLE.test:443/v1',True)
            self.assertEqual(config['api_key'],'custom-private-key')
            with self.assertRaises(ValueError): configure(settings,'https://other.test/v1',True)
            self.store.set('openrouter','custom-private-key')
            with self.assertRaises(ValueError): configure(settings,'https://example.test/v1',True)
            settings=Settings(project,{'CODINE_CUSTOM_API_KEY':''})
            with self.assertRaises(ValueError): configure(settings,'https://example.test/v1',True)

    def test_status_does_not_validate_contact_provider_or_print_key(self):
        from codine.settings import Settings
        self.store.set('openrouter','stored-status-secret')
        project=self.home/'project';project.mkdir()
        out=io.StringIO()
        with patch('codine.auth_cli.Path.cwd',return_value=project), \
             patch('codine.settings.USER_ENV',self.home/'user.env'), patch('codine.settings.LEGACY_ENV',None), \
             patch.dict(os.environ,{'HOME':str(self.home)},clear=True),contextlib.redirect_stdout(out):
            self.assertEqual(main(['status'],store=self.store),0)
            self.assertIn('user credential store',out.getvalue())
            (project/'.env').write_text('OPENROUTER_API_KEY=\n')
            out.seek(0);out.truncate()
            self.assertEqual(main(['status'],store=self.store),0)
            self.assertIn('not configured; source: project .env',out.getvalue())
        self.assertNotIn('stored-status-secret',out.getvalue())

    def test_launcher_auth_needs_no_model_or_provider_sdk(self):
        from codine.launcher import main as launch
        with patch('codine.auth_cli.main',return_value=0) as auth:
            self.assertEqual(launch(['auth','status','openrouter']),0)
            auth.assert_called_once_with(['status','openrouter'])
