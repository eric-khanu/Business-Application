# accounts/apps.py
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Accounts"

    def ready(self):
        # Import the signals module so all @receiver functions register
        # when the app loads. Without this, no signals fire.
        from . import signals  # noqa: F401