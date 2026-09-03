"""PKCE (RFC 7636) helpers shared by the platform OAuth flows.

The verifier is deliberately generated once, at authorization-URL time, and
serialized into the signed OAuth ``state`` JWT so the callback can restore the
exact verifier when it exchanges the authorization code.  ``google-auth-oauthlib``
auto-generates a verifier when a Flow is built, which is the root of the
``invalid_grant: Missing code verifier`` failure when the callback builds a
*fresh* Flow: the new Flow's auto-generated verifier never matches the
``code_challenge`` that Google stored for the authorization code.
"""
import secrets
from string import ascii_letters, digits

# RFC 7636 §4.1: 43–128 chars from [A-Za-z0-9-._~].
PKCE_VERIFIER_MIN_LENGTH = 43
PKCE_VERIFIER_MAX_LENGTH = 128
_PKCE_VERIFIER_ALPHABET = ascii_letters + digits + "-._~"


def generate_code_verifier(length: int = PKCE_VERIFIER_MAX_LENGTH) -> str:
    """Return a fresh high-entropy code verifier (default 128 chars).

    ``secrets.choice`` is cryptographically secure and the character set is
    exactly the one RFC 7636 permits, so the verifier never needs escaping in
    the token request body.
    """
    if not PKCE_VERIFIER_MIN_LENGTH <= length <= PKCE_VERIFIER_MAX_LENGTH:
        raise ValueError(f"PKCE code verifier must be {PKCE_VERIFIER_MIN_LENGTH}-{PKCE_VERIFIER_MAX_LENGTH} characters")
    return "".join(secrets.choice(_PKCE_VERIFIER_ALPHABET) for _ in range(length))


def is_valid_code_verifier(value: str) -> bool:
    """True for a well-formed RFC 7636 verifier (length + character set)."""
    if not isinstance(value, str):
        return False
    if not PKCE_VERIFIER_MIN_LENGTH <= len(value) <= PKCE_VERIFIER_MAX_LENGTH:
        return False
    return all(char in _PKCE_VERIFIER_ALPHABET for char in value)
