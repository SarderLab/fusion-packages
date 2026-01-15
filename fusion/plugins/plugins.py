from fusion.utilities.utility import get_hubmap_url, upload_to_athena


def get_multi_compartment_segmentation(gc, user_name, hubmapid=None, file_path=None):

    if hubmapid and file_path:
        raise ValueError("Please provide either hubmapid or the file path in you local system.")

    # push it to Athena if it's not already there and get item id 
    if hubmapid:
        file_source = get_hubmap_url(hubmapid)
    if file_path:
        file_source = file_path
    
    item_id, folder_id = upload_to_athena(gc, file_source, user_name)

    file = gc.get(f'item/{item_id}')
    file_id = file.get("largeImage").get("fileId")
    
    try:
        # Get docker images list to find the run endpoint
        response = gc.get('slicer_cli_web/docker_image')
        
        # Navigate to the run endpoint
        run_endpoint = response["sarderlab/compreps"]["MultiC"]["MultiCompartmentSegment"]["run"]
        
        params = {
            'input_file': file_id,
            'modelfile': '648c7a9231b16f5747b20404',
            'girderApiUrl': 'https://athena.rc.ufl.edu/api/v1', 
            'girderToken': gc.token  
            
        }
        r = gc.post(run_endpoint, parameters=params)
        
        # Create response dictionary
        response_summary = {
            'job_id': r['_id'],
            'plugin_name': r['_original_name'],
            'raw': r
        }
        return response_summary
        
    except Exception as e:
        print(f"Error: {e}")
        return None

def get_job_status(gc, job_id):
    """
    Get the status of a job by its ID.
    """
    # Status mapping
    status_map = {
        3: "completed",
        2: "in progress",
        0: "failed, inactive",
        4: "failed, error",
        5: "failed, canceled"
    }

    job = gc.get(f'job/{job_id}')
    # Get and map status
    status_code = job.get('status')
    status = status_map.get(status_code, 'unknown')
    
    return status