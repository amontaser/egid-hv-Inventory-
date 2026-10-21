import base64
import hashlib

from cryptography.fernet import Fernet

from tasks.collectors.winrm import _decrypt_password


def _secret_key_fernet(secret: str) -> Fernet:
    derived = base64.urlsafe_b64encode(
        hashlib.sha256((secret + "hyperv-inventory-encryption").encode()).digest()
    )
    return Fernet(derived)


def test_decrypt_with_secret_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "unit-test-secret")
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY_FILE", "/nonexistent/key.file")

    encrypted = _secret_key_fernet("unit-test-secret").encrypt(b"P@ssw0rd").decode()
    assert _decrypt_password(encrypted) == "P@ssw0rd"


def test_decrypt_with_legacy_key_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "a-different-secret")
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)

    legacy_key = Fernet.generate_key()
    key_file = tmp_path / "encryption_key.key"
    key_file.write_text(legacy_key.decode())
    monkeypatch.setenv("ENCRYPTION_KEY_FILE", str(key_file))

    encrypted = Fernet(legacy_key).encrypt(b"legacy-password").decode()
    assert _decrypt_password(encrypted) == "legacy-password"


def test_decrypt_with_encryption_key_env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "a-different-secret")
    monkeypatch.setenv("ENCRYPTION_KEY_FILE", "/nonexistent/key.file")

    legacy_key = Fernet.generate_key()
    monkeypatch.setenv("ENCRYPTION_KEY", legacy_key.decode())

    encrypted = Fernet(legacy_key).encrypt(b"env-password").decode()
    assert _decrypt_password(encrypted) == "env-password"


def test_decrypt_returns_none_when_no_key_matches(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "another-secret")
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY_FILE", "/nonexistent/key.file")

    encrypted = Fernet(Fernet.generate_key()).encrypt(b"nope").decode()
    assert _decrypt_password(encrypted) is None


def test_decrypt_returns_none_for_empty_password(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "unit-test-secret")
    assert _decrypt_password("") is None
