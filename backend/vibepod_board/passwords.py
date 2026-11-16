"""Password hashing with scrypt from the standard library, and the temporary passwords the
board hands out. Knows nothing about users or the database."""

import base64
import hashlib
import hmac
import secrets

# The length a password must have when a user chooses it.
MIN_LENGTH = 12
MAX_LENGTH = 128

_SCHEME = "scrypt"
_N = 2**14
_R = 8
_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32


def _encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    """`scrypt$<n>$<r>$<p>$<salt>$<key>`, with a fresh salt for every password."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_BYTES)
    return "$".join([_SCHEME, str(_N), str(_R), str(_P), _encode(salt), _encode(key)])


def verify_password(password: str, stored: str) -> bool:
    """Whether `password` is the one `stored` was made from. A stored value this module did
    not write matches nothing."""
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != _SCHEME:
            return False
        expected = base64.b64decode(key, validate=True)
        derived = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt, validate=True),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except ValueError:
        # Too few parts, a number that is none, or base64 that is none.
        return False
    return hmac.compare_digest(derived, expected)


# Checked when no user matches a sign-in, so that answer takes as long as a real check.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def generate_temporary_password() -> str:
    """16 URL-safe characters carrying 96 random bits."""
    return secrets.token_urlsafe(12)
