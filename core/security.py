import secrets
from datetime import datetime, timedelta, timezone
import hashlib
import bcrypt  # Use native bcrypt directly

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jose import jwt
from core.config import settings

# ---------------------------------------------------------------------------
# AES-256-GCM credential encryption (for LinkedIn credentials at rest)
# ---------------------------------------------------------------------------

def _get_aes_key() -> bytes:
    """
    Derives the 32-byte AES key from the hex-encoded CREDENTIAL_ENCRYPTION_KEY env var.
    Raises a clear error if the key is missing or malformed. The key is read
    from environment/config ONLY and is never logged or hardcoded.
    """
    raw = settings.CREDENTIAL_ENCRYPTION_KEY
    if not raw:
        raise ValueError(
            "CREDENTIAL_ENCRYPTION_KEY is not set. Generate one with: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
    try:
        key_bytes = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(
            "CREDENTIAL_ENCRYPTION_KEY is malformed — expected a 64-character hex "
            "string (32 bytes). Generate one with: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        ) from exc
    if len(key_bytes) != 32:
        raise ValueError(
            "CREDENTIAL_ENCRYPTION_KEY must be exactly 32 bytes (64 hex characters)"
        )
    return key_bytes


def validate_encryption_key() -> None:
    """
    Eager startup check — call once during API/worker boot so a missing or
    malformed key fails LOUDLY at startup instead of silently corrupting or
    exposing credentials mid-request.
    """
    _get_aes_key()


def encrypt_credential(plaintext: str) -> str:
    """
    Encrypts a plaintext string using AES-256-GCM.

    Returns a single colon-separated string:  <nonce_hex>:<ciphertext_hex>
    The nonce is 12 bytes (96-bit), randomly generated per call.
    The ciphertext includes the GCM authentication tag appended by the library.
    """
    key = _get_aes_key()
    aesgcm = AESGCM(key)
    nonce = secrets.token_bytes(12)               # 96-bit nonce — recommended for GCM
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return f"{nonce.hex()}:{ciphertext.hex()}"


def decrypt_credential(encrypted: str) -> str:
    """
    Decrypts a value produced by encrypt_credential().
    Raises ValueError if the format is wrong or authentication tag fails.
    """
    try:
        nonce_hex, ct_hex = encrypted.split(":", 1)
    except ValueError:
        raise ValueError("Malformed encrypted credential — expected nonce:ciphertext")

    key = _get_aes_key()
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(bytes.fromhex(nonce_hex), bytes.fromhex(ct_hex), None)
    return plaintext.decode("utf-8")

def _get_clean_prehash(password: str) -> bytes:
    """
    Hashes any password down to deterministic bytes for native bcrypt compatibility.
    """
    return hashlib.sha256(password.encode("utf-8")).digest()


def _get_legacy_prehash(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("utf-8")

def hash_password(password: str) -> str:
    # 1. Get clean 64-byte hex representation
    prehashed = _get_clean_prehash(password)
    # 2. Generate salt and hash natively
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(prehashed, salt)
    # 3. Decode to standard string for database storage
    return hashed.decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        prehashed = _get_clean_prehash(plain_password)
        stored_hash = hashed_password.encode("utf-8")
        if bcrypt.checkpw(prehashed, stored_hash):
            return True
        return bcrypt.checkpw(_get_legacy_prehash(plain_password), stored_hash)
    except Exception:
        return False

def generate_5_digit_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(5))

def generate_token_id() -> str:
    return secrets.token_urlsafe(32)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    # Note: Using timezone-aware or UTC dates depending on your settings library configuration
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "token_type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "token_type": "refresh"})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def create_password_reset_token(email: str, token_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES
    )
    to_encode = {
        "sub": email,
        "jti": token_id,
        "exp": expire,
        "token_type": "password_reset",
    }
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
