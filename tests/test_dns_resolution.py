"""Test DNS resolution helper for cluster nodes."""
import pytest
import sys
sys.path.insert(0, "/opt/hyperv_inventory")


def test_resolve_uses_custom_dns_servers(mocker):
    """resolve_node_ip uses the configured DNS servers, not system DNS."""
    mock_resolver_class = mocker.patch("dns.resolver.Resolver")
    mock_resolver = mock_resolver_class.return_value
    mock_answer = mocker.MagicMock()
    mock_answer.__str__ = lambda self: "10.21.0.15"
    mock_resolver.resolve.return_value = [mock_answer]

    from tasks.sync import resolve_node_ip

    ip = resolve_node_ip("NODE1", "10.21.0.10,10.21.0.11", "egitdr.corp")

    assert mock_resolver.nameservers == ["10.21.0.10", "10.21.0.11"]
    mock_resolver.resolve.assert_called_with("NODE1.egitdr.corp", "A")


def test_resolve_falls_back_to_short_name(mocker):
    """Falls back to short name if FQDN resolution fails."""
    mock_resolver_class = mocker.patch("dns.resolver.Resolver")
    mock_resolver = mock_resolver_class.return_value
    mock_answer = mocker.MagicMock()
    mock_answer.__str__ = lambda self: "10.21.0.15"

    mock_resolver.resolve.side_effect = [Exception("NXDOMAIN"), [mock_answer]]

    from tasks.sync import resolve_node_ip

    ip = resolve_node_ip("NODE1", "10.21.0.10", "egitdr.corp")
    assert mock_resolver.resolve.call_count == 2


def test_resolve_returns_hostname_when_all_fail(mocker):
    """Returns the node name as-is when all DNS queries fail."""
    mock_resolver_class = mocker.patch("dns.resolver.Resolver")
    mock_resolver = mock_resolver_class.return_value
    mock_resolver.resolve.side_effect = Exception("timeout")

    from tasks.sync import resolve_node_ip

    result = resolve_node_ip("NODE1", "10.21.0.10", None)
    assert result == "NODE1"


def test_resolve_uses_system_dns_when_no_servers(mocker):
    """Uses socket.gethostbyname when no custom DNS servers are configured."""
    mock_gethostbyname = mocker.patch("socket.gethostbyname", return_value="10.21.0.20")

    from tasks.sync import resolve_node_ip

    result = resolve_node_ip("NODE1", "", None)
    mock_gethostbyname.assert_called_once_with("NODE1")
    assert result == "10.21.0.20"
