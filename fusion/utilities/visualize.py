import os
import requests
import large_image
from fusion.utilities.utility import get_hubmap_url
from IPython.display import IFrame

# vizualise user's data
def vizualise_in_histomicUI(item_id: str):
    if not item_id:
        raise ValueError("please provide a valid item_id")
    else:
        try:
            ts =IFrame(f"https://athena.rc.ufl.edu/histomics#?image={item_id}", "100%", "800px")
        except Exception:
            raise
    return ts

def visualize_hubmap_wsi(hubmap_id,overwrite=False):
    url = get_hubmap_url(hubmap_id)
    print("URL:", url)
    filename = os.path.basename(url)


    if os.path.exists(filename) and not overwrite:
        print(f"{filename} already exists. Skipping download.")
    else:
        print(f"Downloading {filename} ...")
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(filename, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
    
    ts = large_image.open(filename)

    return ts

