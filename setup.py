"""Build hook that places runtime data beside the installed Python package."""
from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithRuntimeData(build_py):
    def run(self):
        super().run()
        root=Path(__file__).parent
        bundle=Path(self.build_lib)/'ai_stack'/'_bundle'
        bundle.mkdir(parents=True,exist_ok=True)
        shutil.copy2(root/'VERSION',bundle/'VERSION')
        for name in ('skills','templates'):
            shutil.copytree(root/name,bundle/name,dirs_exist_ok=True)


setup(cmdclass={'build_py':BuildWithRuntimeData})
