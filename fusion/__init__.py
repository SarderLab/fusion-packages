import os
import warnings

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
