"""
-----------
Globus Connect Personal (GCP) + HuBMAP transfer script.

Simple workflow:

1. Check that Globus CLI, HuBMAP CLT, and Globus Connect Personal are available.
2. Make sure the user is logged into Globus and HuBMAP.
3. Ask how the destination path should be interpreted:
   - Option 1: use destination as a full absolute path.
   - Option 2: append destination to the current working directory.
4. If the final destination is inside the home directory, download there directly.
5. If the final destination is outside home, download temporarily inside home first.
6. Generate or use the HuBMAP manifest file.
7. Start Globus Connect Personal and submit the HuBMAP transfer.
8. Wait for the Globus transfer task to finish.
9. If a temporary home download was used, copy the files to the final destination.
10. Clean up temporary files from the home directory.
"""

import glob
import os
import re
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
    return os.path.abspath(os.path.expanduser("~"))


def _run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kwargs)


def _add_to_path(path: str) -> None:
    current = os.environ.get("PATH", "")
    paths = current.split(os.pathsep) if current else []
    if path not in paths:
        os.environ["PATH"] = path + os.pathsep + current


def _exists(command: str) -> bool:
    return shutil.which(command) is not None


def _ensure_cli(command: str, package: str) -> None:
    _add_to_path(os.path.dirname(sys.executable))

    if _exists(command):
        return

    print(f"[setup] {command} not found. Installing {package}...")
    _run([sys.executable, "-m", "pip", "install", package])

    if not _exists(command):
        raise RuntimeError(
            f"[setup] Installed {package}, but {command} is still not found. "
            f"Python executable: {sys.executable}"
        )


def _ensure_required_tools() -> None:
    _ensure_cli("globus", "globus-cli")
    _ensure_cli("hubmap-clt", "atlas-consortia-clt")

    print("[setup] Required CLIs available:")
    print(f"        globus:     {shutil.which('globus')}")
    print(f"        hubmap-clt: {shutil.which('hubmap-clt')}")


def _get_gcp_dir() -> Union[str, None]:
    matches = [
        p for p in glob.glob(os.path.join(_home(), "globusconnectpersonal-*"))
        if os.path.isdir(p)
    ]
    matches.sort()
    return matches[0] if matches else None


def _get_gcp_binary() -> Union[str, None]:
    gcp_dir = _get_gcp_dir()
    if gcp_dir is None:
        return None

    _add_to_path(gcp_dir)
    binary = os.path.join(gcp_dir, "globusconnectpersonal")
    return binary if os.path.isfile(binary) else None


def _is_gcp_configured() -> bool:
    lta_dir = os.path.join(_home(), ".globusonline", "lta")
    if not os.path.isdir(lta_dir):
        return False

    for _, _, files in os.walk(lta_dir):
        if files:
            return True

    return False


def _download_file(url: str, output_path: str) -> None:
    if _exists("wget"):
        _run(["wget", "-q", "--show-progress", "-O", output_path, url])
        return

    if _exists("curl"):
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
            text = f.read()
    except FileNotFoundError:
        text = ""

    if gcp_dir not in text:
        with open(bashrc, "a") as f:
            f.write(f"\n# Added by transfer.py\n{path_line}\n")
        print(f"[gcp-setup] Added GCP to ~/.bashrc: {path_line}")

    print(f"[gcp-setup] GCP installed at: {gcp_dir}")


def _setup_gcp_once() -> None:
    if _get_gcp_binary() is None:
        _install_gcp()

    if _is_gcp_configured():
        print("[gcp-setup] GCP already configured.")
        return

    binary = _get_gcp_binary()
    if binary is None:
        raise RuntimeError("[gcp-setup] GCP binary not found.")

    print("\n[gcp-setup] One-time GCP setup required.")
    print("[gcp-setup] A URL may appear. Open it, log in, paste the code, then enter endpoint name.")
    _run([binary, "-setup", "--no-gui"])
    print("[gcp-setup] GCP setup complete.\n")


def _set_gcp_allowed_path() -> None:
    home = _home()
    config_dir = os.path.join(home, ".globusonline", "lta")
    config_path = os.path.join(config_dir, "config-paths")

    if not os.path.isdir(config_dir):
        raise RuntimeError(
            f"[gcp-setup] GCP config directory not found: {config_dir}\n"
            "Run GCP setup first."
        )

    with open(config_path, "w") as f:
        f.write(f"{home},0,1\n")

    print(f"[gcp-setup] GCP allowed path set to: {home}")


def _start_gcp(wait_seconds: int = 120) -> None:
    binary = _get_gcp_binary()
    if binary is None:
        raise RuntimeError("[gcp] GCP binary not found.")

    print("[gcp] Restarting Globus Connect Personal...")
    subprocess.run([binary, "-stop"], capture_output=True, text=True)
    time.sleep(3)

    subprocess.Popen(
        [binary, "-start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.time() + wait_seconds
    last_status = ""

    while time.time() < deadline:
        result = subprocess.run([binary, "-status"], capture_output=True, text=True)
        status = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        last_status = status
        lower = status.lower()

        connected = (
            "globus online:" in lower
            and "connected" in lower
            and "no globus connect personal connected" not in lower
        )

        if connected:
            print("[gcp] Globus Connect Personal is connected.")
            print("[gcp] Please wait a few seconds...")
            time.sleep(15)
            return

        print("[gcp] Waiting for GCP to connect...")
        time.sleep(5)

    raise RuntimeError(
        "[gcp] GCP did not connect.\n"
        f"Last status:\n{last_status}\n\n"
        "Try manually:\n"
        f"  {binary} -stop\n"
        f"  {binary} -start -debug\n"
        f"  {binary} -status"
    )


def _globus_whoami() -> Union[str, None]:
    result = subprocess.run(["globus", "whoami"], capture_output=True, text=True)
    if result.returncode != 0:
        return None

    account = result.stdout.strip()
    return account if account else None


def _globus_login_no_local_server() -> None:
    result = subprocess.run(
        ["globus", "login", "--no-local-server"],
        input="\n",
        text=True,
        capture_output=True,
    )
    print((result.stdout or "") + "\n" + (result.stderr or ""))

    auth_code = input("[globus] Paste the Authorization Code here: ").strip()

    result2 = subprocess.run(
        ["globus", "login", "--no-local-server"],
        input=auth_code + "\n",
        text=True,
        capture_output=True,
    )
    print((result2.stdout or "") + "\n" + (result2.stderr or ""))

    if result2.returncode != 0:
        raise RuntimeError("[globus] Globus login failed.")


def _ensure_globus_login() -> None:
    account = _globus_whoami()

    if account is None:
        print("\n[globus] Globus CLI login required.")
        _globus_login_no_local_server()
        account = _globus_whoami()

    if account is None:
        raise RuntimeError("[globus] globus login finished, but globus whoami still failed.")

    print(f"[globus] Logged in as: {account}")
    answer = input("[globus] Is this the correct Globus account? [y/n]: ").strip().lower()

    if answer in {"y", "yes"}:
        return

    print("[globus] Logging out. Please login with the correct account.")

    subprocess.run(
        ["globus", "logout"],
        input="y\n",
        text=True,
        check=True,
    )

    _globus_login_no_local_server()

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
    result = subprocess.run(
        ["hubmap-clt", "whoami"],
        capture_output=True,
        text=True,
    )

    output = (result.stdout or "") + "\n" + (result.stderr or "")

    if result.returncode == 0 and "not logged" not in output.lower():
        print("[hubmap] HuBMAP login already active.")
        return

    print("\n[hubmap] Running HuBMAP login.")
    _run(["hubmap-clt", "login"])
    print("[hubmap] HuBMAP login step complete.\n")


def _handle_session_reauth(output: str) -> None:
    match = re.search(r"globus session update (\S+)", output)
    if not match:
        raise RuntimeError("Session reauth required but identity ID could not be extracted.")

    identity_id = match.group(1)
    print(f"[globus] Session expired. Re-authenticating with identity: {identity_id}")

    result = subprocess.run(
        ["globus", "session", "update", "--no-local-server", identity_id],
        input="\n",
        text=True,
        capture_output=True,
    )
    print(result.stdout)

    auth_code = input("Paste the Authorization Code here: ").strip()

    result2 = subprocess.run(
        ["globus", "session", "update", "--no-local-server", identity_id],
        input=auth_code + "\n",
        text=True,
        capture_output=True,
    )

    if result2.returncode != 0:
        raise RuntimeError("[globus] Re-authentication failed.")

    print("[globus] Re-authentication successful.")


def _can_write(path: str) -> bool:
    try:
        path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(path, exist_ok=True)

        test_file = os.path.join(path, ".fusion_write_test")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)

        return True

    except Exception as e:
        print(f"[transfer] Cannot write to destination: {path}")
        print(f"[transfer] Reason: {e}")
        return False


def _collapse_adjacent_duplicates(path: str) -> str:
    path = os.path.abspath(os.path.expanduser(path))
    absolute = path.startswith(os.sep)

    parts = [p for p in path.split(os.sep) if p not in {"", "."}]
    cleaned = []

    for part in parts:
        if cleaned and cleaned[-1] == part:
            continue
        cleaned.append(part)

    result = os.path.join(*cleaned) if cleaned else ""
    if absolute:
        result = os.sep + result

    return os.path.abspath(result)


def _append_to_cwd(destination: str) -> str:
    cwd = os.getcwd()
    parts = [
        p for p in os.path.expanduser(destination).split(os.sep)
        if p not in {"", "."}
    ]

    cwd_last = os.path.basename(os.path.abspath(cwd))

    if parts and parts[0] == cwd_last:
        parts = parts[1:]

    joined = os.path.join(cwd, os.path.join(*parts) if parts else "")
    return _collapse_adjacent_duplicates(joined)


def _ask_destination(destination: str) -> tuple:
    while True:
        print("\n[transfer] How should destination be interpreted?")
        print("[transfer] 1 = Use destination as absolute/full path")
        print("[transfer] 2 = Append destination to current working directory")
        print(f"[transfer] destination entered: {destination}")
        print(f"[transfer] current working directory: {os.getcwd()}")

        choice = input("[transfer] Choose 1 or 2: ").strip()

        if choice == "1":
            if not os.path.isabs(os.path.expanduser(destination)):
                print("[transfer] You selected absolute/full path, but destination is not absolute.")
                destination = input("[transfer] Enter corrected absolute/full destination path: ").strip()
                continue

            resolved = _collapse_adjacent_duplicates(destination)

            if _can_write(resolved):
                print(f"[transfer] Using absolute/full destination: {resolved}")
                return resolved, "absolute"

            destination = input("[transfer] Enter corrected absolute/full destination path: ").strip()
            continue

        if choice == "2":
            resolved = _append_to_cwd(destination)

            if _can_write(resolved):
                print(f"[transfer] Using cwd-appended destination: {resolved}")
                return resolved, "cwd"

            destination = input("[transfer] Enter corrected destination path: ").strip()
            continue

        print("[transfer] Invalid choice. Please enter 1 or 2.")


def _resolve_download_plan(final_destination: str, mode: str) -> tuple:
    home = _home()

    if mode == "cwd":
        if os.path.commonpath([home, final_destination]) != home:
            raise ValueError(
                "[transfer] CWD-appended destination resolved outside home.\n"
                f"[transfer] Home:        {home}\n"
                f"[transfer] Destination: {final_destination}"
            )

        return final_destination, os.path.relpath(final_destination, home), False

    if mode == "absolute":
        if os.path.commonpath([home, final_destination]) == home:
            return final_destination, os.path.relpath(final_destination, home), False

        name = os.path.basename(os.path.abspath(final_destination))
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        temp = os.path.join(home, "hubmap_temp_transfer", f"{name}_{timestamp}")

        os.makedirs(temp, exist_ok=True)

        return temp, os.path.relpath(temp, home), True

    raise ValueError(f"Unknown destination mode: {mode}")


def _build_manifest(hubmap_ids: list, manifest_dir: str) -> str:
    os.makedirs(manifest_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    manifest_path = os.path.join(manifest_dir, f"hubmap_manifest_{timestamp}.txt")

    clean_ids = []

    with open(manifest_path, "w") as f:
        for hubmap_id in hubmap_ids:
            clean_id = str(hubmap_id).strip()
            if clean_id:
                clean_ids.append(clean_id)
                f.write(f"{clean_id} /\n")

    if not clean_ids:
        raise ValueError("No valid HuBMAP IDs were provided.")

    print(f"[gcp] Manifest generated: {manifest_path}")
    print(f"[gcp] IDs included: {', '.join(clean_ids)}")

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


def _wait_for_task(task_id: str) -> None:
    print(f"[gcp] Waiting for Globus task to finish: {task_id}")

    result = subprocess.run(
        ["globus", "task", "wait", task_id],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print((result.stdout or "") + "\n" + (result.stderr or ""))
        raise RuntimeError(
            "[gcp] Globus task did not finish successfully. "
            "Check the task in Globus activity before copying."
        )

    print("[gcp] Globus task finished successfully.")


def _copy_contents(source_dir: str, destination_dir: str) -> None:
    source_dir = os.path.abspath(os.path.expanduser(source_dir))
    destination_dir = os.path.abspath(os.path.expanduser(destination_dir))

    os.makedirs(destination_dir, exist_ok=True)

    print(f"[transfer] Copying downloaded data to: {destination_dir}")

    for item in os.listdir(source_dir):
        src = os.path.join(source_dir, item)
        dst = os.path.join(destination_dir, item)

        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    print("[transfer] Copy complete.")


def _clean_temp_download(download_destination: str) -> None:
    shutil.rmtree(download_destination, ignore_errors=True)

    temp_root = os.path.join(_home(), "hubmap_temp_transfer")

    if os.path.isdir(temp_root):
        for root, dirs, files in os.walk(temp_root, topdown=False):
            if not dirs and not files:
                try:
                    os.rmdir(root)
                except OSError:
                    pass


def setup() -> None:
    _ensure_required_tools()
    _setup_gcp_once()
    _set_gcp_allowed_path()
    _ensure_globus_login()
    _ensure_hubmap_login()

    print("[setup] Setup complete. You can now call transfer().")


def transfer(
    destination: Union[str, None] = None,
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

    final_destination, mode = _ask_destination(destination)

    download_destination, destination_for_hubmap, copy_after_download = _resolve_download_plan(
        final_destination,
        mode,
    )

    print(f"[transfer] Final destination: {final_destination}")
    if copy_after_download:
        print(f"[transfer] Temporary download location: {download_destination}")

    os.makedirs(download_destination, exist_ok=True)

    _ensure_required_tools()
    _setup_gcp_once()
    _set_gcp_allowed_path()
    _ensure_globus_login()
    _ensure_hubmap_login()

    manifest = _resolve_manifest(
        hubmap_id=hubmap_id,
        manifest_path=manifest_path,
        manifest_dir=final_destination,
    )

    _start_gcp()

    cmd = ["hubmap-clt", "transfer", manifest, "-d", destination_for_hubmap]

    if protected:
        cmd.append("--from-protected-space")

    print(f"[gcp] Starting {'protected' if protected else 'public'} transfer...")
    print("[gcp] This may take a few minutes. Please wait...")
    #print("[gcp] Command:", " ".join(cmd))

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

    for attempt in range(2):
        result = subprocess.run(cmd, capture_output=True, text=True)
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        print(output)

        if "Session reauthentication required" in output:
            if attempt == 0:
                _handle_session_reauth(output)
                continue

            raise RuntimeError("[globus] Re-authentication succeeded but transfer still failed.")

        failed = result.returncode != 0 or any(err in output for err in error_indicators)

        if failed:
            raise RuntimeError(
                "[gcp] Transfer failed. See output above. "
                "Check GCP allowed path, endpoint connectivity, and dataset access."
            )

        task_match = re.search(r"Task ID:\s*([a-fA-F0-9-]+)", output)
        task_id = task_match.group(1) if task_match else None

        if copy_after_download:
            if task_id is None:
                raise RuntimeError(
                    "[gcp] Transfer was accepted, but Task ID could not be found. "
                    "Cannot safely copy to final destination until transfer completion is confirmed."
                )

            _wait_for_task(task_id)
            _copy_contents(download_destination, final_destination)
            _clean_temp_download(download_destination)

            print("[gcp] Transfer and copy complete.")
            print(f"[gcp] Final data location: {final_destination}")
        else:
            print(f"[gcp] Transfer initiated. Data should be in: {final_destination}")
            print("[gcp] If files are still moving, check Globus activity.")

        break