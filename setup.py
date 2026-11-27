"""Bundle evaluation fixtures from their single source in the checkout."""
from pathlib import Path
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithFixtures(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / 'codine' / 'resources' / 'evals'
        target.mkdir(parents=True, exist_ok=True)
        for source in (Path(__file__).parent / 'evals').glob('*.json'):
            self.copy_file(str(source), str(target / source.name))


setup(cmdclass={'build_py': BuildWithFixtures})
