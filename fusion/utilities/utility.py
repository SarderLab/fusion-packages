import json
import pandas as pd

KEY_COLS = 'data.contrast nuclei,data.contrast eosinophilic,data.condition,item.name,item.id,annotation.id,annotationelement.id'

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