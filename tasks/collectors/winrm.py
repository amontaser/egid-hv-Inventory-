"""WinRM session management and PowerShell execution."""

import os
import base64
import json
import logging
import socket
from typing import Any, Optional

import winrm

logger = logging.getLogger(__name__)


def create_winrm_session(host: str, cluster_id: int = None) -> winrm.Session:
    """Create authenticated WinRM session. Credentials from env or DB (Fernet-encrypted)."""
    username = os.getenv("HYPERV_USERNAME")
    password = os.getenv("HYPERV_PASSWORD")
    transport = "ntlm"

    if (not username or not password) and cluster_id:
        username, password, transport = _load_cluster_credentials(cluster_id)

    if not username or not password:
        raise ValueError(
            "No credentials: set HYPERV_USERNAME/HYPERV_PASSWORD env vars "
            "or configure a cluster with saved credentials."
        )

    return winrm.Session(
        target=host,
        auth=(username, password),
        server_cert_validation="ignore",
        transport=transport,
    )


def _load_cluster_credentials(cluster_id: int):
    """Load and decrypt credentials for a cluster from DB."""
    from app.db import get_db_connection

    conn = get_db_connection()
    row = conn.execute(
        "SELECT username, password, transport, require_https FROM clusters WHERE id = ?",
        (cluster_id,),
    ).fetchone()
    conn.close()

    if not row:
        return None, None, "ntlm"

    username = row["username"]
    encrypted_pw = row["password"]
    transport = "ssl" if row["require_https"] else (row["transport"] or "ntlm")

    password = _decrypt_password(encrypted_pw)
    return username, password, transport


def _decrypt_password(encrypted_pw: str) -> Optional[str]:
    try:
        from cryptography.fernet import Fernet

        key = os.getenv("ENCRYPTION_KEY", "")
        key_file = "/opt/hyperv_inventory/encryption_key.key"
        if not key and os.path.exists(key_file):
            with open(key_file, "rb") as f:
                key = f.read().decode().strip()
        if not key:
            return None
        f = Fernet(key.encode() if isinstance(key, str) else key)
        return f.decrypt(encrypted_pw.encode()).decode()
    except Exception as e:
        logger.error(f"Password decryption failed: {e}")
        return None


def run_ps(session: winrm.Session, script: str, context: str = "") -> Optional[Any]:
    """Run a PowerShell script; return parsed JSON or None on error."""
    try:
        result = session.run_ps(script)
        if result.status_code != 0:
            err = result.std_err.decode("utf-8", errors="ignore")
            out = result.std_out.decode("utf-8", errors="ignore").strip()[:200]
            ctx_msg = f" ({context})" if context else ""
            logger.error(
                f"PS error{ctx_msg}: code={result.status_code}, stderr={err}, stdout={out}"
            )
            return None
        output = result.std_out.decode("utf-8", errors="ignore").strip()
        if not output or output in ("null", "[]"):
            return []
        data = json.loads(output)
        return [data] if isinstance(data, dict) else data
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return None
    except OSError:
        raise  # propagate connection failures to caller
    except Exception as e:
        logger.error(f"PS execution error: {e}")
        return None


def run_ps_long(
    session: winrm.Session, script: str, context: str = ""
) -> Optional[Any]:
    """Run large PS scripts via temp file to bypass WinRM size limits.
    Falls back to run_ps for scripts under 7000 bytes."""
    script_bytes = script.encode("utf-8")
    if len(script_bytes) < 7000:
        return run_ps(session, script, context)

    # For very large scripts, use temp file approach
    # Encode to Base64 and write to temp file chunk by chunk
    encoded = base64.b64encode(script_bytes).decode("ascii")

    # Split into smaller chunks (1500 chars each)
    chunk_size = 1500
    chunks = [encoded[i : i + chunk_size] for i in range(0, len(encoded), chunk_size)]

    logger.info(
        f"Using temp file with {len(chunks)} chunks ({len(encoded)} chars encoded)"
    )

    # Use timestamp-based filename for consistency across WinRM calls
    import time

    temp_filename = f"hyperv_script_{int(time.time())}_{os.getpid()}.txt"
    temp_path = f"$env:TEMP\\{temp_filename}"

    # Initialize temp file
    init_cmd = f'"" | Out-File -FilePath "{temp_path}" -Encoding UTF8'
    result = session.run_ps(init_cmd)
    if result.status_code != 0:
        logger.error(
            f"Failed to create temp file: {result.std_err.decode('utf-8', errors='ignore')}"
        )
        return None

    # Append each chunk to temp file
    for i, chunk in enumerate(chunks):
        chunk_cmd = (
            f'"{chunk}" | Out-File -FilePath "{temp_path}" -Append -Encoding UTF8'
        )
        result = session.run_ps(chunk_cmd)
        if result.status_code != 0:
            logger.error(f"Failed to write chunk {i + 1}/{len(chunks)} to temp file")
            session.run_ps(f'Remove-Item "{temp_path}" -ErrorAction SilentlyContinue')
            return None

    # Read temp file, decode, and execute
    wrapper = f"""
        $ErrorActionPreference = "Stop"
        try {{
            $tempFile = "{temp_path}"
            $encoded = Get-Content $tempFile -Raw
            $bytes = [System.Convert]::FromBase64String($encoded)
            $script = [System.Text.Encoding]::UTF8.GetString($bytes)
            Remove-Item $tempFile -Force
            Invoke-Expression -Command $script
        }} catch {{
            Remove-Item "{temp_path}" -ErrorAction SilentlyContinue
            Write-Error $_.Exception.Message
            exit 1
        }}
    """

    result = session.run_ps(wrapper)
    if result.status_code != 0:
        err = result.std_err.decode("utf-8", errors="ignore")
        out = result.std_out.decode("utf-8", errors="ignore").strip()[:200]
        ctx_msg = f" ({context})" if context else ""
        logger.error(
            f"PS temp file error{ctx_msg}: code={result.status_code}, stderr={err}, stdout={out}"
        )
        session.run_ps(f'Remove-Item "{temp_path}" -ErrorAction SilentlyContinue')
        return None
    output = result.std_out.decode("utf-8", errors="ignore").strip()
    if not output or output in ("null", "[]"):
        return []
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error (temp file path): {e}")
        return None

    # Read temp file, decode, and execute
    wrapper = f"""
        $ErrorActionPreference = "Stop"
        try {{
            $tempFile = "{temp_file}"
            $encoded = Get-Content $tempFile -Raw
            $bytes = [System.Convert]::FromBase64String($encoded)
            $script = [System.Text.Encoding]::UTF8.GetString($bytes)
            Remove-Item $tempFile -Force
            Invoke-Expression -Command $script
        }} catch {{
            Remove-Item "{temp_file}" -ErrorAction SilentlyContinue
            Write-Error $_.Exception.Message
            exit 1
        }}
    """

    result = session.run_ps(wrapper)
    if result.status_code != 0:
        err = result.std_err.decode("utf-8", errors="ignore")
        out = result.std_out.decode("utf-8", errors="ignore").strip()[:200]
        ctx_msg = f" ({context})" if context else ""
        logger.error(
            f"PS temp file error{ctx_msg}: code={result.status_code}, stderr={err}, stdout={out}"
        )
        session.run_ps(f'Remove-Item "{temp_file}" -ErrorAction SilentlyContinue')
        return None
    output = result.std_out.decode("utf-8", errors="ignore").strip()
    if not output or output in ("null", "[]"):
        return []
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error (temp file path): {e}")
        return None

    # Step 3: Reassemble and execute
    wrapper = """
        $ErrorActionPreference = "Stop"
        try {
            $encoded = $env:PS_SCRIPT_CHUNKS -join ''
            $bytes = [System.Convert]::FromBase64String($encoded)
            $script = [System.Text.Encoding]::UTF8.GetString($bytes)
            Invoke-Expression -Command $script
            Remove-Item Env:\\PS_SCRIPT_CHUNKS -ErrorAction SilentlyContinue
        } catch {
            Remove-Item Env:\\PS_SCRIPT_CHUNKS -ErrorAction SilentlyContinue
            Write-Error $_.Exception.Message
            exit 1
        }
    """

    result = session.run_ps(wrapper)
    if result.status_code != 0:
        err = result.std_err.decode("utf-8", errors="ignore")
        out = result.std_out.decode("utf-8", errors="ignore").strip()[:200]
        ctx_msg = f" ({context})" if context else ""
        logger.error(
            f"PS chunked execution error{ctx_msg}: code={result.status_code}, stderr={err}, stdout={out}"
        )
        return None
    output = result.std_out.decode("utf-8", errors="ignore").strip()
    if not output or output in ("null", "[]"):
        return []
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error (chunked path): {e}")
        return None
    output = result.std_out.decode("utf-8", errors="ignore").strip()
    if not output or output in ("null", "[]"):
        return []
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error (chunked path): {e}")
        return None


def resolve_node_ip(
    node_name: str, dns_servers_str: str, domain_name: str = None
) -> str:
    """Resolve a cluster node name to IP using configured DNS servers."""
    import dns.resolver

    if not dns_servers_str or not dns_servers_str.strip():
        try:
            return socket.gethostbyname(node_name)
        except socket.gaierror:
            pass
        if domain_name:
            fqdn = f"{node_name}.{domain_name}"
            try:
                return socket.gethostbyname(fqdn)
            except socket.gaierror:
                return fqdn
        return node_name

    dns_servers = [s.strip() for s in dns_servers_str.split(",") if s.strip()]
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = dns_servers
    resolver.timeout = 3
    resolver.lifetime = 5

    if domain_name:
        try:
            ans = resolver.resolve(f"{node_name}.{domain_name}", "A")
            return str(ans[0])
        except Exception:
            pass
    try:
        ans = resolver.resolve(node_name, "A")
        return str(ans[0])
    except Exception:
        pass

    return f"{node_name}.{domain_name}" if domain_name else node_name
