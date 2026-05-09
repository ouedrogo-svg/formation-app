from django.apps import AppConfig


class CoursesConfig(AppConfig):
    name = "courses"

    def ready(self):
        from . import signals  # noqa: F401
