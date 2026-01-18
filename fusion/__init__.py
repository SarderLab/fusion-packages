import os
import warnings
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("fusion")
except PackageNotFoundError:
    __version__ = "unknown"


def configure_jupyter_environment():
    
    # 1. Environment variable configuration
    if "GDAL_PAM_ENABLED" not in os.environ:
        os.environ["GDAL_PAM_ENABLED"] = "no"
    
    # 2. Jupyter proxy configuration
    try:
        from IPython import get_ipython
        if get_ipython() is not None:  
            from large_image.tilesource.jupyter import IPyLeafletMixin
            IPyLeafletMixin.JUPYTER_PROXY = "/passthrough/proxy/"
    except ImportError:
        # Not in Jupyter environment, skip proxy setup
        pass
    
    warnings.filterwarnings('ignore', category=FutureWarning)

configure_jupyter_environment()

from .plugins import *
from .utilities import *

# check if the notebooks are downloaded
_notebooks_dir = Path.home() / "fusion_demo_notebooks"
_downloaded_marker = _notebooks_dir / ".downloaded"

if not _downloaded_marker.exists():
    try:
        from .download_notebooks import download_demo_notebooks
        download_demo_notebooks()
        _notebooks_dir.mkdir(parents=True, exist_ok=True)
        # Create marker file so we don't download again
        _downloaded_marker.touch()
    except Exception as e:
        print(f"Note: Could not auto-download demo notebooks: {e}")
        print("You can manually download them with: from fusion.download_notebooks import download_demo_notebooks; download_demo_notebooks()")
