"""The parts of the host side, one at a time."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hostexec_host.approvals import APPROVED, EXPIRED, GONE, Approvals  # noqa: E402
from hostexec_host.display import command_line, shell_word, visible  # noqa: E402
from hostexec_host.request import ExecRequest, Invalid, map_path, parse_exec_request, parse_mapping  # noqa: E402
from hostexec_host.run import Masker, Run  # noqa: E402


class ParseExecRequestTest(unittest.TestCase):
    def ok(self, body):
        return parse_exec_request(json.dumps(body).encode())

    def bad(self, text):
        with self.assertRaises(Invalid) as e:
            parse_exec_request(text.encode())
        return str(e.exception)

    def test_takes_argv_cwd_and_env(self):
        self.assertEqual(self.ok({'argv': ['ls', '-l'], 'cwd': '/tmp', 'env': {'A': '1'}}),
                         ExecRequest(argv=['ls', '-l'], cwd='/tmp', requested_cwd='/tmp', env={'A': '1'}))
        self.assertEqual(self.ok({'argv': ['ls'], 'cwd': '/'}).env, {})

    def test_refuses_anything_else(self):
        for text in ['not json', '[]', '{"argv":[],"cwd":"/"}', '{"argv":[""],"cwd":"/"}',
                     '{"argv":["a\\u0000b"],"cwd":"/"}', '{"argv":["\\ud800"],"cwd":"/"}',
                     '{"argv":["ls"],"cwd":"relative"}', '{"argv":["ls"],"cwd":"/","shell":true}',
                     '{"argv":["ls"],"cwd":"/","env":[]}',
                     '{"argv":["ls"],"cwd":"/","env":{"A-B":"1"}}', '{"argv":["ls"],"cwd":"/","env":{"A\\n":"1"}}',
                     '{"argv":["ls"],"cwd":"/","env":{"A":1}}']:
            with self.subTest(text):
                self.bad(text)
        self.assertIn('duplicated', self.bad('{"argv":["ls"],"argv":["rm"],"cwd":"/"}'))
        self.assertIn('duplicated', self.bad('{"argv":["ls"],"cwd":"/","env":{"A":"1","A":"2"}}'))

    def test_translates_cwd_through_the_map(self):
        r = parse_exec_request(b'{"argv":["ls"],"cwd":"/workspace/sub"}', [('/workspace', '/home/u/repo')])
        self.assertEqual((r.cwd, r.requested_cwd), ('/home/u/repo/sub', '/workspace/sub'))


class MapPathTest(unittest.TestCase):
    MAP = [('/workspace', '/home/u/repo'), ('/workspace/deep', '/srv/deep')]

    def test_longest_prefix_wins_on_whole_components(self):
        self.assertEqual(map_path('/workspace', self.MAP), '/home/u/repo')
        self.assertEqual(map_path('/workspace/a/b', self.MAP), '/home/u/repo/a/b')
        self.assertEqual(map_path('/workspace/deep/x', self.MAP), '/srv/deep/x')
        self.assertEqual(map_path('/workspace2', self.MAP), '/workspace2')
        self.assertEqual(map_path('/etc', self.MAP), '/etc')

    def test_resolves_dotdot_and_slashes_before_mapping(self):
        self.assertEqual(map_path('/workspace/../etc', self.MAP), '/etc')
        self.assertEqual(map_path('/workspace/a/../../workspace/b', self.MAP), '/home/u/repo/b')
        self.assertEqual(map_path('//workspace//a/', self.MAP), '/home/u/repo/a')

    def test_parse_mapping(self):
        self.assertEqual(parse_mapping('/workspace/=/home/u/repo/'), ('/workspace', '/home/u/repo'))
        self.assertEqual(parse_mapping('/=/'), ('/', '/'))
        for spec in ['/workspace', 'rel=/x', '/x=rel']:
            with self.subTest(spec), self.assertRaises(Invalid):
                parse_mapping(spec)


class MaskerTest(unittest.TestCase):
    SECRET = '0123456789abcdef'

    def run_chunks(self, chunks):
        m = Masker([self.SECRET])
        return b''.join(m.push(c.encode()) for c in chunks).decode() + m.end().decode()

    def test_masks_whole_and_split_secrets(self):
        self.assertEqual(self.run_chunks([f'a{self.SECRET}b']), 'a[masked by hostexec]b')
        self.assertEqual(self.run_chunks(['xx0123', '456789ab', 'cdefyy']), 'xx[masked by hostexec]yy')
        self.assertEqual(self.run_chunks(['0123456789abcde']), '0123456789abcde')

    def test_holds_back_only_what_could_start_a_secret(self):
        m = Masker([self.SECRET])
        self.assertEqual(m.push(b'tick 1\n'), b'tick 1\n')
        self.assertEqual(m.push(b'x 012'), b'x ')
        self.assertEqual(m.push(b'9'), b'0129')

    def test_passes_through_with_no_secrets(self):
        self.assertEqual(Masker([]).push(b'abc'), b'abc')


class RunTest(unittest.TestCase):
    def collect(self, argv, env=None, stop_after=None):
        out = {'stdout': b'', 'stderr': b''}
        done = threading.Event()
        result = {}

        def on_exit(code, sig):
            result.update(code=code, signal=sig)
            done.set()
        request = ExecRequest(argv=argv, cwd='/', requested_cwd='/', env=env or {})
        run = Run(request, ['SECRETSECRET'], lambda s, d: out.__setitem__(s, out[s] + d), on_exit)
        if stop_after is not None:
            time.sleep(stop_after)
            run.stop()
        self.assertTrue(done.wait(10))
        return result, out

    def test_streams_output_and_exit_code_with_only_path_home_and_what_was_asked(self):
        exit, out = self.collect(['sh', '-c', 'echo "$FOO $SECRET_ENV"; echo SECRETSECRET >&2; exit 3'],
                                 {'FOO': 'bar'})
        self.assertEqual(exit, {'code': 3, 'signal': None})
        self.assertEqual(out, {'stdout': b'bar \n', 'stderr': b'[masked by hostexec]\n'})

    def test_a_missing_command_is_127(self):
        exit, out = self.collect(['/nonexistent/cmd'])
        self.assertEqual(exit['code'], 127)
        self.assertIn(b'No such file', out['stderr'])

    def test_stop_stops_the_process_group(self):
        started = time.monotonic()
        exit, _ = self.collect(['sh', '-c', 'sleep 30 & sleep 30; wait'], stop_after=0.2)
        self.assertEqual(exit['signal'], 'SIGTERM')
        self.assertLess(time.monotonic() - started, 4)

    def test_waits_for_what_the_command_left_writing(self):
        exit, out = self.collect(['sh', '-c', '(sleep 0.3; echo late) & echo early'])
        self.assertEqual(exit, {'code': 0, 'signal': None})
        self.assertEqual(out['stdout'], b'early\nlate\n')


class ApprovalsTest(unittest.TestCase):
    EXEC = ExecRequest(argv=['true'], cwd='/', requested_cwd='/', env={})

    def hold(self, a):
        got = []
        done = threading.Event()
        held, withdraw = a.hold(self.EXEC, lambda d: (got.append(d), done.set()))
        return held, withdraw, lambda: (done.wait(5), got)[1]

    def test_decide_expire_withdraw(self):
        a = Approvals(0.1)
        held, _, decision = self.hold(a)
        self.assertEqual([h.id for h in a.list()], [held.id])
        self.assertTrue(a.decide(held.id, True))
        self.assertEqual(decision(), [APPROVED])
        self.assertFalse(a.decide(held.id, False))

        _, _, decision = self.hold(a)
        self.assertEqual(decision(), [EXPIRED])

        _, withdraw, decision = self.hold(Approvals())
        withdraw()
        withdraw()
        self.assertEqual(decision(), [GONE])
        self.assertEqual(a.list(), [])


class DisplayTest(unittest.TestCase):
    ARGV = ['ls', '-l', 'a b', "it's", 'x\ny', '\u202e', 'a\\b', 'tab\there', '\U000e0001', 'é']

    def test_shell_words_show_where_each_argument_ends(self):
        self.assertEqual(command_line(['ls', '-l', 'a b', "it's", 'x\ny', '\u202e']),
                         "ls -l 'a b' $'it\\'s' $'x\\ny' $'\\u202e'")
        self.assertEqual(shell_word('a\\b'), "'a\\b'")
        self.assertEqual(shell_word('a\n'), "$'a\\n'")
        self.assertEqual(visible('a\x1b[31m\\'), 'a\\u{1b}[31m\\\\')

    @unittest.skipUnless(shutil.which('node'), 'needs node')
    def test_the_browser_shows_the_same(self):
        script = f'''
import {{ commandLine, visible }} from {json.dumps((ROOT / 'web/app.js').as_uri())};
const argv = JSON.parse(process.argv[1]);
console.log(JSON.stringify([commandLine(argv), argv.map(visible)]));
'''
        out = subprocess.run(['node', '--input-type=module', '-e', script, json.dumps(self.ARGV)],
                             check=True, capture_output=True, text=True).stdout
        self.assertEqual(json.loads(out), [command_line(self.ARGV), [visible(a) for a in self.ARGV]])



class ServiceTest(unittest.TestCase):
    def service(self, *args, path='/nix/bin:/usr/bin'):
        return subprocess.run([sys.executable, str(ROOT / 'hostexec'), 'service', *args],
                              env={'PATH': path}, capture_output=True, text=True)

    def test_runs_serve_with_its_options_and_this_path(self):
        r = self.service('--listen', '127.0.0.1', '--map', '/a b=/x%y$z"q', path='/p%1:/$q')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f'ExecStart="{sys.executable}" "{ROOT / "hostexec"}" "serve" "--listen" "127.0.0.1" '
                      '"--map" "/a b=/x%%y$$z\\"q"\n', r.stdout)
        self.assertIn('Environment="PATH=/p%%1:/$q"\n', r.stdout)

    def test_refuses_what_serve_would(self):
        self.assertEqual(self.service('--map', 'rel').returncode, 2)
        self.assertEqual(self.service('--port', '0').returncode, 2)
        self.assertEqual(self.service('--force').returncode, 2)

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'needs systemd-analyze')
    def test_systemd_reads_it(self):
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / 'hostexec.service'
            unit.write_text(self.service().stdout)
            r = subprocess.run(['systemd-analyze', '--user', 'verify', str(unit)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)



class SnippetTest(unittest.TestCase):
    def snippet(self, *args, home, config=None):
        env = {'HOME': home, 'PATH': '/usr/bin:/bin', **({'XDG_CONFIG_HOME': config} if config else {})}
        return subprocess.run([sys.executable, str(ROOT / 'hostexec'), 'snippet', *args], env=env,
                              capture_output=True, text=True, check=True).stdout

    def test_the_same_for_every_workspace_with_home_as_tilde(self):
        out = self.snippet(home=str(ROOT.parent))
        self.assertIn('X-Hostexec-Map = "/workspace=${HOSTEXEC_WORKSPACE}"', out)
        self.assertIn('ca = "~/.config/hostexec/ca.pem"', out)
        self.assertIn(f'"/usr/bin/hostexec" = "~/{ROOT.name}/client/hostexec"', out)
        out = self.snippet('--secrets', home=str(ROOT.parent))
        self.assertIn('HOSTEXEC_TOKEN = { command = "cat ~/.config/hostexec/api-token" }', out)
        self.assertIn('HOSTEXEC_WORKSPACE = { command = \'printf %s "$CLAUDE_DEVCONTAINER_WORKSPACE"\' }', out)

    def test_keeps_paths_outside_home(self):
        out = self.snippet(home='/nonexistent/home', config='/etc/xdg')
        self.assertIn('ca = "/etc/xdg/hostexec/ca.pem"', out)
        self.assertIn(f'"/usr/bin/hostexec" = "{ROOT}/client/hostexec"', out)

    def test_takes_no_directory(self):
        r = subprocess.run([sys.executable, str(ROOT / 'hostexec'), 'snippet', '/srv/project'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

if __name__ == '__main__':
    unittest.main()
