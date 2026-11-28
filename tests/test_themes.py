import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from codine.config import Redactor
from codine.terminal import Terminal, STYLE
from codine.themes import NAMES, PALETTES, load, save, style_for, command


class ThemeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.project=self.root/'project';self.project.mkdir()
        self.path=self.root/'user/theme.json'

    def test_private_preferences_and_project_authority(self):
        self.assertEqual(load(self.project,self.path),'auto')
        for name in NAMES:
            save(self.project,name,self.path)
            self.assertEqual(load(self.project,self.path),name)
            self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        with self.assertRaises(ValueError):save(self.project,'untrusted escape',self.path)
        with self.assertRaises(ValueError):save(self.project,'dark',self.project/'theme.json')
        self.path.unlink();self.path.symlink_to(self.root/'unrelated')
        with self.assertRaises(OSError):save(self.project,'dark',self.path)
        self.assertFalse((self.root/'unrelated').exists())

    def test_invalid_oversized_or_shared_preferences_fail_closed(self):
        self.path.parent.mkdir(mode=0o700)
        for value in ({'version':True,'theme':'light'},{'version':1,'theme':'unknown'},
                      {'version':1,'theme':'dark','css':'arbitrary'},'x'*2000):
            self.path.write_text(json.dumps(value));self.path.chmod(0o600)
            with self.assertRaises(ValueError):load(self.project,self.path)
        self.path.write_text('{"version":1,"theme":"dark"}');self.path.chmod(0o644)
        with self.assertRaises(ValueError):load(self.project,self.path)

    def test_explicit_palette_text_meets_contrast_target(self):
        def luminance(color):
            values=[int(color[i:i+2],16)/255 for i in (1,3,5)]
            linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in values]
            return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))
        for name,palette in PALETTES.items():
            bg=luminance(palette['background'])
            for role,color in palette.items():
                if role=='background':continue
                fg=luminance(color)
                self.assertGreaterEqual((max(bg,fg)+.05)/(min(bg,fg)+.05),4.5,(name,role))
        contrast=style_for('contrast',STYLE)
        self.assertTrue(contrast.get_attrs_for_style_str('class:prompt').bold)
        self.assertFalse(contrast.get_attrs_for_style_str('class:error').color)

    def test_no_color_dumb_and_noninteractive_precede_theme(self):
        for env,interactive in (({'NO_COLOR':''},True),({'TERM':'dumb'},True),({'TERM':'xterm-256color'},False)):
            with patch.dict(os.environ,env,clear=True),patch.object(Terminal,'_configure_editor'):
                ui=Terminal(Redactor(),interactive=interactive)
                ui.apply_theme('dark')
                self.assertFalse(ui.color)
                self.assertEqual(ui.theme,'dark')
                with patch('builtins.print') as output,patch('codine.terminal.print_formatted_text') as colored:
                    ui.line('Error: denied','error')
                    colored.assert_not_called();output.assert_called_once()

    def test_command_persists_and_switches_only_ui_preference(self):
        ui=Mock()
        with patch('codine.themes.THEME_PATH',self.path):
            command('light',self.project,ui)
            ui.apply_theme.assert_called_once_with('light')
            self.assertEqual(load(self.project,self.path),'light')
        self.assertEqual(list(self.project.iterdir()),[])

    def test_actual_cli_theme_command_is_offline_and_remains_plain(self):
        package=Path(__file__).resolve().parents[1]
        home=self.root/'home';home.mkdir()
        source='import sys;sys.path.insert(0,'+repr(str(package))+');from codine.launcher import main;raise SystemExit(main())'
        env={'PATH':os.environ.get('PATH',''),'HOME':str(home),'CODINE_HOME':str(home/'state'),
             'NVIDIA_API_KEY':'fake-no-request','NO_COLOR':'1','TERM':'dumb'}
        args=[sys.executable,'-I','-c',source,'--project',str(self.project),'--provider','nvidia',
              '--prompt','/theme contrast']
        result=subprocess.run(args,env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('Theme: contrast',result.stdout)
        self.assertNotIn('\x1b[',result.stdout)
        self.assertEqual(json.loads((home/'.config/codine/theme.json').read_text())['theme'],'contrast')

    def test_real_pty_theme_switches_on_ansi_and_vt100(self):
        from tests.test_terminal_integration import PTY
        package=Path(__file__).resolve().parents[1]
        driver='''
import socket

def deny(*args,**kwargs): raise AssertionError('No inference in theme checks')
socket.socket.connect=deny
from codine.cli import main
raise SystemExit(main(['--provider','nvidia']))
'''
        for term in ('xterm-256color','vt100'):
            with self.subTest(term=term):
                terminal=PTY([sys.executable,'-c',driver],self.project,
                             env={'TERM':term,'NVIDIA_API_KEY':'fake-no-request',
                                  'CODINE_HOME':str(self.root/'state')},columns=52)
                try:
                    terminal.until('›'.encode())
                    for name in NAMES:
                        start=len(terminal.output)
                        terminal.send(('/theme '+name+'\r').encode())
                        terminal.until(('Theme: '+name).encode(),start=start)
                    terminal.send(b'exit\r');terminal.until(b'Goodbye!')
                    self.assertEqual(terminal.process.wait(timeout=5),0)
                    saved=Path(terminal.user_home.name)/'.config/codine/theme.json'
                    self.assertEqual(json.loads(saved.read_text())['theme'],'contrast')
                finally:terminal.close()

    def test_broad_project_cannot_model_write_global_theme_or_case_alias(self):
        from codine.workspace import Workspace
        preference=self.root/'.config/codine/theme.json'
        with patch('codine.themes.THEME_PATH',preference):
            workspace=Workspace(self.root,Mock(return_value=True),Mock())
        for path in ('.config/codine/theme.json','.CONFIG/CODINE/THEME.JSON'):
            with self.subTest(path=path),self.assertRaises(ValueError):
                workspace.resolve(path,operation='write')
        self.assertFalse(preference.exists())
