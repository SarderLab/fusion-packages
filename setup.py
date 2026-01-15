from setuptools import setup
from setuptools.command.develop import develop
from setuptools.command.install import install
import subprocess
import sys

def run_download():
    subprocess.call([sys.executable, '-m', 'fusion.download_notebooks'])

class PostDevelopCommand(develop):
    def run(self):
        develop.run(self)
        run_download()

class PostInstallCommand(install):
    def run(self):
        install.run(self)
        run_download()

setup(
    cmdclass={
        'develop': PostDevelopCommand,
        'install': PostInstallCommand,
    }
)
