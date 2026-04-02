
from fusion.utilities.utility import download_to_fusion_backend
from IPython.display import IFrame
import os


def visualize_wsi(gc, hubmap_id=None, item_id=None, user=None, dir_path=None):
    """
    Visualize WSI data in HistomicsUI.

    Args:
        gc: Girder client instance.
        hubmap_id (str): The HubMAP ID to fetch and visualize data for.
        item_id (str): Direct item_id from fusion backend to visualize in HistomicsUI.
        user (str): Athena username (required if using hubmap_id or dir_path).
        dir_path (str): Local directory path previously pushed to Fusion; looks up
                        the corresponding Athena folder by directory name and lets
                        the user pick a WSI item to visualize.

    Returns:
        IFrame with HistomicsUI viewer for selected file
    """

    if not item_id and not hubmap_id and not dir_path:
        if user:
            return IFrame("https://fusionpub.rc.ufl.edu/histomics", "100%", "800px")
        else:
            raise ValueError("Please provide a user parameter.")
    
    # Case 1: dir_path provided - look up folder by directory name and pick a WSI
    if dir_path:
        if not user:
            raise ValueError("Please provide a user parameter when using dir_path.")

        folder_name = os.path.basename(os.path.normpath(dir_path))
        print(f"Looking up Athena folder '{folder_name}' for user '{user}'...")

        users = gc.get('/user', parameters={'text': user})
        if not users:
            raise ValueError(f"No user found matching '{user}'")
        user_id = users[0]['_id']

        folders = gc.get('/folder', parameters={'parentType': 'user', 'parentId': user_id})
        folder = next((f for f in folders if f.get('name') == folder_name), None)

        if not folder:
            raise ValueError(f"No Athena folder named '{folder_name}' found. Make sure you have pushed the directory first.")

        folder_id = folder['_id']
        print(f"Found folder '{folder_name}' (ID: {folder_id})")

        items = gc.get('/item', parameters={'folderId': folder_id})

        wsi_files = [
            {'file': item.get('name', ''), 'item_id': item.get('_id', '')}
            for item in items
            if item.get('name', '').endswith(('.ome.tiff', '.ome.tif', '.tiff', '.tif'))
        ]

        if not wsi_files:
            print("No WSI files found in the folder.")
            return None

        print(f"\n{'=' * 80}")
        print("AVAILABLE WSI FILES")
        print(f"{'=' * 80}")
        for idx, f in enumerate(wsi_files, 1):
            print(f"{idx}. {f['file']}  (item_id: {f['item_id']})")
        print(f"{'=' * 80}")

        if len(wsi_files) == 1:
            print("\nOnly one file available. Auto-selecting...")
            selected = wsi_files[0]
        else:
            while True:
                try:
                    choice = input(f"\nSelect a file to visualize (1-{len(wsi_files)}): ").strip()
                    idx = int(choice) - 1
                    if 0 <= idx < len(wsi_files):
                        selected = wsi_files[idx]
                        break
                    else:
                        print(f"Please enter a number between 1 and {len(wsi_files)}")
                except ValueError:
                    print("Please enter a valid number")
                except KeyboardInterrupt:
                    print("\nVisualization cancelled.")
                    return None

        print(f"\nOpening '{selected['file']}' in HistomicsUI...")
        return IFrame(
            f"https://fusionpub.rc.ufl.edu//histomics#?image={selected['item_id']}",
            width="100%",
            height="800px"
        )

    # Case 2: item_id provided - direct visualization
    if item_id:
        print("Visualizing item in HistomicUI...")
        try:
            return IFrame(
                f"https://fusionpub.rc.ufl.edu//histomics#?image={item_id}", 
                width="100%", 
                height="800px"
            )
        except Exception as e:
            raise ValueError(f"Failed to visualize item_id: {e}")
    
    # Case 2: hubmap_id provided - download to Athena and visualize
    if not hubmap_id:
        raise ValueError("Please provide either a hubmap_id or an item_id")
    
    if not user:
        raise ValueError("Please provide a user parameter.")
    
    print(f"Fetching and uploading data for HubMAP ID: {hubmap_id}")
    
    # Download histology files to fusion_backend
    upload_result = download_to_fusion_backend(
        gc=gc,
        hubmap_id=hubmap_id,
        user=user,
        histology=True,
        temp_download=True
    )
    
    if 'error' in upload_result:
        print(f"Error: {upload_result['error']}")
        return None
    
    # Filter for WSI files (OME-TIFF files)
    folder_id = upload_result.get('folder_id')
    
    if not folder_id:
        print("\nNo folder_id found in upload result.")
        return None
    
    # Get contents of the folder
    items = gc.get('/item', parameters={'folderId': folder_id})
    print(f"\nCurrent contents ({len(items)} item(s)):")
    
    # Filter for WSI files based on file extensions
    wsi_files = []
    for item in items:
        item_name = item.get('name', '')
        item_id = item.get('_id', '')
        
        if item_name.endswith(('.ome.tiff', '.ome.tif', '.tiff', '.tif')):
            wsi_files.append({
                'file': item_name,
                'item_id': item_id,
                'folder_id': folder_id
            })
    
    if not wsi_files:
        print("\nNo WSI files available for visualization.")
        return None
    
    # Display available files
    print(f"\n{'=' * 80}")
    print("AVAILABLE WSI FILES IN ATHENA")
    print(f"{'=' * 80}")
    for idx, file_info in enumerate(wsi_files, 1):
        print(f"{idx}. {file_info['file']}")
        print(f"   Item ID: {file_info['item_id']}")
    print(f"{'=' * 80}")
    
    # Ask user to select a file
    if len(wsi_files) == 1:
        print("\nOnly one file available. Auto-selecting...")
        selected_idx = 0
    else:
        while True:
            try:
                choice = input(f"\nSelect a file to visualize (1-{len(wsi_files)}): ").strip()
                selected_idx = int(choice) - 1
                if 0 <= selected_idx < len(wsi_files):
                    break
                else:
                    print(f"Please enter a number between 1 and {len(wsi_files)}")
            except ValueError:
                print("Please enter a valid number")
            except KeyboardInterrupt:
                print("\nVisualization cancelled.")
                return None
    
    selected_file = wsi_files[selected_idx]
    print(f"\nSelected: {selected_file['file']}")
    print(f"Item ID: {selected_file['item_id']}")
    
    # Visualize in HistomicUI
    try:
        print(f"\n{'=' * 80}")
        print("OPENING IN HISTOMICUI")
        print(f"{'=' * 80}")
        print(f"Filename:  {selected_file['file']}")
        print(f"Item ID:   {selected_file['item_id']}")
        print(f"Folder ID: {selected_file['folder_id']}")
        print(f"{'=' * 80}")
        
        return IFrame(
            f"https://fusionpub.rc.ufl.edu//histomics#?image={item_id}", 
            width="100%", 
            height="800px"
        )
        
    except Exception as e:
        print(f"\nFailed to open visualization: {e}")
        return None
