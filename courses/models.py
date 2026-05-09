from django.db import models
from django.utils import timezone


class Category(models.Model):
    name = models.CharField(max_length=120, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Categorie"
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name


class CourseMonth(models.Model):
    MONTH_CHOICES = [
        (1, "Janvier"),
        (2, "Fevrier"),
        (3, "Mars"),
        (4, "Avril"),
        (5, "Mai"),
        (6, "Juin"),
        (7, "Juillet"),
        (8, "Aout"),
        (9, "Septembre"),
        (10, "Octobre"),
        (11, "Novembre"),
        (12, "Decembre"),
    ]

    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="months")
    month = models.PositiveSmallIntegerField(choices=MONTH_CHOICES)

    class Meta:
        ordering = ["category__name", "month"]
        constraints = [
            models.UniqueConstraint(fields=["category", "month"], name="courses_coursemnth_category_month_uniq"),
        ]
        verbose_name = "Mois de cours"
        verbose_name_plural = "Mois de cours"

    def __str__(self):
        return f"{self.category.name} - {self.get_month_display()}"


class Course(models.Model):
    TYPE_SUBJECT = "subject"
    TYPE_CORRECTION = "correction"
    TYPE_CHOICES = [
        (TYPE_SUBJECT, "Sujet"),
        (TYPE_CORRECTION, "Correction"),
    ]

    month = models.ForeignKey(
        CourseMonth,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="courses",
    )
    content_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_SUBJECT,
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    short_description = models.CharField(max_length=255)
    description = models.TextField()
    level = models.CharField(
        max_length=20,
        choices=[
            ("beginner", "Beginner"),
            ("intermediate", "Intermediate"),
            ("advanced", "Advanced"),
        ],
        default="beginner",
    )
    pdf_file = models.FileField(upload_to="course_pdfs/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]
        indexes = [
            models.Index(fields=["month", "content_type", "title"], name="courses_month_type_title_idx"),
            models.Index(fields=["level"], name="courses_course_level_idx"),
            models.Index(fields=["created_at"], name="courses_course_created_idx"),
            models.Index(fields=["month", "title"], name="courses_course_month_title_idx"),
        ]

    def __str__(self):
        return self.title

    @property
    def quiz_enabled(self):
        return self.content_type == self.TYPE_CORRECTION


class CourseQuizQuestion(models.Model):
    """Question generee a partir du PDF du cours (QCM)."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="quiz_questions")
    order = models.PositiveIntegerField(default=0)
    prompt = models.TextField()
    choices = models.JSONField()
    correct_index = models.PositiveSmallIntegerField()
    correct_indices = models.JSONField(default=list)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Question de quiz (cours)"
        verbose_name_plural = "Questions de quiz (cours)"

    def __str__(self):
        return f"{self.course.title} — Q{self.order + 1}"


class Lesson(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="lessons")
    title = models.CharField(max_length=200)
    video_url = models.URLField(blank=True)
    content = models.TextField()
    position = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["course", "position"], name="courses_lesson_course_position_uniq"),
        ]
        indexes = [
            models.Index(fields=["course", "position"], name="courses_lesson_course_pos_idx"),
        ]

    def __str__(self):
        return f"{self.course.title} - {self.title}"


class Enrollment(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    full_name = models.CharField(max_length=120)
    email = models.EmailField()
    enrolled_at = models.DateTimeField(auto_now_add=True)
    trainer_validated = models.BooleanField(
        default=False,
        verbose_name="Inscription validee par le formateur",
    )
    validated_at = models.DateTimeField(null=True, blank=True)
    amount_paid = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Montant (inscription)",
    )

    class Meta:
        ordering = ["-enrolled_at"]
        constraints = [
            models.UniqueConstraint(fields=["course", "email"], name="courses_enroll_course_email_uniq"),
        ]
        indexes = [
            models.Index(fields=["course", "enrolled_at"], name="courses_enroll_course_date_idx"),
            models.Index(fields=["trainer_validated", "validated_at"], name="courses_enroll_validated_idx"),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.course.title})"


class Subscription(models.Model):
    PLAN_MONTHLY = "monthly"
    PLAN_YEARLY = "yearly"
    PLAN_CHOICES = [
        (PLAN_MONTHLY, "Mensuel"),
        (PLAN_YEARLY, "Annuel"),
    ]

    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="subscriptions")
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="subscriptions",
        null=True,
        blank=True,
    )
    month = models.ForeignKey(
        CourseMonth,
        on_delete=models.CASCADE,
        related_name="subscriptions",
        null=True,
        blank=True,
    )
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES)
    trainer_approved = models.BooleanField(
        default=False,
        verbose_name="Valide par le formateur",
        help_text="Si faux, l'etudiant ne peut pas acceder au contenu reserve malgre les dates.",
    )
    trainer_rejected = models.BooleanField(
        default=False,
        verbose_name="Refuse par le formateur",
        help_text="Si vrai, la demande est refusee ; l'etudiant peut soumettre une nouvelle demande.",
    )
    start_at = models.DateTimeField(default=timezone.now)
    end_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-end_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_at__gte=models.F("start_at")),
                name="courses_sub_end_after_start_chk",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(plan="monthly", month__isnull=False)
                    | models.Q(plan="yearly", month__isnull=True)
                ),
                name="courses_sub_plan_month_consistency_chk",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "end_at"], name="courses_sub_user_end_idx"),
            models.Index(
                fields=["user", "plan", "category", "month", "end_at"],
                name="courses_sub_access_lookup_idx",
            ),
            models.Index(fields=["category", "plan", "end_at"], name="courses_sub_cat_plan_end_idx"),
            models.Index(fields=["trainer_approved", "end_at"], name="courses_sub_approved_end_idx"),
            models.Index(fields=["trainer_rejected", "end_at"], name="courses_sub_rejected_end_idx"),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.get_plan_display()} ({self.end_at.date()})"

    @property
    def is_active(self):
        return self.end_at >= timezone.now()


class SubscriptionPricing(models.Model):
    plan = models.CharField(max_length=20, choices=Subscription.PLAN_CHOICES, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan"]

    def __str__(self):
        return f"{self.get_plan_display()} - {self.amount}"


class MonthlyExamResult(models.Model):
    month = models.ForeignKey(CourseMonth, on_delete=models.CASCADE, related_name="exam_results")
    exam_course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="exam_results", null=True, blank=True)
    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="monthly_exam_results")
    score = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    note_sur_20 = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    passed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["month__category__name", "month__month", "-note_sur_20", "user__last_name", "user__first_name"]
        indexes = [
            models.Index(fields=["month", "-note_sur_20"], name="courses_exres_m_note_idx"),
            models.Index(fields=["user", "month"], name="courses_exres_u_m_idx"),
        ]
        verbose_name = "Resultat examen mensuel"
        verbose_name_plural = "Resultats examens mensuels"

    def __str__(self):
        return f"{self.user.username} - {self.month} - {self.note_sur_20}/20"


class MonthlyExam(models.Model):
    """Examen créé manuellement par le formateur pour un mois."""
    month = models.ForeignKey(CourseMonth, on_delete=models.CASCADE, related_name="exams")
    title = models.CharField(max_length=200, default="Examen mensuel")
    description = models.TextField(blank=True, help_text="Description de l'examen")
    pdf_file = models.FileField(upload_to="exam_pdfs/", blank=True, null=True, help_text="PDF pour générer les questions automatiquement")
    questions = models.JSONField(default=list, help_text="Liste des questions au format JSON")
    passing_score = models.PositiveIntegerField(default=12, help_text="Note sur 20 pour réussir")
    duration_minutes = models.PositiveIntegerField(default=60, help_text="Durée en minutes")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["month__category__name", "month__month"]
        verbose_name = "Examen mensuel"
        verbose_name_plural = "Examens mensuels"

    def __str__(self):
        return f"Examen {self.month} - {self.title}"

    def get_total_questions(self):
        """Retourne le nombre total de questions."""
        return len(self.questions) if self.questions else 0

    def calculate_score(self, answers):
        """Calcule le score basé sur les réponses fournies."""
        if not self.questions:
            return 0, 0

        correct_answers = 0
        total_questions = len(self.questions)

        for i, question in enumerate(self.questions):
            key = str(i)
            if key not in answers:
                continue

            user_answer = answers[key]
            correct_answer = question.get("correct_answer")

            # Normaliser en listes d'index (int) pour supporter:
            # - question simple: "single"/"multiple_choice"
            # - question multiple: "multiple"/"multiple_select"
            user_values = user_answer if isinstance(user_answer, list) else [user_answer]
            correct_values = correct_answer if isinstance(correct_answer, list) else [correct_answer]

            user_indices = set()
            for value in user_values:
                try:
                    user_indices.add(int(value))
                except (ValueError, TypeError):
                    continue

            correct_indices = set()
            for value in correct_values:
                try:
                    correct_indices.add(int(value))
                except (ValueError, TypeError):
                    continue

            if user_indices and correct_indices and user_indices == correct_indices:
                correct_answers += 1

        return correct_answers, total_questions
