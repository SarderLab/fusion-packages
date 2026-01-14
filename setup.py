from setuptools import setup
from setuptools.command.install import install
import subprocess
import sys

class PostInstallCommand(install):
    def run(self):
        install.run(self)
        # Run the notebook download after installation
        subprocess.call([sys.executable, '-m', 'fusion.download_notebooks'])

setup(
    cmdclass={'install': PostInstallCommand}
)
