import json
import os
import pandas as pd
import requests

KEY_COLS = 'data.contrast nuclei,data.contrast eosinophilic,data.condition,item.name,item.id,annotation.id,annotationelement.id'

# Check if file already exists in folder
def file_exists(gc, folder_id, filename):
    items = gc.get('/item', parameters={'folderId': folder_id})
    for item in items:
        if item.get('name') == filename:
            return item.get('_id')
    return None

# uploads the files to Athena without downloading it to the local workspace
def upload_to_athena(gc, file_source, user_name, folder_id=None, athena_url='https://athena.rc.ufl.edu/api/v1'):
    """
    Upload a file to Athena from local path or URL.
    Parameters:
    - file_source: Local file path or URL
    - folder_id: Athena folder ID (optional, takes priority if provided)
    - athena_url: Athena API base URL
    - user_name: username
    - auth_pass: Authentication password
    If folder_id is provided, it will be used. 
    """
    
    # If folder_id is not provided, search for user
    if not folder_id:
        if user_name:
            print(f"Searching for username: {user_name}")
            users = gc.get('/user', parameters={'text': user_name})
        if not users:
            raise ValueError(f"No user found matching the provided search criteria")
        user_id = users[0]['_id']
        user_info = users[0]
        print(f"Using user: {user_info.get('login', 'N/A')} (ID: {user_id})")
        # Create folder in user's root dir
        folder_name = f"uploads_{file_source.split('/')[-1].split('.')[0]}"
        
        try:
            print(f"Creating folder: {folder_name}")
            folder = gc.post('/folder', parameters={
                'parentType': 'user',
                'parentId': user_id,
                'name': folder_name
            })
            folder_id = folder['_id']
            print(f"Folder created: {folder_id}")
            
        except Exception as e:
            # Check if folder already exists
            if "already exists" in str(e):
                print(f"Folder '{folder_name}' already exists. Using existing folder.")
                # Get the existing folder
                folders = gc.get('/folder', parameters={
                    'parentType': 'user',
                    'parentId': user_id,
                    'name': folder_name
                })
                folder_id = folders[0]['_id']
                print(f"Using folder: {folder_id}")
            else:
                raise
    else:
        print(f"Using provided folder_id: {folder_id}")
        
    # Determine if source is URL or local file
    is_url = file_source.startswith('http://') or file_source.startswith('https://')
    if is_url:
        # Upload from URL
        print(f"Uploading from URL: {file_source}")
        
        # Get file info
        head_response = requests.head(file_source)
        file_size = int(head_response.headers.get('Content-Length', 0))
        filename = file_source.split('/')[-1]

        # check if the file exists
        item_id = file_exists(gc, folder_id, filename)
        if item_id:
            print(f"File '{filename}' : id {item_id} already exists in folder. Skipping upload.")
            return item_id, folder_id
        
        # Initialize upload
        r = gc.post('/file', parameters={
            'parentType': 'folder',
            'parentId': folder_id,
            'name': filename,
            'size': file_size
        })
        upload_id = r['_id']
        print(f"Upload ID: {upload_id}")
        
        # Stream from URL and upload
        chunk_size = 64 * 1024 * 1024  
        offset = 0
        with requests.get(file_source, stream=True) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    print(f"Uploading chunk at offset {offset} ({offset/file_size*100:.1f}%)")
                    r1 = gc.post('/file/chunk', parameters={
                        'uploadId': upload_id,
                        'offset': offset
                    }, data=chunk)
                    offset += len(chunk)
        print("itemid and folder id :")
        print(item_id, folder_id)
    else:
        # Upload from local file
        print(f"Uploading from local file: {file_source}")
        if not os.path.exists(file_source):
            raise FileNotFoundError(f"File not found: {file_source}")
        filename = os.path.basename(file_source)
        file_size = os.path.getsize(file_source)

        # check if the file exists
        item_id = file_exists(gc, folder_id, filename)
        if item_id:
            print(f"File '{filename}' : id {item_id} already exists in folder. Skipping upload.")
            return item_id, folder_id
        
        # Initialize upload
        r = gc.post('/file', parameters={
            'parentType': 'folder',
            'parentId': folder_id,
            'name': filename,
            'size': file_size
        })
        with open('upload_initialise_response.json', 'w') as file:
            json.dump(r, file)
        upload_id = r['_id']
        print(f"Upload ID: {upload_id}")
        
        # Upload in chunks
        chunk_size = 64 * 1024 * 1024  
        offset = 0
        with open(file_source, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                print(f"Uploading chunk at offset {offset} ({offset/file_size*100:.1f}%)")
                r1 = gc.post('/file/chunk', parameters={
                    'uploadId': upload_id,
                    'offset': offset
                }, data=chunk)
                offset += len(chunk)
                
    item_id = r1["itemId"]
    print("Upload completed successfully!")
    print("item_id, folder_id :", item_id, folder_id)
    
    return item_id, folder_id

# get url of the image from hubmapid
def get_hubmap_url(hubmap_id):
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
    json.dump(r.json(), open("response.json", "w"), indent=4)
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
    return url

def get_patient_id(gc, path):
    r = gc.get("resource/lookup", parameters={'path': path})
    return r['meta']['Patient']

def plot_json_to_df(json_obj):
    sorted_cols = sorted(json_obj['columns'], key=lambda x: x['index'])
    col_names = [col['title'] for col in sorted_cols]
    return pd.DataFrame(json_obj['data'], columns=col_names)

def get_available_annotations(gc, path):
    r = gc.get("resource/lookup", parameters={'path': path})
    uuid = r['_id']
    annotations = gc.get(f"annotation", parameters={'itemId': uuid})
    print(f"Found {len(annotations)} annotation(s) for item: {r['name']}")
    print("-" * 80)
    annotation_dict = {}
    for i, ann in enumerate(annotations, 1):
        annotation_name = ann.get('annotation', {}).get('name', 'Unnamed')
        annotation_id = ann['_id']
        element_count = ann.get('_elementCount', 0)
        print(f"{i}. Name: '{annotation_name}' | ID: {annotation_id} | Elements: {element_count}")
        annotation_dict[annotation_name] = annotation_id
    print("-" * 80)
    return annotation_dict


def get_available_columns(gc, path, annotation_name):
    r = gc.get("resource/lookup", parameters={'path': path})
    uuid = r['_id']
    annotations = gc.get("annotation", parameters={'itemId': uuid})
    matching_annotations = [
        ann
        for ann in annotations
        if ann.get('annotation', {}).get('name') == annotation_name
    ]

    columns = list(matching_annotations[0].keys()) if matching_annotations else []
    print("-" * 80)
    print(f"Available columns for annotation '{annotation_name}' ({len(columns)}):")
    for i, col in enumerate(columns, 1):
        print(f"{i}. {col}")
    print("-" * 80)
    return columns


def get_annotation_data(gc, path, annotation_name, columns=KEY_COLS):
    
    r = gc.get("resource/lookup", parameters={'path': path})
    uuid = r['_id']
    annotations = gc.get(f"annotation", parameters={'itemId': uuid})
    annotation_ids = [
        ann['_id']
        for ann in annotations
        if ann.get('annotation', {}).get('name') == annotation_name
    ]
    print(f"Found {len(annotation_ids)} annotation(s) with name '{annotation_name}': {annotation_ids}")
    if annotation_ids:
        # Use the list of IDs in your plot data request
        data = gc.post(f"annotation/item/{uuid}/plot/data", parameters={
            'adjacentItems': 'true',
            'keys': ','.join(columns),
            'annotations': json.dumps(annotation_ids)
        })
        df = plot_json_to_df(data)
    else:
        print(f"No annotations found with name: {annotation_name}")
        df = None
    
    return df