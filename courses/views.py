from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import user_passes_test
from django.core.cache import cache
from django.db.models import OuterRef, Prefetch, Q, Subquery
from decimal import Decimal, InvalidOperation

from django.http import FileResponse, Http404, HttpResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.utils.text import slugify
from datetime import datetime, timedelta

from .forms import (
    CategoryForm,
    CourseMonthForm,
    EnrollmentForm,
    LessonForm,
    MonthlyExamForm,
    SignUpForm,
    SubscriptionForm,
    SubscriptionPricingForm,
    TrainerCourseForm,
    TrainerLessonForm,
)
from .models import (
    Category,
    Course,
    CourseMonth,
    CourseQuizQuestion,
    Enrollment,
    Lesson,
    MonthlyExam,
    MonthlyExamResult,
    Subscription,
    SubscriptionPricing,
)
from .pdf_quiz import rebuild_course_quiz, rebuild_monthly_exam_quiz


def _user_email(user):
    if user.is_authenticated and user.email:
        return user.email
    return ""


def _user_nom_prenom(user):
    """Nom puis prenom (User Django), ou identifiant si les deux sont vides."""
    ln = (user.last_name or "").strip()
    fn = (user.first_name or "").strip()
    if ln or fn:
        return f"{ln} {fn}".strip()
    return user.username


def _active_subscriptions_queryset(user, now=None):
    if not user.is_authenticated:
        return Subscription.objects.none()
    if now is None:
        now = timezone.now()
    return Subscription.objects.filter(user=user, end_at__gte=now, trainer_approved=True)


def _active_subscription(user):
    return _active_subscriptions_queryset(user).order_by("-end_at").first()


def _active_subscription_for_course(user, course):
    subscriptions = _active_subscriptions_queryset(user)
    if not course.month:
        return subscriptions.order_by("-end_at").first()
    return (
        subscriptions.filter(
            (
                Q(
                    plan=Subscription.PLAN_MONTHLY,
                    month=course.month,
                )
            )
            | (
                Q(
                    plan=Subscription.PLAN_YEARLY,
                    category=course.month.category,
                )
            )
        )
        .order_by("-end_at")
        .first()
    )


def _pending_subscription_for_course(user, course):
    if not user.is_authenticated or user.is_staff or not course.month:
        return None
    now = timezone.now()
    pending = Subscription.objects.filter(
        user=user,
        end_at__gte=now,
        trainer_approved=False,
        trainer_rejected=False,
    )
    return (
        pending.filter(
            (
                Q(plan=Subscription.PLAN_MONTHLY, month=course.month)
                | Q(plan=Subscription.PLAN_YEARLY, category=course.month.category)
            )
        )
        .order_by("-end_at")
        .first()
    )


def _has_course_access(user, course):
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    return _active_subscription_for_course(user, course) is not None


def _generate_unique_slug(title):
    """Generate unique slug using efficient database query instead of Python filtering."""
    base_slug = slugify(title) or "cours"
    prefix = f"{base_slug}-"
    
    # Check if base slug exists
    if not Course.objects.filter(slug=base_slug).exists():
        return base_slug
    
    # Find highest numbered variant efficiently
    from django.db.models import Value, CharField
    from django.db.models.functions import Substr
    
    numbered_variants = Course.objects.filter(
        slug__startswith=prefix
    ).values_list('slug', flat=True)
    
    max_index = 1
    for slug in numbered_variants:
        suffix = slug[len(prefix):]
        if suffix.isdigit():
            max_index = max(max_index, int(suffix))
    
    return f"{base_slug}-{max_index + 1}"


def _format_form_errors(form, label):
    errors = []
    for field_name, field_errors in form.errors.items():
        field_label = "general" if field_name == "__all__" else field_name
        for error in field_errors:
            errors.append(f"{label}.{field_label}: {error}")
    return errors


def _parse_decimal_amount(raw):
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return Decimal(s.replace(",", "."))
    except InvalidOperation:
        return None


def _parse_subscription_datetime(raw):
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _note_sur_20(score, total):
    if not total:
        return Decimal("0.00")
    note = (Decimal(score) * Decimal("20")) / Decimal(total)
    return note.quantize(Decimal("0.01"))


def _first_exam_results_queryset():
    """
    Return only the first validated result per (month, user).
    Candidates can retry exams, but ranking keeps the first attempt only.
    """
    latest_result_id_subquery = (
        MonthlyExamResult.objects.filter(month=OuterRef("month"), user=OuterRef("user"))
        .order_by("passed_at", "id")
        .values("id")[:1]
    )
    return (
        MonthlyExamResult.objects.filter(id=Subquery(latest_result_id_subquery))
        .select_related("month", "month__category", "user", "exam_course")
        .order_by(
            "month__category__name",
            "month__month",
            "-note_sur_20",
            "user__last_name",
            "user__first_name",
        )
    )


def _default_monthly_pricing_amount():
    cache_key = "courses:pricing:monthly"
    amount = cache.get(cache_key)
    if amount is not None:
        return amount
    
    row = SubscriptionPricing.objects.filter(plan=Subscription.PLAN_MONTHLY).first()
    amount = row.amount if row else None
    cache.set(cache_key, amount, 3600)  # Cache for 1 hour
    return amount


def _get_subscription_pricing_dict():
    """Get all subscription pricing as a dict, cached."""
    cache_key = "courses:pricing:all"
    pricing_dict = cache.get(cache_key)
    if pricing_dict is not None:
        return pricing_dict
    
    pricing_dict = dict(SubscriptionPricing.objects.values_list("plan", "amount"))
    cache.set(cache_key, pricing_dict, 3600)  # Cache for 1 hour
    return pricing_dict


def _invalidate_pricing_cache():
    """Invalidate pricing cache."""
    cache.delete("courses:pricing:monthly")
    cache.delete("courses:pricing:all")


def _get_cached_home_catalog():
    cache_key = "courses:home:catalog:v2"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached["categories"], cached["months"]

    categories = list(Category.objects.filter(months__courses__isnull=False).distinct())
    months = list(
        CourseMonth.objects.select_related("category")
        .filter(courses__isnull=False)
        .distinct()
    )
    cache.set(cache_key, {"categories": categories, "months": months}, 300)
    return categories, months


def _invalidate_home_catalog_cache():
    cache.delete("courses:home:catalog:v1")
    cache.delete("courses:home:catalog:v2")


def _months_for_category_from_catalog(months, category_id):
    return sorted(
        [month for month in months if month.category_id == category_id],
        key=lambda month: month.month,
    )


@user_passes_test(lambda u: u.is_staff, login_url="login")
def trainer_page(request):
    category_form = CategoryForm(prefix="category")
    month_form = CourseMonthForm(prefix="month")
    course_form = TrainerCourseForm(prefix="course")
    course_lesson_form = TrainerLessonForm(prefix="course_lesson")
    pricing_form = SubscriptionPricingForm(prefix="pricing")
    exam_form = MonthlyExamForm(prefix="exam")
    default_inscription_amount = _default_monthly_pricing_amount()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_category":
            category_form = CategoryForm(request.POST, prefix="category")
            if category_form.is_valid():
                category_form.save()
                _invalidate_home_catalog_cache()
                messages.success(request, "Categorie creee avec succes.")
                return redirect("formateur")
        elif action == "create_month":
            month_form = CourseMonthForm(request.POST, prefix="month")
            if month_form.is_valid():
                month_form.save()
                _invalidate_home_catalog_cache()
                messages.success(request, "Mois ajoute avec succes.")
                return redirect("formateur")
        elif action == "create_course":
            course_form = TrainerCourseForm(request.POST, request.FILES, prefix="course")
            course_lesson_form = TrainerLessonForm(request.POST, prefix="course_lesson")
            if course_form.is_valid() and course_lesson_form.is_valid():
                course = course_form.save(commit=False)
                course.slug = _generate_unique_slug(course.title)
                course.short_description = f"Cours: {course.title}"
                course.description = f"Description du cours: {course.title}"
                course.level = "beginner"
                course.save()

                lesson = course_lesson_form.save(commit=False)
                lesson.course = course
                lesson.title = f"Introduction - {course.title}"
                lesson.position = 1
                lesson.content = f"Contenu introductif pour le cours {course.title}."
                lesson.video_url = ""
                lesson.save()
                _invalidate_home_catalog_cache()

                generated_count = 0
                if course.quiz_enabled and course.pdf_file:
                    generated_count = rebuild_course_quiz(course)

                messages.success(
                    request,
                    f"{course.get_content_type_display()} cree(e) avec succes. Premiere lecon creee avec succes. "
                    + (
                        f"Quiz genere automatiquement ({generated_count} question(s))."
                        if generated_count > 0
                        else (
                            "Seules les corrections avec PDF sont converties en quiz."
                            if course.pdf_file
                            else "Ajoutez un PDF si vous souhaitez permettre la lecture du document."
                        )
                    ),
                )
                return redirect("formateur")
            error_details = _format_form_errors(course_form, "cours") + _format_form_errors(
                course_lesson_form, "lecon"
            )
            if error_details:
                messages.error(request, "Creation echouee: " + " | ".join(error_details))
            else:
                messages.error(
                    request,
                    "Creation echouee. Verifiez le titre du cours et que le fichier est bien un PDF.",
                )
        elif action == "create_lesson":
            lesson_form = LessonForm(request.POST, prefix="lesson")
            if lesson_form.is_valid():
                lesson_form.save()
                messages.success(request, "Lecon ajoutee avec succes.")
                return redirect("formateur")
            messages.error(request, "Ajout de lecon echoue. Verifiez les champs du formulaire.")
        elif action == "set_subscription_pricing":
            pricing_form = SubscriptionPricingForm(request.POST, prefix="pricing")
            if pricing_form.is_valid():
                plan = pricing_form.cleaned_data["plan"]
                amount = pricing_form.cleaned_data["amount"]
                SubscriptionPricing.objects.update_or_create(
                    plan=plan,
                    defaults={"amount": amount},
                )
                _invalidate_pricing_cache()  # Invalidate pricing cache
                messages.success(request, "Tarif d'abonnement enregistre avec succes.")
                return redirect("formateur")
            pricing_errors = _format_form_errors(pricing_form, "tarif")
            if pricing_errors:
                messages.error(request, "Enregistrement du tarif echoue: " + " | ".join(pricing_errors))
            else:
                messages.error(request, "Enregistrement du tarif echoue. Verifiez le formulaire.")
        elif action == "approve_subscription":
            subscription_id = request.POST.get("subscription_id", "").strip()
            if subscription_id.isdigit():
                subscription = get_object_or_404(
                    Subscription.objects.select_related("user", "category", "month", "month__category"),
                    pk=int(subscription_id),
                    trainer_approved=False,
                )
                plan = request.POST.get("plan", "").strip()
                if plan not in (Subscription.PLAN_MONTHLY, Subscription.PLAN_YEARLY):
                    plan = subscription.plan
                if plan not in (Subscription.PLAN_MONTHLY, Subscription.PLAN_YEARLY):
                    messages.error(request, "Plan d'abonnement invalide.")
                    return redirect("formateur")

                category = None
                month = None
                if plan == Subscription.PLAN_MONTHLY:
                    month_id = request.POST.get("month_id", "").strip()
                    if not month_id.isdigit() and subscription.month_id:
                        month_id = str(subscription.month_id)
                    if not month_id.isdigit():
                        messages.error(
                            request,
                            "Pour un abonnement mensuel, selectionnez le mois de cours.",
                        )
                        return redirect("formateur")
                    month = get_object_or_404(
                        CourseMonth.objects.select_related("category"),
                        pk=int(month_id),
                    )
                    category = month.category
                else:
                    category_id = request.POST.get("category_id", "").strip()
                    if not category_id.isdigit() and subscription.category_id:
                        category_id = str(subscription.category_id)
                    if not category_id.isdigit():
                        messages.error(
                            request,
                            "Pour un abonnement annuel, selectionnez une categorie.",
                        )
                        return redirect("formateur")
                    category = get_object_or_404(Category, pk=int(category_id))

                start_raw = request.POST.get("start_at", "").strip()
                end_raw = request.POST.get("end_at", "").strip()
                start_at = _parse_subscription_datetime(start_raw) if start_raw else subscription.start_at
                if start_raw and start_at is None:
                    start_at = subscription.start_at
                end_at = _parse_subscription_datetime(end_raw) if end_raw else subscription.end_at
                if end_raw and end_at is None:
                    end_at = subscription.end_at
                if start_at is None or end_at is None:
                    messages.error(
                        request,
                        "Dates de debut ou de fin invalides ou manquantes.",
                    )
                    return redirect("formateur")
                if end_at < start_at:
                    messages.error(
                        request,
                        "La date de fin doit etre posterieure ou egale a la date de debut.",
                    )
                    return redirect("formateur")

                subscription.plan = plan
                subscription.category = category
                subscription.month = month
                subscription.start_at = start_at
                subscription.end_at = end_at
                subscription.trainer_rejected = False
                subscription.trainer_approved = True
                subscription.save(
                    update_fields=[
                        "plan",
                        "category",
                        "month",
                        "start_at",
                        "end_at",
                        "trainer_rejected",
                        "trainer_approved",
                    ]
                )
                messages.success(
                    request,
                    f"Abonnement valide pour {subscription.user.username}. L'etudiant peut acceder au contenu.",
                )
            return redirect("formateur")
        elif action == "refuse_subscription":
            subscription_id = request.POST.get("subscription_id", "").strip()
            if subscription_id.isdigit():
                subscription = get_object_or_404(
                    Subscription.objects.select_related("user"),
                    pk=int(subscription_id),
                )
                if subscription.trainer_approved:
                    messages.error(
                        request,
                        "Impossible de refuser un abonnement deja valide. Utilisez supprimer si necessaire.",
                    )
                else:
                    subscription.trainer_rejected = True
                    subscription.save(update_fields=["trainer_rejected"])
                    messages.success(
                        request,
                        f"Demande d'abonnement refusee pour {_user_nom_prenom(subscription.user)}.",
                    )
            return redirect("formateur")
        elif action == "delete_subscription":
            subscription_id = request.POST.get("subscription_id", "").strip()
            if subscription_id.isdigit():
                subscription = get_object_or_404(
                    Subscription.objects.select_related("user"),
                    pk=int(subscription_id),
                )
                label = _user_nom_prenom(subscription.user)
                subscription.delete()
                messages.success(request, f"Abonnement supprime ({label}).")
            return redirect("formateur")
        elif action == "validate_enrollment":
            enrollment_id = request.POST.get("enrollment_id", "").strip()
            if enrollment_id.isdigit():
                enrollment = get_object_or_404(
                    Enrollment.objects.select_related("course", "course__month", "course__month__category"),
                    pk=int(enrollment_id),
                    trainer_validated=False,
                )
                amount = _parse_decimal_amount(request.POST.get("amount"))
                if amount is None:
                    amount = default_inscription_amount
                enrollment.trainer_validated = True
                enrollment.validated_at = timezone.now()
                enrollment.amount_paid = amount
                enrollment.save(update_fields=["trainer_validated", "validated_at", "amount_paid"])
                messages.success(
                    request,
                    f"Inscription validee : {enrollment.full_name} — {enrollment.course.title}.",
                )
            return redirect("formateur")
        elif action == "regenerate_course_quiz":
            course_id = request.POST.get("course_id", "").strip()
            if course_id.isdigit():
                course = get_object_or_404(Course, pk=int(course_id))
                if not course.quiz_enabled:
                    messages.info(
                        request,
                        "Le quiz est reserve aux corrections. Convertissez ce contenu en correction pour generer un quiz.",
                    )
                    return redirect("formateur")
                if not course.pdf_file:
                    messages.error(
                        request,
                        "Impossible de regenerer le quiz de ce cours: aucun fichier PDF n'est associe.",
                    )
                    return redirect("formateur")
                count = rebuild_course_quiz(course)
                if count > 0:
                    messages.success(
                        request,
                        f"Quiz regenere pour « {course.title} » ({count} question(s)).",
                    )
                else:
                    messages.info(
                        request,
                        "Aucune question regeneree. Verifiez le format du PDF "
                        "(questions + options + section Reponses).",
                    )
            return redirect("formateur")
        elif action == "create_exam":
            exam_form = MonthlyExamForm(request.POST, request.FILES, prefix="exam")
            if exam_form.is_valid():
                exam = exam_form.save()
                generated_count = 0
                if exam.pdf_file:
                    generated_count = rebuild_monthly_exam_quiz(exam)
                if generated_count > 0:
                    messages.success(
                        request,
                        f"Examen cree pour {exam.month}. Quiz genere automatiquement ({generated_count} question(s)).",
                    )
                elif exam.pdf_file:
                    messages.warning(
                        request,
                        "Examen cree, mais aucune question n'a ete generee automatiquement depuis le PDF. "
                        "Verifiez le contenu/format du document puis utilisez 'Regenerer quiz'.",
                    )
                else:
                    messages.success(
                        request,
                        f"Examen cree pour {exam.month}. Ajoutez un PDF pour generer le quiz automatiquement.",
                    )
                return redirect("formateur")
            messages.error(request, "Création d'examen échouée. Vérifiez les champs.")
        elif action == "update_exam":
            exam_id = request.POST.get("exam_id", "").strip()
            if exam_id.isdigit():
                exam = get_object_or_404(MonthlyExam, pk=int(exam_id))
                exam_form = MonthlyExamForm(request.POST, prefix="exam", instance=exam)
                if exam_form.is_valid():
                    exam_form.save()
                    messages.success(request, f"Examen mis à jour pour {exam.month}.")
                    return redirect("formateur")
                messages.error(request, "Mise à jour d'examen échouée. Vérifiez les champs.")
        elif action == "delete_exam":
            exam_id = request.POST.get("exam_id", "").strip()
            if exam_id.isdigit():
                exam = get_object_or_404(MonthlyExam, pk=int(exam_id))
                month_name = str(exam.month)
                exam.delete()
                messages.success(request, f"Examen supprimé pour {month_name}.")
            return redirect("formateur")
        elif action == "regenerate_exam_quiz":
            exam_id = request.POST.get("exam_id", "").strip()
            if exam_id.isdigit():
                exam = get_object_or_404(MonthlyExam, pk=int(exam_id))
                if not exam.pdf_file:
                    messages.error(
                        request,
                        "Impossible de regenerer le quiz de cet examen: aucun PDF n'est charge. "
                        "Ouvrez 'Editer' puis ajoutez un PDF, ou saisissez les questions manuellement.",
                    )
                    return redirect("formateur")
                count = rebuild_monthly_exam_quiz(exam)
                if count > 0:
                    messages.success(
                        request,
                        f"Examen « {exam.title} » regenere ({count} question(s)).",
                    )
                else:
                    messages.info(
                        request,
                        "Aucune question regeneree. Verifiez que le PDF est charge et "
                        "au bon format (questions + options + section Reponses).",
                    )
            return redirect("formateur")
        elif action == "upload_and_regenerate_exam_quiz":
            exam_id = request.POST.get("exam_id", "").strip()
            if exam_id.isdigit():
                exam = get_object_or_404(MonthlyExam, pk=int(exam_id))
                uploaded_pdf = request.FILES.get("exam_pdf_file")
                if not uploaded_pdf:
                    messages.error(
                        request,
                        "Aucun fichier PDF recu. Selectionnez un PDF puis relancez l'action.",
                    )
                    return redirect("formateur")

                exam.pdf_file = uploaded_pdf
                exam.save(update_fields=["pdf_file"])
                count = rebuild_monthly_exam_quiz(exam)
                if count > 0:
                    messages.success(
                        request,
                        f"PDF charge et quiz regenere pour « {exam.title} » ({count} question(s)).",
                    )
                else:
                    messages.info(
                        request,
                        "PDF charge, mais aucune question n'a ete extraite. "
                        "Verifiez que le PDF contient du texte lisible/selectable.",
                    )
            return redirect("formateur")

    recent_courses = Course.objects.select_related("month", "month__category").order_by("-created_at")[:5]
    recent_lessons = Lesson.objects.select_related("course").order_by("-id")[:5]
    pricing_list = list(SubscriptionPricing.objects.all())
    now = timezone.now()
    pricing_by_plan = _get_subscription_pricing_dict()  # Use cached pricing
    trainer_subscriptions = list(
        Subscription.objects.select_related("user", "category", "month", "month__category")
        .order_by("-created_at")[:500]
    )
    subscriptions_total = Decimal("0")
    for sub in trainer_subscriptions:
        row_amount = pricing_by_plan.get(sub.plan)
        sub.row_amount = row_amount
        if row_amount is not None and sub.trainer_approved:
            subscriptions_total += row_amount
    trainer_subscription_months = list(
        CourseMonth.objects.select_related("category").order_by("category__name", "month")
    )
    trainer_subscription_categories = list(Category.objects.order_by("name"))
    pending_enrollments = list(
        Enrollment.objects.filter(trainer_validated=False)
        .select_related("course", "course__month", "course__month__category")
        .order_by("-enrolled_at")[:500]
    )
    validated_enrollments = list(
        Enrollment.objects.filter(trainer_validated=True)
        .select_related("course", "course__month", "course__month__category")
        .order_by("-validated_at", "-id")[:500]
    )
    exam_results = list(_first_exam_results_queryset())
    exam_rankings = []
    current_month_id = None
    month_group = None
    rank = 0
    for row in exam_results:
        if row.month_id != current_month_id:
            current_month_id = row.month_id
            rank = 0
            month_group = {
                "month": row.month,
                "rows": [],
            }
            exam_rankings.append(month_group)
        rank += 1
        month_group["rows"].append(
            {
                "rank": rank,
                "candidate_last_name": (row.user.last_name or "").strip() or "-",
                "candidate_first_name": (row.user.first_name or "").strip() or row.user.username,
                "note_sur_20": row.note_sur_20,
                "score": row.score,
                "total": row.total,
                "exam_title": row.exam_course.title if row.exam_course else "-",
                "passed_at": row.passed_at,
            }
        )
    monthly_exams = list(
        MonthlyExam.objects.select_related("month", "month__category")
        .order_by("month__category__name", "month__month")
    )
    return render(
        request,
        "courses/formateur.html",
        {
            "course_form": course_form,
            "course_lesson_form": course_lesson_form,
            "category_form": category_form,
            "month_form": month_form,
            "pricing_form": pricing_form,
            "exam_form": exam_form,
            "pricing_list": pricing_list,
            "trainer_subscriptions": trainer_subscriptions,
            "subscriptions_total": subscriptions_total,
            "now": now,
            "trainer_subscription_months": trainer_subscription_months,
            "trainer_subscription_categories": trainer_subscription_categories,
            "pending_enrollments": pending_enrollments,
            "validated_enrollments": validated_enrollments,
            "exam_rankings": exam_rankings,
            "monthly_exams": monthly_exams,
            "default_inscription_amount": default_inscription_amount,
            "recent_courses": recent_courses,
            "recent_lessons": recent_lessons,
        },
    )


@user_passes_test(lambda u: u.is_staff, login_url="login")
def trainer_enrollments_export_xlsx(request):
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Inscriptions validees"
    ws.append(["Nom", "Email", "Categorie", "Mois", "Montant", "Cours", "Date validation"])
    rows = (
        Enrollment.objects.filter(trainer_validated=True)
        .select_related("course", "course__month", "course__month__category")
        .order_by("-validated_at", "-id")
        .iterator(chunk_size=1000)
    )
    for enrollment in rows:
        if enrollment.course.month:
            category_name = enrollment.course.month.category.name
            month_label = enrollment.course.month.get_month_display()
        else:
            category_name = ""
            month_label = ""
        amount = enrollment.amount_paid if enrollment.amount_paid is not None else ""
        validated = enrollment.validated_at.strftime("%d/%m/%Y %H:%M") if enrollment.validated_at else ""
        ws.append(
            [
                enrollment.full_name,
                enrollment.email,
                category_name,
                month_label,
                amount,
                enrollment.course.title,
                validated,
            ]
        )

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"inscriptions_validees_{timezone.now().strftime('%Y%m%d_%H%M')}.xlsx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@user_passes_test(lambda u: u.is_staff, login_url="login")
def trainer_subscriptions_export_xlsx(request):
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Abonnements"
    pricing = _get_subscription_pricing_dict()  # Use cached pricing
    ws.append(
        [
            "Nom et prenom",
            "Email",
            "Plan",
            "Categorie",
            "Mois",
            "Valide formateur",
            "Debut",
            "Fin",
            "Cree le",
            "Montant",
        ]
    )
    rows = (
        Subscription.objects.select_related("user", "category", "month", "month__category")
        .order_by("-created_at")
        .iterator(chunk_size=1000)
    )
    export_total = Decimal("0")
    for sub in rows:
        plan_label = sub.get_plan_display()
        if sub.plan == Subscription.PLAN_MONTHLY and sub.month:
            category_name = sub.month.category.name
            month_label = sub.month.get_month_display()
        elif sub.plan == Subscription.PLAN_YEARLY and sub.category:
            category_name = sub.category.name
            month_label = ""
        else:
            category_name = sub.category.name if sub.category else ""
            month_label = sub.month.get_month_display() if sub.month else ""

        if sub.trainer_approved:
            approved = "Oui"
        elif sub.trainer_rejected:
            approved = "Refuse"
        else:
            approved = "Non"
        start_s = sub.start_at.strftime("%d/%m/%Y %H:%M") if sub.start_at else ""
        end_s = sub.end_at.strftime("%d/%m/%Y %H:%M") if sub.end_at else ""
        created_s = sub.created_at.strftime("%d/%m/%Y %H:%M") if sub.created_at else ""
        row_price = pricing.get(sub.plan)
        if row_price is not None:
            montant_cell = row_price
            if sub.trainer_approved:
                export_total += row_price
        else:
            montant_cell = ""
        ws.append(
            [
                _user_nom_prenom(sub.user),
                sub.user.email or "",
                plan_label,
                category_name,
                month_label,
                approved,
                start_s,
                end_s,
                created_s,
                montant_cell,
            ]
        )
    ws.append(
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "Montant total (abonnements valides)",
            export_total,
        ]
    )

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"abonnements_{timezone.now().strftime('%Y%m%d_%H%M')}.xlsx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def signup(request):
    if request.user.is_authenticated:
        return redirect("home")

    form = SignUpForm()
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, "Compte cree avec succes.")
            return redirect("home")

    return render(request, "registration/signup.html", {"form": form})


@never_cache
def home(request):
    category_id = request.GET.get("category", "").strip()
    month_id = request.GET.get("month", "").strip()
    if not category_id.isdigit():
        category_id = ""
    if not month_id.isdigit():
        month_id = ""

    categories, months = _get_cached_home_catalog()
    categories_by_id = {str(c.id): c for c in categories}
    months_by_id = {str(m.id): m for m in months}
    selected_category = None
    selected_month = None
    months_for_selected_category = []
    subjects_for_selected_month = []
    corrections_for_selected_month = []
    selected_month_active_subscription = None
    selected_month_pending_subscription = None
    selected_month_subscription_form = SubscriptionForm()
    selected_month_pricing = _get_subscription_pricing_dict()  # Use cached pricing

    if category_id:
        selected_category = categories_by_id.get(category_id)
        if selected_category:
            months_for_selected_category = _months_for_category_from_catalog(
                months, selected_category.id
            )

    if month_id:
        selected_month = months_by_id.get(month_id)
        if selected_month and not selected_category:
            selected_category = selected_month.category
            months_for_selected_category = _months_for_category_from_catalog(
                months, selected_category.id
            )
        if selected_month and selected_category and selected_month.category_id == selected_category.id:
            if request.user.is_authenticated and not request.user.is_staff:
                now = timezone.now()
                selected_month_active_subscription = _active_subscriptions_queryset(
                    request.user, now=now
                ).filter(
                    plan=Subscription.PLAN_MONTHLY,
                    month=selected_month,
                ).order_by("-end_at").first()
                selected_month_pending_subscription = Subscription.objects.filter(
                    user=request.user,
                    plan=Subscription.PLAN_MONTHLY,
                    month=selected_month,
                    end_at__gte=now,
                    trainer_approved=False,
                    trainer_rejected=False,
                ).order_by("-end_at").first()
                if request.method == "POST" and request.POST.get("action") == "subscribe_month":
                    selected_month_subscription_form = SubscriptionForm(request.POST)
                    if selected_month_subscription_form.is_valid():
                        plan = selected_month_subscription_form.cleaned_data["plan"]
                        if plan != Subscription.PLAN_MONTHLY:
                            messages.error(
                                request,
                                "Depuis cette page, seul l'abonnement mensuel au mois selectionne est autorise.",
                            )
                            return redirect(f"{reverse('home')}?category={selected_category.id}&month={selected_month.id}")
                        if plan not in selected_month_pricing:
                            messages.error(
                                request,
                                "Le montant de l'abonnement mensuel n'est pas encore configure par le formateur.",
                            )
                            return redirect(f"{reverse('home')}?category={selected_category.id}&month={selected_month.id}")
                        if selected_month_active_subscription:
                            messages.info(
                                request,
                                "Vous avez deja un abonnement actif pour ce mois.",
                            )
                            return redirect(f"{reverse('home')}?category={selected_category.id}&month={selected_month.id}")
                        if selected_month_pending_subscription:
                            messages.info(
                                request,
                                "Votre demande d'abonnement pour ce mois est deja en attente de validation.",
                            )
                            return redirect(f"{reverse('home')}?category={selected_category.id}&month={selected_month.id}")

                        start_at = now
                        end_at = start_at + timedelta(days=30)
                        Subscription.objects.create(
                            user=request.user,
                            category=selected_month.category,
                            month=selected_month,
                            plan=Subscription.PLAN_MONTHLY,
                            trainer_approved=False,
                            start_at=start_at,
                            end_at=end_at,
                        )
                        messages.success(
                            request,
                            "Demande d'abonnement mensuel envoyee pour ce mois. "
                            "Vous pourrez consulter les cours apres validation du formateur.",
                        )
                        return redirect(f"{reverse('home')}?category={selected_category.id}&month={selected_month.id}")
            if (
                request.user.is_staff
                or selected_month_active_subscription is not None
            ):
                subjects_for_selected_month = list(
                    selected_month.courses.filter(content_type=Course.TYPE_SUBJECT).order_by("title")
                )
                corrections_for_selected_month = list(
                    selected_month.courses.filter(content_type=Course.TYPE_CORRECTION).order_by("title")
                )
    context = {
        "categories": categories,
        "selected_category": selected_category,
        "selected_month": selected_month,
        "months_for_selected_category": months_for_selected_category,
        "subjects_for_selected_month": subjects_for_selected_month,
        "corrections_for_selected_month": corrections_for_selected_month,
        "selected_month_active_subscription": selected_month_active_subscription,
        "selected_month_pending_subscription": selected_month_pending_subscription,
        "selected_month_subscription_form": selected_month_subscription_form,
        "selected_month_pricing": selected_month_pricing,
        "monthly_exams": list(
            selected_month.exams.filter(is_active=True).order_by("-id")
        )
        if selected_month
        else [],
    }
    return render(request, "courses/home.html", context)


def course_detail(request, slug):
    course = get_object_or_404(
        Course.objects.select_related("month", "month__category")
        .prefetch_related(
            "lessons",
            Prefetch(
                "quiz_questions",
                queryset=CourseQuizQuestion.objects.order_by("order", "id"),
            ),
        ),
        slug=slug,
    )
    active_subscription = _active_subscription_for_course(request.user, course)
    can_read_pdf = request.user.is_authenticated and (request.user.is_staff or active_subscription is not None)
    form = EnrollmentForm(course=course)
    quiz_questions = list(course.quiz_questions.all()) if course.quiz_enabled else []
    if course.quiz_enabled and not quiz_questions and course.pdf_file:
        try:
            created_count = rebuild_course_quiz(course)
        except Exception:
            created_count = 0
        if created_count > 0:
            quiz_questions = list(
                CourseQuizQuestion.objects.filter(course=course).order_by("order", "id")
            )

    if request.method == "POST":
        post_data = request.POST.copy()
        user_email = _user_email(request.user)
        if user_email:
            post_data["email"] = user_email
        form = EnrollmentForm(post_data, course=course)
        if form.is_valid():
            enrollment = form.save(commit=False)
            enrollment.course = course
            if user_email:
                enrollment.email = user_email
            enrollment.save()
            messages.success(request, "Inscription enregistree avec succes.")
            return redirect("course_detail", slug=course.slug)
    return render(
        request,
        "courses/course_detail.html",
        {
            "course": course,
            "form": form,
            "can_read_pdf": can_read_pdf,
            "quiz_questions": quiz_questions,
            "is_quiz_enabled": course.quiz_enabled,
            "monthly_exams": list(
                course.month.exams.filter(is_active=True).order_by("-id")
            )
            if course.month
            else [],
        },
    )


def read_course_pdf(request, slug):
    course = get_object_or_404(Course.objects.select_related("month", "month__category"), slug=slug)
    if not course.pdf_file:
        raise Http404("Aucun PDF disponible pour ce cours.")
    if not _has_course_access(request.user, course):
        raise Http404("Acces non autorise a ce PDF.")

    wants_download = request.GET.get("download", "").strip() in {"1", "true", "yes"}
    response = FileResponse(
        course.pdf_file.open("rb"),
        as_attachment=wants_download,
        filename=f"{course.slug}.pdf",
        content_type="application/pdf",
    )
    disposition = "attachment" if wants_download else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="{course.slug}.pdf"'
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


def course_quiz(request, slug):
    course = get_object_or_404(
        Course.objects.select_related("month", "month__category").prefetch_related(
            Prefetch(
                "quiz_questions",
                queryset=CourseQuizQuestion.objects.order_by("order", "id"),
            ),
        ),
        slug=slug,
    )
    if not _has_course_access(request.user, course):
        raise Http404("Acces non autorise au quiz de ce cours.")
    if not course.quiz_enabled:
        messages.info(
            request,
            "Le quiz est disponible uniquement pour les corrections.",
        )
        return redirect("course_detail", slug=course.slug)
    questions = list(course.quiz_questions.all())
    if not questions and course.pdf_file:
        try:
            created_count = rebuild_course_quiz(course)
        except Exception:
            created_count = 0
        if created_count > 0:
            questions = list(
                CourseQuizQuestion.objects.filter(course=course).order_by("order", "id")
            )
    if not questions:
        messages.info(
            request,
            "Aucun quiz n'est disponible pour ce cours. "
            "Le PDF doit contenir du texte selectionnable (pas seulement des images), "
            "et etre structure question par question avec options juste en dessous "
            "et une section Reponses (ex: 1 b, 2 a, 3 d).",
        )
        return redirect("course_detail", slug=course.slug)

    session_key = f"course_quiz_locked_{course.id}"
    if request.method == "GET" and request.GET.get("restart", "").strip() in {"1", "true", "yes"}:
        request.session.pop(session_key, None)
        request.session.modified = True
        messages.success(request, "Le quiz a ete reinitialise. Vous pouvez recommencer a zero.")
        return redirect("course_quiz", slug=course.slug)

    if request.method == "POST" and request.POST.get("action") == "restart":
        request.session.pop(session_key, None)
        request.session.modified = True
        messages.success(request, "Le quiz a ete reinitialise. Vous pouvez recommencer a zero.")
        return redirect("course_quiz", slug=course.slug)

    if request.method == "POST":
        if request.session.get(session_key):
            messages.info(
                request,
                "Ce quiz est deja valide. Cliquez sur \"Recommencer a zero\" pour refaire une tentative.",
            )
            return redirect("course_quiz", slug=course.slug)
        score = 0
        total = len(questions)
        details = []
        for q in questions:
            raw_values = request.POST.getlist(f"q_{q.pk}")
            selected_indices = []
            for raw in raw_values:
                try:
                    idx = int(str(raw).strip())
                except ValueError:
                    continue
                if 0 <= idx < 4 and idx not in selected_indices:
                    selected_indices.append(idx)
            expected_indices = (
                [int(idx) for idx in q.correct_indices if isinstance(idx, int) and 0 <= idx < 4]
                if isinstance(getattr(q, "correct_indices", None), list)
                else []
            )
            if not expected_indices:
                expected_indices = [q.correct_index]
            ok = set(selected_indices) == set(expected_indices)
            if ok:
                score += 1
            choices = q.choices if isinstance(q.choices, list) else []
            selected_labels = [choices[idx] for idx in selected_indices if 0 <= idx < len(choices)]
            correct_labels = [choices[idx] for idx in expected_indices if 0 <= idx < len(choices)]
            details.append(
                {
                    "question": q,
                    "selected_indices": selected_indices,
                    "selected_labels": selected_labels,
                    "correct_labels": correct_labels,
                    "correct": ok,
                }
            )
        request.session[session_key] = True
        request.session.modified = True
        note_sur_20 = _note_sur_20(score, total)
        if request.user.is_authenticated and not request.user.is_staff and course.month_id:
            MonthlyExamResult.objects.create(
                month=course.month,
                user=request.user,
                exam_course=course,
                score=score,
                total=total,
                note_sur_20=note_sur_20,
            )
        return render(
            request,
            "courses/course_quiz.html",
            {
                "course": course,
                "finished": True,
                "score": score,
                "total": total,
                "note_sur_20": note_sur_20,
                "details": details,
            },
        )

    return render(
        request,
        "courses/course_quiz.html",
        {
            "course": course,
            "questions": questions,
            "finished": False,
            "quiz_locked": bool(request.session.get(session_key)),
        },
    )


def search_courses(request):
    """Moteur de recherche pour les cours."""
    query = request.GET.get("q", "").strip()
    results = []
    
    if query:
        # Recherche par titre, description courte et niveau
        results = list(
            Course.objects.select_related("month", "month__category")
            .filter(
                Q(title__icontains=query)
                | Q(short_description__icontains=query)
                | Q(description__icontains=query)
            )
            .order_by("title")[:50]  # Limit to 50 results
        )
    
    # Récupérer les catégories pour la barre latérale
    categories, months = _get_cached_home_catalog()
    
    return render(
        request,
        "courses/search_results.html",
        {
            "query": query,
            "results": results,
            "categories": categories,
        },
    )


@user_passes_test(lambda u: u.is_staff, login_url="login")
def edit_monthly_exam(request, exam_id):
    """Vue pour éditer les questions d'un examen mensuel."""
    exam = get_object_or_404(MonthlyExam.objects.select_related("month", "month__category"), pk=exam_id)
    
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_question":
            # Ajouter une nouvelle question
            questions = exam.questions.copy() if exam.questions else []
            question_text = request.POST.get("question_text", "").strip()
            question_type = request.POST.get("question_type", "single")
            options = []
            correct_indices = []
            
            # Récupérer les options
            for i in range(1, 11):  # Maximum 10 options
                option_text = request.POST.get(f"option_{i}", "").strip()
                if option_text:
                    options.append(option_text)
                    if request.POST.get(f"correct_{i}"):
                        correct_indices.append(i-1)
            
            if question_text and options:
                question = {
                    "text": question_text,
                    "type": question_type,
                    "options": options,
                    "correct_answer": correct_indices if len(correct_indices) > 1 else (correct_indices[0] if correct_indices else 0)
                }
                questions.append(question)
                exam.questions = questions
                exam.save()
                messages.success(request, "Question ajoutée avec succès.")
            else:
                messages.error(request, "Veuillez saisir le texte de la question et au moins une option.")
                
        elif action == "delete_question":
            question_index = request.POST.get("question_index", "").strip()
            if question_index.isdigit():
                index = int(question_index)
                questions = exam.questions.copy() if exam.questions else []
                if 0 <= index < len(questions):
                    questions.pop(index)
                    exam.questions = questions
                    exam.save()
                    messages.success(request, "Question supprimée avec succès.")
                    
        elif action == "update_exam_settings":
            # Mettre à jour les paramètres de l'examen
            exam.title = request.POST.get("title", exam.title)
            exam.description = request.POST.get("description", exam.description)
            exam.passing_score = int(request.POST.get("passing_score", exam.passing_score))
            exam.duration_minutes = int(request.POST.get("duration_minutes", exam.duration_minutes))
            exam.is_active = request.POST.get("is_active") == "on"
            
            # Gérer l'upload du PDF
            if "pdf_file" in request.FILES:
                exam.pdf_file = request.FILES["pdf_file"]
            
            exam.save()
            
            # Régénérer les questions si un PDF a été uploadé
            if "pdf_file" in request.FILES:
                count = rebuild_monthly_exam_quiz(exam)
                if count > 0:
                    messages.success(request, f"Paramètres mis à jour. Quiz régénéré ({count} question(s)).")
                else:
                    messages.warning(request, "Paramètres mis à jour. Aucune question générée du PDF. Vérifiez le format.")
            else:
                messages.success(request, "Paramètres de l'examen mis à jour.")
    
    return render(request, "courses/edit_monthly_exam.html", {
        "exam": exam,
    })


@never_cache
def monthly_exam(request, exam_id):
    """Vue pour passer un examen mensuel."""
    exam = get_object_or_404(MonthlyExam.objects.select_related("month", "month__category"), pk=exam_id)
    month = exam.month

    # Vérifier l'accès à l'examen sans passer de "course=None".
    if not request.user.is_authenticated:
        raise Http404("Accès non autorisé à cet examen.")
    if not request.user.is_staff:
        now = timezone.now()
        active_subscription = (
            _active_subscriptions_queryset(request.user, now=now)
            .filter(
                Q(plan=Subscription.PLAN_MONTHLY, month=month)
                | Q(plan=Subscription.PLAN_YEARLY, category=month.category)
            )
            .order_by("-end_at")
            .first()
        )
        if not active_subscription:
            raise Http404("Accès non autorisé à cet examen.")
    
    course_for_result = month.courses.order_by("id").first()
    
    if not exam.is_active:
        raise Http404("Cet examen n'est plus disponible.")
    
    if request.method == "GET" and request.GET.get("restart", "").strip() in {"1", "true", "yes"}:
        messages.success(request, "Nouvelle tentative lancee. Le quiz redemarre a zero.")
        return redirect("monthly_exam", exam_id=exam.id)

    if request.method == "POST" and request.POST.get("action") == "restart":
        messages.success(request, "Nouvelle tentative lancee. Le quiz redemarre a zero.")
        return redirect("monthly_exam", exam_id=exam.id)

    if request.method == "POST":
        answers = {}
        for key in request.POST.keys():
            if not key.startswith("q_"):
                continue
            question_index = key[2:]
            values = [v for v in request.POST.getlist(key) if str(v).strip()]
            if not values:
                continue
            # Pour les checkbox (réponses multiples), conserver la liste.
            # Pour les radio, conserver la valeur unique.
            answers[question_index] = values if len(values) > 1 else values[0]
        
        score, total = exam.calculate_score(answers)
        note_sur_20 = _note_sur_20(score, total)

        details = []
        for idx, question in enumerate(exam.questions or []):
            raw_selected = answers.get(str(idx), [])
            selected_values = raw_selected if isinstance(raw_selected, list) else [raw_selected]
            selected_indices = []
            for raw in selected_values:
                try:
                    val = int(str(raw).strip())
                except (TypeError, ValueError):
                    continue
                if val not in selected_indices:
                    selected_indices.append(val)

            raw_correct = question.get("correct_answer")
            correct_values = raw_correct if isinstance(raw_correct, list) else [raw_correct]
            correct_indices = []
            for raw in correct_values:
                try:
                    val = int(raw)
                except (TypeError, ValueError):
                    continue
                if val not in correct_indices:
                    correct_indices.append(val)

            options = question.get("options") if isinstance(question.get("options"), list) else []
            selected_labels = [options[i] for i in selected_indices if 0 <= i < len(options)]
            correct_labels = [options[i] for i in correct_indices if 0 <= i < len(options)]
            details.append(
                {
                    "question_text": question.get("text", ""),
                    "selected_labels": selected_labels,
                    "correct_labels": correct_labels,
                    "correct": set(selected_indices) == set(correct_indices) and bool(correct_indices),
                }
            )

        if course_for_result is None:
            messages.error(
                request,
                "Impossible d'enregistrer le resultat: aucun cours n'est associe a ce mois.",
            )
            return redirect("monthly_exam", exam_id=exam.id)

        # Créer un nouveau résultat à chaque tentative.
        # Le classement formateur garde uniquement la première tentative par candidat/mois.
        MonthlyExamResult.objects.create(
            month=month,
            user=request.user,
            exam_course=course_for_result,
            score=score,
            total=total,
            note_sur_20=note_sur_20,
        )

        return render(
            request,
            "courses/monthly_exam.html",
            {
                "exam": exam,
                "month": month,
                "questions": exam.questions,
                "finished": True,
                "score": score,
                "total": total,
                "note_sur_20": note_sur_20,
                "details": details,
            },
        )
    
    return render(
        request,
        "courses/monthly_exam.html",
        {
            "exam": exam,
            "month": month,
            "questions": exam.questions,
            "finished": False,
        },
    )
