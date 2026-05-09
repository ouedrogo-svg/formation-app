from django import template
from django.core.cache import cache

from courses.models import Course, Enrollment, Lesson

register = template.Library()

_ADMIN_INDEX_COUNTS_KEY = "courses:admin:index_counts"
_ADMIN_INDEX_COUNTS_TTL = 60


def _admin_index_counts():
    data = cache.get(_ADMIN_INDEX_COUNTS_KEY)
    if data is None:
        data = {
            "courses": Course.objects.count(),
            "lessons": Lesson.objects.count(),
            "enrollments": Enrollment.objects.count(),
        }
        cache.set(_ADMIN_INDEX_COUNTS_KEY, data, _ADMIN_INDEX_COUNTS_TTL)
    return data


@register.simple_tag
def courses_count():
    return _admin_index_counts()["courses"]


@register.simple_tag
def lessons_count():
    return _admin_index_counts()["lessons"]


@register.simple_tag
def enrollments_count():
    return _admin_index_counts()["enrollments"]
