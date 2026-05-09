from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Category,
    Course,
    CourseMonth,
    Enrollment,
    Lesson,
    MonthlyExamResult,
    Subscription,
    SubscriptionPricing,
)

admin.site.site_header = "Administration E-Learning"
admin.site.site_title = "Admin E-Learning"
admin.site.index_title = "Gestion des contenus"


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 1
    ordering = ("position",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(CourseMonth)
class CourseMonthAdmin(admin.ModelAdmin):
    list_display = ("category", "month")
    list_filter = ("category", "month")
    list_select_related = ("category",)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("title", "content_type", "month", "level", "created_at")
    list_filter = ("content_type", "level", "month__category", "month")
    search_fields = ("title", "short_description")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [LessonInline]
    list_select_related = ("month", "month__category")


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "position")
    list_filter = ("course",)
    search_fields = ("title", "content")
    ordering = ("course", "position")
    list_select_related = ("course",)


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("full_name", "email", "course", "trainer_validated", "amount_paid", "enrolled_at", "validated_at")
    list_filter = ("course", "trainer_validated", "enrolled_at")
    search_fields = ("full_name", "email")
    readonly_fields = ("enrolled_at",)
    list_select_related = ("course",)
    ordering = ("-enrolled_at",)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "trainer_approved", "start_at", "end_at", "status_badge")
    list_filter = ("plan", "trainer_approved", "trainer_rejected", "start_at", "end_at")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("created_at",)
    list_select_related = ("user", "category", "month")
    ordering = ("-end_at",)

    @admin.display(description="Statut")
    def status_badge(self, obj):
        if obj.trainer_rejected:
            return format_html('<span class="sub-badge sub-badge-expired">Refuse</span>')
        if not obj.trainer_approved and obj.is_active:
            return format_html('<span class="sub-badge sub-badge-pending">En attente</span>')
        if obj.is_active:
            return format_html('<span class="sub-badge sub-badge-active">Actif</span>')
        return format_html('<span class="sub-badge sub-badge-expired">Expire</span>')


@admin.register(SubscriptionPricing)
class SubscriptionPricingAdmin(admin.ModelAdmin):
    list_display = ("plan", "amount", "updated_at")
    list_filter = ("plan",)
    ordering = ("plan",)


@admin.register(MonthlyExamResult)
class MonthlyExamResultAdmin(admin.ModelAdmin):
    list_display = ("user", "month", "exam_course", "score", "total", "note_sur_20", "passed_at")
    list_filter = ("month__category", "month", "passed_at")
    search_fields = ("user__username", "user__first_name", "user__last_name", "exam_course__title")
    list_select_related = ("user", "month", "month__category", "exam_course")
    ordering = ("month__category__name", "month__month", "-note_sur_20", "-passed_at")
