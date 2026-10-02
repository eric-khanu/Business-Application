# accounts/services.py
"""
Business logic that spans models. Views and serializers call into here.
"""
import secrets

from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


# ---------------------------------------------------------------
# Temporary password generation
# ---------------------------------------------------------------
# Excludes ambiguous characters (0/O, 1/l/I) for readability.
TEMP_PASSWORD_ALPHABET = (
    "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
)
TEMP_PASSWORD_GROUPS = 4
TEMP_PASSWORD_GROUP_LEN = 4
TEMP_PASSWORD_SEPARATOR = "-"


def generate_temporary_password() -> str:
    """
    Generate a human-readable temporary password like 'Xk7m-Pq2v-N9sr-Wb4c'.
    Uses secrets for cryptographically secure randomness.
    """
    parts = []
    for _ in range(TEMP_PASSWORD_GROUPS):
        group = "".join(
            secrets.choice(TEMP_PASSWORD_ALPHABET)
            for _ in range(TEMP_PASSWORD_GROUP_LEN)
        )
        parts.append(group)
    return TEMP_PASSWORD_SEPARATOR.join(parts)


def reset_user_password(user, *, actor) -> str:
    """
    Reset user's password to a fresh temporary password.

    Returns the temporary password (plaintext, one-time display).
    """
    temp_password = generate_temporary_password()

    user.set_password(temp_password)
    user.must_change_password = True
    user.password_reset_by = actor
    user.password_reset_at = timezone.now()
    user.save(update_fields=[
        "password",
        "must_change_password",
        "password_reset_by",
        "password_reset_at",
        "updated_at",
    ])

    return temp_password


def clear_password_reset_flag(user) -> None:
    """
    Called after the user changes their password themselves.
    Clears the forced-change flag and reset metadata.
    """
    user.must_change_password = False
    user.password_reset_by = None
    user.password_reset_at = None
    user.save(update_fields=[
        "must_change_password",
        "password_reset_by",
        "password_reset_at",
        "updated_at",
    ])