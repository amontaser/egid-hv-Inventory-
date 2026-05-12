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
    """Create authenticated WinRM session. Credentials from DB (Fernet-encrypted)."""
    if not cluster_id:
        raise ValueError(
            "cluster_id is required to load credentials from the database."
        )

    username, password, transport, dns_servers, domain_name = _load_cluster_credentials(
        cluster_id
    )

    if not username or not password:
        raise ValueError(
            "No credentials found: configure this cluster with saved credentials."
        )

    resolved_host = _resolve_host(host, dns_servers, domain_name)

    return winrm.Session(
        target=resolved_host,
        auth=(username, password),
        server_cert_validation="ignore",
        transport=transport,
    )


def _resolve_host(host: str, dns_servers: str, domain_name: str = None) -> str:
    """Try system DNS first, fall back to cluster's configured DNS servers."""
    try:
        ip = socket.gethostbyname(host)
        logger.debug(f"System DNS resolved {host} -> {ip}")
        return host
    except socket.gaierror:
        pass

    if dns_servers and dns_servers.strip():
        ip = resolve_node_ip(host, dns_servers, domain_name)
        if ip and ip != host:
            logger.info(f"Resolved {host} -> {ip} via cluster DNS ({dns_servers})")
            return ip
        if not ip:
            logger.warning(f"Cluster DNS ({dns_servers}) failed to resolve {host}")

    logger.warning(f"Could not resolve {host} via system or cluster DNS")
    return host


def _load_cluster_credentials(cluster_id: int):
    """Load and decrypt credentials for a cluster from DB."""
    from app.db import get_db_connection

    from sqlalchemy import text

    session = get_db_connection()
    row = session.execute(
        text(
            "SELECT username, password, transport, require_https, dns_servers, domain_name FROM clusters WHERE id = :cluster_id"
        ),
        {"cluster_id": cluster_id},
    ).fetchone()

    if not row:
        return None, None, "ntlm", None, None

    username = row._mapping["username"]
    encrypted_pw = row._mapping["password"]
    transport = (
        "ssl"
        if row._mapping["require_https"]
        else (row._mapping["transport"] or "ntlm")
    )
    dns_servers = row._mapping["dns_servers"]
    domain_name = row._mapping["domain_name"]

    password = _decrypt_password(encrypted_pw)
    return username, password, transport, dns_servers, domain_name


def _decrypt_password(encrypted_pw: str) -> Optional[str]:
    try:
        import hashlib, base64
        from cryptography.fernet import Fernet

        secret = os.getenv("SECRET_KEY", "")
        if not secret:
            logger.error("SECRET_KEY is not set. Cannot decrypt password.")
            return None
        derived = base64.urlsafe_b64encode(
            hashlib.sha256((secret + "hyperv-inventory-encryption").encode()).digest()
        )
        f = Fernet(derived)
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
    """Run large PS scripts via PowerShell with temp-file output.
    Falls back to run_ps for scripts under 7000 bytes unless force_encoded=True."""
    script_bytes = script.encode("utf-8")
    if len(script_bytes) < 7000 and not force_encoded:
        return run_ps(session, script, context)

    import re
    import time as _time

    temp_name = f"hyperv_out_{int(_time.time())}_{os.getpid()}.json"
    temp_path = f"C:\\Windows\\Temp\\{temp_name}"

    wrapper = script
    match = re.search(r"ConvertTo-Json\b([^\n|;}]*)", wrapper)
    if match:
        wrapper = (
            wrapper[: match.start()]
            + f"{match.group(0)} | Out-File -FilePath '{temp_path}' -Encoding UTF8"
            + wrapper[match.end() :]
        )

    logger.info(f"Using PowerShell with temp file ({len(wrapper)} chars)")

    try:
        result = session.run_ps(
            f"Set-Item WSMan:\\localhost\\MaxEnvelopeSizeKB 8192 -ErrorAction SilentlyContinue; "
            + wrapper
        )
    except Exception as e:
        logger.error(f"Failed to execute command: {e}")
        return None

    if result.status_code != 0:
        err = result.std_err.decode("utf-8", errors="ignore")
        ctx_msg = f" ({context})" if context else ""
        logger.warning(
            f"PS error{ctx_msg}: code={result.status_code}, stderr={err}, trying run_ps without temp file"
        )
        session.run_ps(f"Remove-Item '{temp_path}' -ErrorAction SilentlyContinue")
        return run_ps(session, script, context)

    stdout = result.std_out.decode("utf-8", errors="ignore").strip()

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

    dns_servers = [s.strip() for s in dns_servers_str.split(",") if s.strip()]
    if not dns_servers:
        return node_name

    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = dns_servers
    resolver.timeout = 5
    resolver.lifetime = 10

    already_fqdn = "." in node_name

    if domain_name and not already_fqdn:
        try:
            ans = resolver.resolve(f"{node_name}.{domain_name}", "A")
            ip = str(ans[0])
            logger.info(f"Cluster DNS resolved {node_name}.{domain_name} -> {ip}")
            return ip
        except Exception as e:
            logger.warning(f"Cluster DNS failed for {node_name}.{domain_name}: {e}")

    try:
        ans = resolver.resolve(node_name, "A")
        ip = str(ans[0])
        logger.info(f"Cluster DNS resolved {node_name} -> {ip}")
        return ip
    except Exception as e:
        logger.warning(f"Cluster DNS failed for {node_name}: {e}")

    return node_name
