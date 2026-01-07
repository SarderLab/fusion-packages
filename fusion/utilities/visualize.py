import os
import requests
import large_image

def visualize_hubmap_wsi(hubmap_id,overwrite=False):
    search_api = "https://search.api.hubmapconsortium.org/v3/search"
    ds_payload = {"_source": ["files"],
        "query": {
            "bool": {
            "must": [
                {"match": {"hubmap_id": hubmap_id}}
            ]
            }
        }
        }
    r = requests.post(search_api, json=ds_payload)
    r.raise_for_status()
    hits = r.json()["hits"]["hits"]
    if not hits:
        raise ValueError(f"No dataset found for HuBMAP ID {hubmap_id}")
    print(f'dataset found for HuBMAP ID {hubmap_id}')
    src = hits[0]["_source"]
    uuid = hits[0]["_id"]  
    print("uuid: ", uuid)
    
    # Find the ome.tiff file in the specific path
    omi_tiff_filename = None
    for file in src["files"]:
        if file["rel_path"].startswith("ometiff-pyramids/lab_processed/images/") and file["rel_path"].endswith(".ome.tif"):
            omi_tiff_filename = file["rel_path"].split("/")[-1]
            break
    
    if not omi_tiff_filename:
        raise ValueError("No ome.tiff file found in ometiff-pyramids/lab_processed/images/")
    
    url = f"https://assets.hubmapconsortium.org/{uuid}/ometiff-pyramids/lab_processed/images/{omi_tiff_filename}"
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