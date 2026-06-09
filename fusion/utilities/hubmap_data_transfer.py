"""
transfer.py
-----------
Simple Globus Connect Personal (GCP) + HuBMAP transfer script.

Important HiPerGator behavior:
- GCP allowed path is forced to the user's current working directory.
- So run this script from the /blue/... or /orange/... folder you want GCP to access.

Workflow:
1. Ensure GCP, globus CLI, and hubmap-clt are available.
2. Add GCP and current Python environment bin directory to PATH.
3. Run GCP setup if needed.
4. Set GCP config-paths to current working directory.
5. Run Globus CLI login using: globus login --no-local-server
6. Check globus whoami and ask user to confirm account.
7. Run HuBMAP login using: hubmap-clt login --no-browser
8. Build or use manifest.
9. Stop previous GCP, start GCP again, wait until connected.
10. Run hubmap-clt transfer.
"""

import glob
import os
import shutil
import subprocess
import sys
import time
from typing import Union


GCP_TARBALL_URL = (
    "https://downloads.globus.org/globus-connect-personal"
    "/linux/stable/globusconnectpersonal-latest.tgz"
)


def _home() -> str:
    return os.path.expanduser("~")


def _add_to_path(path: str) -> None:
    current_path = os.environ.get("PATH", "")
    paths = current_path.split(os.pathsep) if current_path else []
    if path not in paths:
        os.environ["PATH"] = path + os.pathsep + current_path


def _command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kwargs)


def _ensure_python_cli(command: str, package: str) -> None:
    venv_bin = os.path.dirname(sys.executable)
    _add_to_path(venv_bin)

    if _command_exists(command):
        return

    print(f"[setup] {command} not found. Installing {package}...")
    _run([sys.executable, "-m", "pip", "install", package])

    if not _command_exists(command):
        raise RuntimeError(
            f"[setup] Installed {package}, but {command} is still not found. "
            f"Check PATH. Python executable: {sys.executable}"
        )


def _ensure_required_tools() -> None:
    _ensure_python_cli("globus", "globus-cli")
    _ensure_python_cli("hubmap-clt", "atlas-consortia-clt")

    print("[setup] Required CLIs available:")
    print(f"        globus:     {shutil.which('globus')}")
    print(f"        hubmap-clt: {shutil.which('hubmap-clt')}")


def _get_gcp_dir() -> Union[str, None]:
    matches = [
        path for path in glob.glob(os.path.join(_home(), "globusconnectpersonal-*"))
        if os.path.isdir(path)
    ]
    return matches[0] if matches else None


def _get_gcp_binary() -> Union[str, None]:
    gcp_dir = _get_gcp_dir()
    if gcp_dir is None:
        return None

    _add_to_path(gcp_dir)
    binary = os.path.join(gcp_dir, "globusconnectpersonal")
    return binary if os.path.isfile(binary) else None


def _is_gcp_installed() -> bool:
    return _get_gcp_binary() is not None


def _is_gcp_configured() -> bool:
    lta_dir = os.path.join(_home(), ".globusonline", "lta")
    if not os.path.isdir(lta_dir):
        return False

    for _, _, files in os.walk(lta_dir):
        if files:
            return True

    return False


def _download_file(url: str, output_path: str) -> None:
    if _command_exists("wget"):
        _run(["wget", "-q", "--show-progress", "-O", output_path, url])
        return

    if _command_exists("curl"):
        _run(["curl", "-L", url, "-o", output_path])
        return

    raise RuntimeError("[setup] Need either wget or curl to download GCP.")


def _install_gcp() -> None:
    home = _home()
    tarball = os.path.join(home, "globusconnectpersonal-latest.tgz")

    print("[gcp-setup] Downloading Globus Connect Personal...")
    _download_file(GCP_TARBALL_URL, tarball)

    print("[gcp-setup] Extracting Globus Connect Personal...")
    _run(["tar", "xzf", tarball, "-C", home])
    os.remove(tarball)

    gcp_dir = _get_gcp_dir()
    if gcp_dir is None:
        raise RuntimeError("[gcp-setup] GCP extraction finished, but directory was not found.")

    _add_to_path(gcp_dir)

    bashrc = os.path.join(home, ".bashrc")
    path_line = f'export PATH="{gcp_dir}:$PATH"'

    try:
        with open(bashrc, "r") as f:
            bashrc_text = f.read()
    except FileNotFoundError:
        bashrc_text = ""

    if gcp_dir not in bashrc_text:
        with open(bashrc, "a") as f:
            f.write(f"\n# Added by transfer.py\n{path_line}\n")
        print(f"[gcp-setup] Added GCP to ~/.bashrc: {path_line}")

    print(f"[gcp-setup] GCP installed at: {gcp_dir}")


def _setup_gcp_once() -> None:
    if not _is_gcp_installed():
        _install_gcp()

    if _is_gcp_configured():
        print("[gcp-setup] GCP already configured.")
        return

    binary = _get_gcp_binary()
    if binary is None:
        raise RuntimeError("[gcp-setup] GCP binary not found.")

    print("\n[gcp-setup] One-time GCP setup required.")
    print("[gcp-setup] A URL may appear. Open it, log in, paste the code, then enter endpoint name.")
    print("[gcp-setup] Example endpoint name: ashmit-hipergator-gcp\n")

    _run([binary, "-setup", "--no-gui"])
    print("[gcp-setup] GCP setup complete.\n")


def _set_gcp_allowed_path_to_cwd() -> None:
    """
    Set GCP allowed path to the current working directory.

    Original code relied on the default:
        ~,0,1

    This function overwrites ~/.globusonline/lta/config-paths with:
        <current working directory>,0,1

    So run from /blue/... or /orange/... before calling transfer().
    """
    cwd = os.getcwd()
    config_dir = os.path.join(_home(), ".globusonline", "lta")
    config_path = os.path.join(config_dir, "config-paths")

    if not os.path.isdir(config_dir):
        raise RuntimeError(
            f"[gcp-setup] GCP config directory not found: {config_dir}\n"
            "Run GCP setup first."
        )

    with open(config_path, "w") as f:
        f.write(f"{cwd},0,1\n")

    print("[gcp-setup] GCP allowed path set to current working directory:")
    print(f"            {cwd}")


def _globus_whoami() -> Union[str, None]:
    result = subprocess.run(
        ["globus", "whoami"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None

    account = result.stdout.strip()
    return account if account else None


def _ensure_globus_login() -> None:
    account = _globus_whoami()

    if account is None:
        print("\n[globus] Globus CLI login required.")
        print("[globus] Open the URL in your browser, then paste the auth code here.\n")
        _run(["globus", "login", "--no-local-server"])
        account = _globus_whoami()

    if account is None:
        raise RuntimeError("[globus] globus login finished, but globus whoami still failed.")

    print(f"[globus] Logged in as: {account}")
    answer = input("[globus] Is this the correct Globus account? [y/n]: ").strip().lower()

    if answer in {"y", "yes"}:
        return

    print("[globus] Logging out. Please login with the correct account.")
    _run(["globus", "logout"])
    _run(["globus", "login", "--no-local-server"])

    account = _globus_whoami()
    if account is None:
        raise RuntimeError("[globus] Re-login failed. Run globus login --no-local-server manually.")

    print(f"[globus] Logged in as: {account}")
    answer = input("[globus] Is this the correct Globus account now? [y/n]: ").strip().lower()

    if answer not in {"y", "yes"}:
        raise RuntimeError(
            "[globus] Wrong account selected. Run manually:\n"
            "  globus logout\n"
            "  globus login --no-local-server\n"
            "  globus whoami"
        )


def _ensure_hubmap_login() -> None:
    print("\n[hubmap] Running HuBMAP login.")
    print("[hubmap] If a URL appears, open it in your browser and paste the auth code here.\n")

    _run(["hubmap-clt", "login", "--no-browser"])
    print("[hubmap] HuBMAP login step complete.\n")


def _start_gcp(wait_seconds: int = 120) -> None:
    binary = _get_gcp_binary()
    if binary is None:
        raise RuntimeError("[gcp] GCP binary not found.")

    print("[gcp] Stopping any previous Globus Connect Personal process...")
    subprocess.run([binary, "-stop"], capture_output=True, text=True)
    time.sleep(3)

    print("[gcp] Starting Globus Connect Personal...")
    subprocess.Popen(
        [binary, "-start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.time() + wait_seconds
    last_status = ""

    while time.time() < deadline:
        result = subprocess.run(
            [binary, "-status"],
            capture_output=True,
            text=True,
        )

        status = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        last_status = status

        print("[gcp] Status:")
        print(status)

        lower_status = status.lower()
        is_connected = (
            "globus online:" in lower_status
            and "connected" in lower_status
            and "no globus connect personal connected" not in lower_status
        )

        if is_connected:
            print("[gcp] Globus Connect Personal is connected.")
            print("[gcp] Waiting 15 seconds for Globus Transfer API to recognize endpoint...")
            time.sleep(15)
            return

        print("[gcp] Waiting for GCP to connect...")
        time.sleep(5)

    raise RuntimeError(
        "[gcp] GCP did not become connected.\n"
        f"Last status:\n{last_status}\n\n"
        "Try manually:\n"
        f"  {binary} -stop\n"
        f"  {binary} -start -debug\n"
        f"  {binary} -status"
    )


def _build_manifest(hubmap_ids: list, manifest_dir: str) -> str:
    os.makedirs(manifest_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    manifest_path = os.path.join(manifest_dir, f"hubmap_manifest_{timestamp}.txt")

    with open(manifest_path, "w") as f:
        for hubmap_id in hubmap_ids:
            f.write(f"{hubmap_id.strip()} /\n")

    print(f"[gcp] Manifest generated: {manifest_path}")
    print(f"[gcp] IDs included: {', '.join(hubmap_ids)}")

    return manifest_path


def _resolve_manifest(
    hubmap_id: Union[str, list, None],
    manifest_path: Union[str, None],
    manifest_dir: str,
) -> str:
    if hubmap_id is not None and manifest_path is not None:
        raise ValueError("Provide either hubmap_id or manifest_path, not both.")

    if manifest_path is not None:
        manifest_path = os.path.abspath(os.path.expanduser(manifest_path))
        if not os.path.isfile(manifest_path):
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")
        print(f"[gcp] Using manifest: {manifest_path}")
        return manifest_path

    if hubmap_id is not None:
        ids = [hubmap_id] if isinstance(hubmap_id, str) else list(hubmap_id)
        if not ids:
            raise ValueError("hubmap_id list is empty.")
        return _build_manifest(ids, manifest_dir)

    raise ValueError("Provide either hubmap_id or manifest_path.")


def setup() -> None:
    _ensure_required_tools()
    _setup_gcp_once()
    _set_gcp_allowed_path_to_cwd()
    _ensure_globus_login()
    _ensure_hubmap_login()
    print("[setup] Setup complete. You can now call transfer().")


def transfer(
    destination: str,
    hubmap_id: Union[str, list, None] = None,
    manifest_path: Union[str, None] = None,
    protected: bool = False,
) -> None:
    if destination is None:
        if isinstance(hubmap_id, str):
            destination = f"./{hubmap_id}"
        elif isinstance(hubmap_id, list):
            destination = "./hubmap_downloads"
        elif manifest_path is not None:
            destination = "./hubmap_downloads"
        else:
            raise ValueError("destination could not be inferred. Provide hubmap_id or manifest_path.")

    destination = os.path.abspath(os.path.expanduser(destination))
    os.makedirs(destination, exist_ok=True)

    _ensure_required_tools()
    _setup_gcp_once()
    _set_gcp_allowed_path_to_cwd()
    _ensure_globus_login()
    _ensure_hubmap_login()

    manifest = _resolve_manifest(
        hubmap_id=hubmap_id,
        manifest_path=manifest_path,
        manifest_dir=destination,
    )

    _start_gcp()

    cmd = ["hubmap-clt", "transfer", manifest, "--destination", destination]

    if protected:
        cmd.append("--from-protected-space")

    print(f"[gcp] Starting {'protected' if protected else 'public'} transfer...")
    print("[gcp] Command:", " ".join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    print(output)

    error_indicators = [
        "Globus CLI Error",
        "Transfer API Error",
        "GCDisconnectedException",
        "is not currently connected",
        "is not active",
        "Transfer failure",
        "Traceback",
        "Error:",
    ]

    if result.returncode != 0 or any(error in output for error in error_indicators):
        raise RuntimeError(
            "[gcp] Transfer failed. See output above. "
            "Check GCP allowed path, endpoint connectivity, and dataset access."
        )

    print(f"[gcp] Transfer complete. Data should be in: {destination}")
    
if __name__ == "__main__":
    hubmap_input = input("Enter HuBMAP ID(s), comma-separated if multiple: ").strip()

    hubmap_ids = [hid.strip() for hid in hubmap_input.split(",") if hid.strip()]

    if not hubmap_ids:
        raise ValueError("No HuBMAP ID provided.")

    hubmap_id = hubmap_ids[0] if len(hubmap_ids) == 1 else hubmap_ids

    transfer(
        hubmap_id=hubmap_id
    )
