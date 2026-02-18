"""PowerShell and WinRM operations"""

import os
import base64
import json
import logging
from typing import Any, Optional, Dict

import winrm

logger = logging.getLogger(__name__)


def create_winrm_session(host: str, cluster_id: int = None) -> winrm.Session:
    """Create a WinRM session with credentials from environment or database."""
    username = os.getenv("HYPERV_USERNAME")
    password = os.getenv("HYPERV_PASSWORD")

    # If no env credentials, try to get from database
    if (not username or not password) and cluster_id:
        from hyperv_inventory.app.utils.db import get_db_connection

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT username, password, transport, require_https
            FROM clusters
            WHERE id = ?
        """,
            (cluster_id,),
        )
        cluster = cursor.fetchone()
        conn.close()

        if cluster:
            from cryptography.fernet import Fernet

            key = os.getenv("ENCRYPTION_KEY", "")
            if not key and os.path.exists("encryption_key.key"):
                with open("encryption_key.key", "rb") as f:
                    key = f.read().decode()

            if key:
                f = Fernet(key.encode() if isinstance(key, str) else key)
                password = f.decrypt(cluster[1].encode()).decode()

            username = cluster[0]
            logger.info(f"Using database credentials for cluster_id={cluster_id}")

    if not username or not password:
        raise ValueError(
            "HYPERV_USERNAME and HYPERV_PASSWORD must be set, or valid cluster_id required"
        )

    # Determine transport
    transport = "ntlm"  # Default

    # Check if cluster requires HTTPS
    if cluster_id:
        from hyperv_inventory.app.utils.db import get_db_connection

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT require_https FROM clusters WHERE id = ?", (cluster_id,))
        result = cursor.fetchone()
        conn.close()

        if result and result[0]:
            # Cluster requires HTTPS, use SSL transport
            transport = "ssl"

    return winrm.Session(
        target=host,
        auth=(username, password),
        server_cert_validation="ignore",
        transport=transport,
    )


def run_powershell(session: winrm.Session, script: str) -> Optional[Any]:
    """Execute a PowerShell script and return parsed JSON result."""
    try:
        result = session.run_ps(script)

        if result.status_code != 0:
            logger.error(
                f"PowerShell error: {result.std_err.decode('utf-8', errors='ignore')}"
            )
            return None

        output = result.std_out.decode("utf-8", errors="ignore").strip()

        if not output or output == "null":
            return []

        # Handle single object vs array
        data = json.loads(output)
        if isinstance(data, dict):
            return [data]
        return data

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        logger.debug(f"Raw output: {output[:500] if output else 'empty'}")
        return None
    except Exception as e:
        logger.error(f"PowerShell execution error: {e}")
        return None


def run_powershell_long(session: winrm.Session, script: str) -> Optional[Any]:
    """Execute large PowerShell scripts via Base64 encoding to bypass WinRM size limits.

    Args:
        session: WinRM session
        script: PowerShell script to execute

    Returns:
        Parsed JSON result or None on error
    """
    # Check script size - use regular method for small scripts
    script_bytes = script.encode("utf-8")
    if len(script_bytes) < 7000:
        logger.debug(f"Script size: {len(script_bytes)} bytes, using regular method")
        return run_powershell(session, script)

    # Encode large script to Base64
    logger.info(f"Script size: {len(script_bytes)} bytes, using Base64 encoding")
    encoded_script = base64.b64encode(script_bytes).decode("ascii")

    # Wrapper that decodes and executes on remote host
    wrapper = f"""
        $ErrorActionPreference = "Stop"
        try {{
            $scriptBytes = [System.Convert]::FromBase64String('{encoded_script}')
            $script = [System.Text.Encoding]::UTF8.GetString($scriptBytes)
            Invoke-Expression -Command $script
        }} catch {{
            Write-Error $_.Exception.Message
            exit 1
        }}
    """

    result = session.run_ps(wrapper)

    if result.status_code != 0:
        error_msg = result.std_err.decode("utf-8", errors="ignore")
        logger.error(f"PowerShell error (Base64): {error_msg}")
        return None

    output = result.std_out.decode("utf-8", errors="ignore").strip()

    if not output or output == "null":
        return []

    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse PowerShell output: {e}")
        return None
