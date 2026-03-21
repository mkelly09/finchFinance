from django.apps import AppConfig


class HomeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'home'

    def ready(self):
        """
        Import signal handlers when the app is ready.
        This ensures all signals are registered and active.
        """
        import home.signals  # noqa
