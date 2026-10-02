# accounts/context_processors.py
"""
Context processors for the accounts app.

Exposes tenant + role information to every template so you don't have
to pass it manually in every view.

Usage in templates:
    {{ current_business.name }}
    {{ current_business.currency }}
    {% if is_owner %}...{% endif %}
    {% if is_manager_or_above %}...{% endif %}
    {% if is_salesperson %}...{% endif %}
"""

from django.contrib.auth import get_user_model

User = get_user_model()


def _get_business(user):
    """
    Return the user's business or None, caching on the User instance.
    The FK access is cached by Django anyway, but we guard against
    anonymous / orphan users.
    """
    if not user or not user.is_authenticated:
        return None
    if not getattr(user, "business_id", None):
        return None
    # Django caches the related object on the instance after first access,
    # so this doesn't hit the DB again inside the same request.
    return user.business


def business_context(request):
    """
    Injects tenant + role context into every template.

    Keys exposed:
        current_business      -> Business instance or None
        current_user_role     -> str or None ('owner' | 'manager' | 'salesperson')
        is_owner              -> bool
        is_manager_or_above   -> bool
        is_manager            -> bool  (alias of is_manager_or_above)
        is_salesperson        -> bool
        business_currency     -> str or None (convenience for formatting)
    """
    user = getattr(request, "user", None)
    biz = _get_business(user)

    if not biz:
        return {
            "current_business": None,
            "current_user_role": None,
            "is_owner": False,
            "is_manager_or_above": False,
            "is_manager": False,
            "is_salesperson": False,
            "business_currency": None,
        }

    return {
        "current_business": biz,
        "current_user_role": user.role,
        "is_owner": user.is_owner,
        "is_manager_or_above": user.is_manager_or_above,
        "is_manager": user.is_manager,  # alias, kept for backwards compat
        "is_salesperson": user.is_salesperson,
        "business_currency": biz.currency,
    }