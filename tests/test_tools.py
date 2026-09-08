import argparse, contextlib, io, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ai_stack'))
import tools  # noqa: E402


class Ctx7CmdTests(unittest.TestCase):
    def test_missing_binary_returns_none(self):
        with patch('tools.shutil.which', return_value=None):
            self.assertIsNone(tools.ctx7_cmd())

    def test_present_binary_returns_argv_list(self):
        with patch('tools.shutil.which', return_value='/usr/local/bin/ctx7'):
            self.assertEqual(tools.ctx7_cmd(), ['ctx7'])


class GraphEnvTests(unittest.TestCase):
    def test_graph_env_sets_output_dir_and_keeps_ambient(self):
        state = Path('/tmp/graph-state')
        env = tools.graph_env(state)
        self.assertEqual(env['GRAPHIFY_OUT'], str(state / 'graphify'))
        self.assertIn('PATH', env)  # ambient environment preserved


class _GitSandbox(unittest.TestCase):
    """A throwaway git repo with HOME/XDG redirected and AI_GATE/AI_TASK_DIR stripped."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='tools-test-')
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.repo = self.home / 'repo'
        self.repo.mkdir()
        base_env = {k: v for k, v in os.environ.items() if k not in ('AI_GATE', 'AI_TASK_DIR')}
        self.env = dict(base_env, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / 'config'))
        for args in (('init', '-q'), ('config', 'user.name', 'Test'),
                     ('config', 'user.email', 'test@example.com')):
            self.git(*args)

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, env=self.env, text=True).strip()

    def write(self, rel, text=''):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


class DetectDeployTests(_GitSandbox):
    def test_every_bucket_is_classified_from_tracked_files_only(self):
        self.write('Dockerfile', 'FROM scratch\n')
        self.write('sub/Dockerfile', 'FROM scratch\n')
        self.write('docker-compose.yml', 'services: {}\n')
        self.write('.github/workflows/ci.yml', 'name: ci\n')
        self.write('.github/workflows/deploy.yaml', 'name: deploy\n')
        self.write('Procfile', 'web: run\n')
        self.write('fly.toml', 'app = "x"\n')
        self.write('vercel.json', '{}\n')
        self.write('infra/main.tf', 'resource "null_resource" "x" {}\n')
        self.write('k8s/deploy.yaml', 'kind: Deployment\n')
        self.write('scripts/deploy.sh', '#!/bin/sh\necho go\n')
        self.write('deploy_prod.py', 'print("go")\n')
        self.write('Makefile', 'deploy:\n\techo shipping\n')
        self.write('README.md', '# App\n\n## Deploy\n\nrun the script\n')
        self.write('docs/guide.md', '# Guide\n\n### Deployment\n\nsteps\n')
        self.write('src/app.py', 'x = 1\n')  # noise, no bucket
        self.git('add', '.')
        self.write('untracked/Dockerfile', 'FROM scratch\n')  # never `git add`ed

        found = tools.detect_deploy(self.repo)
        self.assertCountEqual(found['docker'], ['Dockerfile', 'sub/Dockerfile', 'docker-compose.yml'])
        self.assertEqual(found['ci_cd'], ['.github/workflows/ci.yml', '.github/workflows/deploy.yaml'])
        self.assertCountEqual(found['paas'], ['Procfile', 'fly.toml', 'vercel.json'])
        self.assertEqual(found['infra'], ['infra/main.tf', 'k8s/deploy.yaml'])
        self.assertCountEqual(found['scripts'], ['Makefile', 'deploy_prod.py', 'scripts/deploy.sh'])
        self.assertCountEqual(found['docs'], ['README.md', 'docs/guide.md'])
        for bucket in found.values():
            self.assertNotIn('untracked/Dockerfile', bucket)

    def test_makefile_without_a_deploy_target_is_not_a_deploy_script(self):
        self.write('Makefile', 'build:\n\techo build\n')
        self.git('add', '.')
        self.assertEqual(tools.detect_deploy(self.repo)['scripts'], [])

    def test_readme_without_a_deploy_heading_is_not_docs(self):
        self.write('README.md', '# Project\n\nJust a description, nothing deployable.\n')
        self.git('add', '.')
        self.assertEqual(tools.detect_deploy(self.repo)['docs'], [])

    def test_untracked_dockerfile_is_ignored(self):
        self.write('Dockerfile', 'FROM scratch\n')  # created but not staged
        self.assertEqual(tools.detect_deploy(self.repo)['docker'], [])

    def test_empty_repo_yields_all_empty_buckets(self):
        self.write('README.md', '# Project\n\nnothing deployable here\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial')
        self.assertEqual(
            tools.detect_deploy(self.repo),
            {'docker': [], 'ci_cd': [], 'paas': [], 'infra': [], 'scripts': [], 'docs': []})


class CmdDeployDispatcherTests(_GitSandbox):
    def test_cmd_deploy_prints_without_error(self):
        self.write('Dockerfile', 'FROM scratch\n')
        self.git('add', '.')
        old = os.getcwd()
        os.chdir(self.repo)  # cmd_deploy -> git_root() resolves from cwd
        self.addCleanup(os.chdir, old)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tools.cmd_deploy(argparse.Namespace())
        out = buf.getvalue()
        self.assertIn('Deployment detection', out)
        self.assertIn('Dockerfile', out)


class CmdFigmaDoctorTests(unittest.TestCase):
    def test_doctor_reports_missing_clients_without_touching_git_state(self):
        # doctor branch only shells out via shutil.which; both faked absent
        buf = io.StringIO()
        with patch('tools.shutil.which', return_value=None), contextlib.redirect_stdout(buf):
            tools.cmd_figma(argparse.Namespace(figma_cmd='doctor'))
        out = buf.getvalue()
        self.assertIn('claude missing', out)
        self.assertIn('codex missing', out)
        self.assertIn('Recommended remote:', out)


if __name__ == '__main__':
    unittest.main()
