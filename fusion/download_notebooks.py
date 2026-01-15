import urllib.request
from pathlib import Path

def download_demo_notebooks():
    """Download demo notebooks to current directory"""
    notebooks_dir = Path.cwd() / "fusion_demo_notebooks"
    notebooks_dir.mkdir(exist_ok=True)
    
    # GitHub raw URLs
    base_url = "https://raw.githubusercontent.com/SarderLab/fusion-packages/main/fusion/"
    notebooks = [
        "fusion_demo.ipynb"
    ]
    
    print("Downloading fusion demo notebooks")
    for notebook in notebooks:
        url = base_url + notebook
        save_path = notebooks_dir / notebook
        try:
            urllib.request.urlretrieve(url, save_path)
            print(f"{notebook}")
        except Exception as e:
            print(f"{notebook}: {e}")
    
    print(f"\n Notebooks saved to: {notebooks_dir.absolute()}")

if __name__ == "__main__":
    download_demo_notebooks()
