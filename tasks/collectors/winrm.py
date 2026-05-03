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
    session: winrm.Session, script: str, context: str = "", force_encoded: bool = False
) -> Optional[Any]:
    """Run large PS scripts via PowerShell native -EncodedCommand.
    Uses temp-file approach for large outputs to bypass WinRM envelope size limits.
    Falls back to run_ps for scripts under 7000 bytes unless force_encoded=True."""
    script_bytes = script.encode("utf-8")
    if len(script_bytes) < 7000 and not force_encoded:
        return run_ps(session, script, context)

    import time as _time

    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    temp_name = f"hyperv_out_{int(_time.time())}_{os.getpid()}.json"
    temp_path = f"C:\\Windows\\Temp\\{temp_name}"

    # Write output to temp file instead of stdout to bypass WinRM envelope limits
    wrapper = (
        f"$null = Set-Item WSMan:\\localhost\\MaxEnvelopeSizeKB 4096 -ErrorAction SilentlyContinue; "
        + script.replace(
            "ConvertTo-Json",
            f"ConvertTo-Json | Out-File -FilePath '{temp_path}' -Encoding UTF8; Write-Output 'DONE'",
        )
    )
    # Only replace the first occurrence of ConvertTo-Json
    wrapper = script
    wrapper_parts = wrapper.split("ConvertTo-Json", 1)
    if len(wrapper_parts) == 2:
        wrapper = (
            wrapper_parts[0]
            + f"ConvertTo-Json | Out-File -FilePath '{temp_path}' -Encoding UTF8; Write-Output 'FILE_WRITTEN'"
            + wrapper_parts[1]
        )

    encoded_wrapper = base64.b64encode(wrapper.encode("utf-16-le")).decode("ascii")
    logger.info(
        f"Using PowerShell -EncodedCommand with temp file ({len(encoded_wrapper)} chars)"
    )

    try:
        result = session.run_cmd("powershell.exe", ["-EncodedCommand", encoded_wrapper])
    except Exception as e:
        logger.error(f"Failed to execute encoded command: {e}")
        return None

    if result.status_code != 0:
        err = result.std_err.decode("utf-8", errors="ignore")
        out = result.std_out.decode("utf-8", errors="ignore").strip()[:200]
        ctx_msg = f" ({context})" if context else ""
        logger.error(
            f"PS encoded cmd error{ctx_msg}: code={result.status_code}, stderr={err}, stdout={out}"
        )
        session.run_ps(f"Remove-Item '{temp_path}' -ErrorAction SilentlyContinue")
        return None

    stdout = result.std_out.decode("utf-8", errors="ignore").strip()

    # If output was written to file, read it back in chunks
    if "FILE_WRITTEN" in stdout:
        logger.info(f"Reading output from temp file {temp_path}")
        all_content = ""
        offset = 0
        chunk_size = 50000
        while True:
            read_script = (
                f"$c = Get-Content '{temp_path}' -TotalCount {offset + chunk_size} -Encoding UTF8; "
                f"$c | Select-Object -Skip {offset} | Out-String"
            )
            chunk_result = session.run_ps(read_script)
            if chunk_result.status_code != 0:
                break
            chunk_text = chunk_result.std_out.decode("utf-8", errors="ignore")
            if not chunk_text.strip():
                break
            all_content += chunk_text
            if len(chunk_text.strip().split("\n")) < chunk_size:
                break
            offset += chunk_size

        session.run_ps(f"Remove-Item '{temp_path}' -ErrorAction SilentlyContinue")
        output = all_content.strip()
    else:
        output = stdout

    if not output or output in ("null", "[]"):
        return []
    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        logger.error(f"Output length: {len(output)}, preview: {output[:500]}")
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
