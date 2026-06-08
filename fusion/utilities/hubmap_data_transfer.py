"""
transfer.py
-----------
Globus Connect Personal (GCP) setup + file transfer module.

User-facing API:
    setup()       -- optional explicit one-time setup
    transfer()    -- auto-sets up on first call, then transfers

transfer() accepts either:
    - hubmap_id / list of hubmap_ids  → manifest is auto-generated
    - manifest_path                   → manifest file is used directly
"""

import glob
import os
import shutil
import subprocess
import time
from typing import Union

# ── Constants ─────────────────────────────────────────────────────────────────

GCP_TARBALL_URL = (
    "https://downloads.globus.org/globus-connect-personal"
    "/linux/stable/globusconnectpersonal-latest.tgz"
)

# ~ is already present in config-paths by default after -setup, no edits needed.
DEFAULT_ALLOWED_PATH = "~,0,1"

# Where auto-generated manifest files are written (current working directory)
DEFAULT_MANIFEST_DIR = os.getcwd()

# ── Path helpers ──────────────────────────────────────────────────────────────

def _home() -> str:
    return os.path.expanduser("~")


def _get_gcp_dir() -> Union[str, None]:
    """
    Dynamically resolve the installed GCP directory by scanning ~ for
    any folder matching globusconnectpersonal-*.
    e.g. /home/user/globusconnectpersonal-3.2.8
    Returns None if not installed.
    """
    matches = [
        m for m in glob.glob(os.path.join(_home(), "globusconnectpersonal-*"))
        if os.path.isdir(m)
    ]
    return matches[0] if matches else None


def _get_gcp_binary() -> Union[str, None]:
    """Return the full path to the globusconnectpersonal binary, or None."""
    gcp_dir = _get_gcp_dir()
    if gcp_dir is None:
        return None
    binary = os.path.join(gcp_dir, "globusconnectpersonal")
    return binary if os.path.isfile(binary) else None


# ── State checks ──────────────────────────────────────────────────────────────

def _is_installed() -> bool:
    """True if the GCP directory and binary both exist."""
    return _get_gcp_binary() is not None


def _is_authenticated() -> bool:
    """
    True if GCP has been through -setup at least once.
    config-paths is created by -setup, so its presence = successful auth.
    """
    config = os.path.join(_home(), ".globusonline", "lta", "config-paths")
    return os.path.exists(config)


# ── Installation ──────────────────────────────────────────────────────────────

def _install_gcp() -> None:
    """
    1. wget tarball into ~
    2. tar extract into ~
    3. rm tarball
    4. Write versioned PATH into ~/.bashrc (idempotent)
    5. Export PATH for the current process
    """
    home = _home()
    tarball = os.path.join(home, "globusconnectpersonal-latest.tgz")

    # Step 1 — Download
    print("[gcp-setup] Downloading Globus Connect Personal...")
    subprocess.run(
        ["wget", "-q", "--show-progress", "-P", home, GCP_TARBALL_URL],
        check=True,
    )

    # Step 2 — Extract
    print("[gcp-setup] Extracting tarball...")
    subprocess.run(
        ["tar", "xzf", tarball, "-C", home],
        check=True,
    )

    # Step 3 — Remove tarball
    os.remove(tarball)

    # Resolve the versioned folder that was just extracted
    gcp_dir = _get_gcp_dir()
    if gcp_dir is None:
        raise RuntimeError(
            "[gcp-setup] Extraction succeeded but GCP directory not found. "
            "Check ~/globusconnectpersonal-* manually."
        )

    # Step 4 — Write versioned PATH to ~/.bashrc (idempotent)
    bashrc = os.path.join(home, ".bashrc")
    path_line = f'export PATH="{gcp_dir}:$PATH"'
    try:
        with open(bashrc, "r") as f:
            bashrc_content = f.read()
    except FileNotFoundError:
        bashrc_content = ""

    if "globusconnectpersonal" not in bashrc_content:
        with open(bashrc, "a") as f:
            f.write(f"\n# Added by gcp-setup\n{path_line}\n")
        print(f"[gcp-setup] Added to ~/.bashrc: {path_line}")

    # Step 5 — Export for the current running process
    os.environ["PATH"] = f"{gcp_dir}:{os.environ['PATH']}"
    print(f"[gcp-setup] GCP installed at: {gcp_dir}")


# ── Authentication ────────────────────────────────────────────────────────────

def _authenticate_gcp() -> None:
    """
    Run globusconnectpersonal -setup --no-gui.
    Prints a URL, waits for user to paste back the auth code.
    config-paths already contains ~ by default after setup — no edits needed.
    """
    binary = _get_gcp_binary()

    print("\n[gcp-setup] One-time Globus authentication required.")
    print("[gcp-setup] Steps:")
    print("  1. A URL will appear below.")
    print("  2. Open it in a browser and log in with your Globus account.")
    print("  3. Copy the auth code and paste it here when prompted.\n")

    subprocess.run([binary, "-setup", "--no-gui"], check=True)

    print("[gcp-setup] Authentication complete.\n")


# ── GCP process control ───────────────────────────────────────────────────────

def _start_gcp() -> None:
    """
    globusconnectpersonal -stop  (safe even if not running)
    globusconnectpersonal -start (backgrounded)
    """
    binary = _get_gcp_binary()

    subprocess.run([binary, "-stop"], capture_output=True)
    subprocess.Popen(
        [binary, "-start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("[gcp] Globus Connect Personal started.")
    time.sleep(3)  # allow GCP to initialize before transfer begins


# ── Setup gate ────────────────────────────────────────────────────────────────

def _ensure_gcp_ready() -> None:
    """
    Idempotent gate called before every transfer.
    - First call ever : installs + authenticates (~1 min, one user interaction)
    - All later calls : two fast os.path.exists() checks, then continues
    """
    if not _is_installed():
        print("[gcp-setup] First-time setup — this runs once and takes about a minute.\n")
        _install_gcp()

    if not _is_authenticated():
        _authenticate_gcp()


# ── Manifest helpers ──────────────────────────────────────────────────────────

def _build_manifest(
    hubmap_ids: list,
    manifest_dir: str = DEFAULT_MANIFEST_DIR,
) -> str:
    """
    Auto-generate a manifest file from a list of HuBMAP IDs.
    Writes to <manifest_dir>/hubmap_manifest_<timestamp>.txt
    and returns the path to that file.

    Manifest format expected by hubmap-clt:
        HBM123.ABCD.456 /
        HBM789.EFGH.012 /
        ...
    """
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    manifest_path = os.path.join(manifest_dir, f"hubmap_manifest_{timestamp}.txt")

    with open(manifest_path, "w") as f:
        for hid in hubmap_ids:
            f.write(f"{hid.strip()} /\n")

    print(f"[gcp] Manifest generated: {manifest_path}")
    print(f"[gcp] IDs included: {', '.join(hubmap_ids)}")
    return manifest_path


def _resolve_manifest(
    hubmap_id: Union[str, list, None],
    manifest_path: Union[str, None],
) -> str:
    """
    Resolve which manifest to use and return its path.

    Rules:
      - If manifest_path is given              → use it directly
      - If hubmap_id (str or list) is given    → auto-generate manifest
      - If both are given                      → raise, ambiguous
      - If neither is given                    → raise, nothing to transfer
    """
    if hubmap_id is not None and manifest_path is not None:
        raise ValueError(
            "Provide either hubmap_id or manifest_path, not both."
        )

    if manifest_path is not None:
        if not os.path.isfile(manifest_path):
            raise FileNotFoundError(f"Manifest file not found: {manifest_path}")
        print(f"[gcp] Using manifest: {manifest_path}")
        return manifest_path

    if hubmap_id is not None:
        ids = [hubmap_id] if isinstance(hubmap_id, str) else list(hubmap_id)
        if not ids:
            raise ValueError("hubmap_id list is empty.")
        return _build_manifest(ids)

    raise ValueError(
        "You must provide either hubmap_id (str or list) or manifest_path."
    )


# ── Post-transfer move ────────────────────────────────────────────────────────

def _move_to_destination(destination: str) -> None:
    """
    hubmap-clt always downloads into ~.
    This function moves everything that landed in ~ into the
    user-specified destination directory.

    Only moves directories that look like HuBMAP dataset folders
    (i.e. match the HBM*.*.* pattern) to avoid moving unrelated files.
    """
    home = _home()
    os.makedirs(destination, exist_ok=True)

    moved = []
    for item in glob.glob(os.path.join(home, "HBM*")):
        if os.path.isdir(item):
            dest_path = os.path.join(destination, os.path.basename(item))
            shutil.move(item, dest_path)
            moved.append(os.path.basename(item))

    if moved:
        print(f"[gcp] Moved {len(moved)} dataset(s) to {destination}:")
        for name in moved:
            print(f"      {name}")
    else:
        print(f"[gcp] Warning: no HBM* folders found in ~ to move.")


# ── Public API ────────────────────────────────────────────────────────────────

def setup() -> None:
    """
    Optional explicit one-time setup.
    Users can call this once upfront to get setup out of the way
    before their first transfer(). Subsequent calls are no-ops.

    Example
    -------
    >>> import transfer
    >>> transfer.setup()
    """
    _ensure_gcp_ready()
    print("[gcp-setup] Setup complete. You can now call transfer().")


def transfer(
    destination: str,
    hubmap_id: Union[str, list, None] = None,
    manifest_path: Union[str, None] = None,
    protected: bool = False,
) -> None:
    """
    Transfer HuBMAP datasets using hubmap-clt + Globus Connect Personal.
    Runs one-time setup automatically on the very first call.

    Data lands in ~ first (hubmap-clt behavior), then is moved
    to the specified destination automatically.

    Provide EITHER hubmap_id OR manifest_path — not both.

    Parameters
    ----------
    destination   : str
        Directory where downloaded datasets will be moved after transfer.
        e.g. "/hive/user-workspaces/injarapu/1815"

    hubmap_id     : str or list of str, optional
        A single HuBMAP ID or a list of IDs.
        A manifest file is auto-generated from these IDs.
        e.g. "HBM123.ABCD.456"
             ["HBM123.ABCD.456", "HBM789.EFGH.012"]

    manifest_path : str, optional
        Path to an existing manifest.txt file.
        e.g. "/home/user/manifest.txt"

    protected     : bool, default False
        Set to True for protected (controlled-access) datasets.
        Adds --from-protected-space to the hubmap-clt command.
        Only applicable to users with protected data access.

    Examples
    --------
    # Single public dataset
    >>> transfer.transfer(
    ...     hubmap_id="HBM123.ABCD.456",
    ...     destination="/hive/user-workspaces/injarapu/1815"
    ... )

    # Multiple public datasets
    >>> transfer.transfer(
    ...     hubmap_id=["HBM123.ABCD.456", "HBM789.EFGH.012"],
    ...     destination="/hive/user-workspaces/injarapu/1815"
    ... )

    # Protected dataset (controlled-access users only)
    >>> transfer.transfer(
    ...     hubmap_id="HBM123.ABCD.456",
    ...     destination="/hive/user-workspaces/injarapu/1815",
    ...     protected=True
    ... )

    # Existing manifest file
    >>> transfer.transfer(
    ...     manifest_path="manifest.txt",
    ...     destination="/hive/user-workspaces/injarapu/1815"
    ... )
    """
    # ── Gate: install + auth if this is the first call ────────────────────────
    _ensure_gcp_ready()

    # ── Resolve manifest ──────────────────────────────────────────────────────
    manifest = _resolve_manifest(hubmap_id, manifest_path)

    # ── Start GCP ─────────────────────────────────────────────────────────────
    _start_gcp()

    # ── Build hubmap-clt command ───────────────────────────────────────────────
    cmd = ["hubmap-clt", "transfer", manifest]
    if protected:
        cmd.append("--from-protected-space")

    # ── Run transfer (downloads into ~) ───────────────────────────────────────
    print(f"[gcp] Starting {'protected' if protected else 'public'} transfer...")
    subprocess.run(cmd, check=True)

    # ── Move from ~ to destination ────────────────────────────────────────────
    print(f"[gcp] Moving datasets to: {destination}")
    _move_to_destination(destination)

    print("[gcp] Transfer complete.")