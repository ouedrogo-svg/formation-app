from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from .models import (
    Category,
    Course,
    CourseMonth,
    CourseQuizQuestion,
    Enrollment,
    Subscription,
    SubscriptionPricing,
)
from .pdf_quiz import build_questions_from_text, rebuild_course_quiz


class HomePageTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_home_no_course_list_until_month_selected(self):
        category = Category.objects.create(name="Programmation")
        month = CourseMonth.objects.create(category=category, month=1)
        Course.objects.create(
            title="JS Janvier",
            slug="js-janvier",
            short_description="Cours JS",
            description="Details JS",
            level="beginner",
            month=month,
        )
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "JS Janvier")
        response_cat = self.client.get(reverse("home"), {"category": category.id})
        self.assertNotContains(response_cat, "JS Janvier")
        response_month = self.client.get(
            reverse("home"), {"category": category.id, "month": month.id}
        )
        self.assertNotContains(response_month, "JS Janvier")
        self.assertContains(response_month, "vous devez d'abord vous abonner a ce mois")

    def test_home_displays_clickable_month_with_course_count(self):
        category = Category.objects.create(name="Programmation")
        month = CourseMonth.objects.create(category=category, month=1)
        Course.objects.create(
            title="JS Janvier",
            slug="js-janvier",
            short_description="Cours JS",
            description="Details JS",
            level="beginner",
            month=month,
        )
        response = self.client.get(reverse("home"))
        self.assertContains(response, "?category=" + str(category.id))
        response = self.client.get(reverse("home"), {"category": category.id})
        self.assertContains(response, "?category=" + str(category.id) + "&month=" + str(month.id))

    def test_home_month_requires_month_subscription_before_courses(self):
        user_model = get_user_model()
        user_model.objects.create_user(
            username="month-student",
            email="month-student@example.com",
            password="Secret12345",
        )
        self.client.login(username="month-student", password="Secret12345")
        category = Category.objects.create(name="Data")
        month = CourseMonth.objects.create(category=category, month=2)
        Course.objects.create(
            title="Python Fevrier",
            slug="python-fevrier",
            short_description="Cours Python",
            description="Details Python",
            level="beginner",
            month=month,
        )
        response = self.client.get(reverse("home"), {"category": category.id, "month": month.id})
        self.assertContains(response, "vous devez d'abord vous abonner a ce mois")
        self.assertNotContains(response, "Python Fevrier")

    def test_home_month_shows_courses_when_month_subscription_is_active(self):
        user_model = get_user_model()
        student = user_model.objects.create_user(
            username="month-subscribed",
            email="month-subscribed@example.com",
            password="Secret12345",
        )
        self.client.login(username="month-subscribed", password="Secret12345")
        category = Category.objects.create(name="DevOps")
        month = CourseMonth.objects.create(category=category, month=3)
        Course.objects.create(
            title="Docker Mars",
            slug="docker-mars",
            short_description="Cours Docker",
            description="Details Docker",
            level="beginner",
            month=month,
        )
        Subscription.objects.create(
            user=student,
            plan=Subscription.PLAN_MONTHLY,
            month=month,
            category=category,
            trainer_approved=True,
            start_at=timezone.now() - timedelta(days=1),
            end_at=timezone.now() + timedelta(days=30),
        )
        response = self.client.get(reverse("home"), {"category": category.id, "month": month.id})
        self.assertContains(response, "Docker Mars")


class EnrollmentFlowTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            title="Flask Avance",
            slug="flask-avance",
            short_description="Flask API",
            description="Construire des APIs",
            level="advanced",
        )

    def test_enrollment_success(self):
        response = self.client.post(
            reverse("course_detail", args=[self.course.slug]),
            {"full_name": "Jean Dupont", "email": "jean@example.com"},
            follow=True,
        )
        self.assertContains(response, "Inscription enregistree avec succes.")
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_enrollment_rejects_short_name(self):
        response = self.client.post(
            reverse("course_detail", args=[self.course.slug]),
            {"full_name": "Al", "email": "al@example.com"},
        )
        self.assertContains(response, "au moins 3 caracteres")
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_enrollment_rejects_duplicate_email_case_insensitive(self):
        Enrollment.objects.create(
            course=self.course,
            full_name="Premier Inscrit",
            email="TEST@EXAMPLE.COM",
        )
        response = self.client.post(
            reverse("course_detail", args=[self.course.slug]),
            {"full_name": "Deuxieme Inscrit", "email": "test@example.com"},
        )
        self.assertContains(response, "deja inscrit pour ce cours")
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_authenticated_enrollment_uses_account_email(self):
        user_model = get_user_model()
        user_model.objects.create_user(
            username="login-user",
            email="login-user@example.com",
            password="Secret12345",
        )
        self.client.login(username="login-user", password="Secret12345")
        self.client.post(
            reverse("course_detail", args=[self.course.slug]),
            {"full_name": "Utilisateur Connecte", "email": "autre@example.com"},
            follow=True,
        )
        self.assertTrue(
            Enrollment.objects.filter(course=self.course, email="login-user@example.com").exists()
        )


class TrainerPageTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            username="formateur",
            email="formateur@example.com",
            password="Secret12345",
            is_staff=True,
        )

    def test_trainer_page_requires_authentication(self):
        response = self.client.get(reverse("formateur"))
        self.assertEqual(response.status_code, 302)

    def test_trainer_page_denies_non_staff_user(self):
        user_model = get_user_model()
        user_model.objects.create_user(
            username="etudiant-simple",
            email="etudiant-simple@example.com",
            password="Secret12345",
            is_staff=False,
        )
        self.client.login(username="etudiant-simple", password="Secret12345")
        response = self.client.get(reverse("formateur"))
        self.assertEqual(response.status_code, 302)

    def test_trainer_page_accessible_for_staff(self):
        self.client.login(username="formateur", password="Secret12345")
        response = self.client.get(reverse("formateur"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Espace Formateur")

    def test_trainer_can_create_course(self):
        self.client.login(username="formateur", password="Secret12345")
        category = Category.objects.create(name="Creation Cat")
        month = CourseMonth.objects.create(category=category, month=4)
        pdf = SimpleUploadedFile(
            "cours.pdf",
            b"%PDF-1.4 test pdf",
            content_type="application/pdf",
        )
        response = self.client.post(
            reverse("formateur"),
            {
                "action": "create_course",
                "course-month": month.id,
                "course-content_type": Course.TYPE_SUBJECT,
                "course-title": "Nouveau Cours",
                "course-pdf_file": pdf,
            },
            follow=True,
        )
        self.assertContains(response, "cree(e) avec succes")
        self.assertTrue(Course.objects.filter(title="Nouveau Cours").exists())

    def test_trainer_can_create_lesson(self):
        self.client.login(username="formateur", password="Secret12345")
        course = Course.objects.create(
            title="Cours Test",
            slug="cours-test",
            short_description="Court",
            description="Long",
            level="beginner",
        )
        response = self.client.post(
            reverse("formateur"),
            {
                "action": "create_lesson",
                "lesson-course": course.id,
                "lesson-title": "Lecon 1",
                "lesson-position": 1,
                "lesson-content": "Contenu de la lecon",
                "lesson-video_url": "",
            },
            follow=True,
        )
        self.assertContains(response, "Lecon ajoutee avec succes.")
        self.assertTrue(course.lessons.filter(title="Lecon 1").exists())

    def test_trainer_can_trigger_quiz_regeneration(self):
        self.client.login(username="formateur", password="Secret12345")
        course = Course.objects.create(
            title="Cours Quiz Regeneration",
            slug="cours-quiz-regeneration",
            short_description="S",
            description="D",
            level="beginner",
            content_type=Course.TYPE_CORRECTION,
        )
        response = self.client.post(
            reverse("formateur"),
            {"action": "regenerate_course_quiz", "course_id": str(course.id)},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aucune question regeneree")

    def test_trainer_validates_enrollment_and_exports_xlsx(self):
        self.client.login(username="formateur", password="Secret12345")
        category = Category.objects.create(name="Export Cat")
        month = CourseMonth.objects.create(category=category, month=1)
        course = Course.objects.create(
            title="Cours Export",
            slug="cours-export",
            short_description="S",
            description="D",
            level="beginner",
            month=month,
        )
        enrollment = Enrollment.objects.create(
            full_name="Marie Martin",
            email="marie@example.com",
            course=course,
            trainer_validated=False,
        )
        SubscriptionPricing.objects.create(plan=Subscription.PLAN_MONTHLY, amount=Decimal("40.00"))
        self.client.post(
            reverse("formateur"),
            {"action": "validate_enrollment", "enrollment_id": str(enrollment.id), "amount": "35,50"},
        )
        enrollment.refresh_from_db()
        self.assertTrue(enrollment.trainer_validated)
        self.assertEqual(enrollment.amount_paid, Decimal("35.50"))
        response = self.client.get(reverse("formateur_enrollments_export"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            response["Content-Type"],
        )
        self.assertTrue(response.content.startswith(b"PK"))

    def test_enrollment_export_denies_non_staff(self):
        user_model = get_user_model()
        user_model.objects.create_user(
            username="solo-export",
            email="solo-export@example.com",
            password="Secret12345",
            is_staff=False,
        )
        self.client.login(username="solo-export", password="Secret12345")
        response = self.client.get(reverse("formateur_enrollments_export"))
        self.assertEqual(response.status_code, 302)

    def test_course_pdf_read_inline(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="etudiant",
            email="etudiant@example.com",
            password="Secret12345",
        )
        pdf = SimpleUploadedFile(
            "lecture.pdf",
            b"%PDF-1.4 inline content",
            content_type="application/pdf",
        )
        category = Category.objects.create(name="Acces PDF")
        month = CourseMonth.objects.create(category=category, month=1)
        course = Course.objects.create(
            title="Lecture PDF",
            slug="lecture-pdf",
            short_description="Lecture",
            description="Cours en mode lecture",
            level="beginner",
            month=month,
            pdf_file=pdf,
        )
        Subscription.objects.create(
            user=user,
            plan=Subscription.PLAN_MONTHLY,
            month=month,
            category=category,
            trainer_approved=True,
            start_at=timezone.now() - timedelta(days=1),
            end_at=timezone.now() + timedelta(days=30),
        )
        self.client.login(username="etudiant", password="Secret12345")
        response = self.client.get(reverse("course_pdf_read", args=[course.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("inline;", response["Content-Disposition"])

    def test_course_pdf_blocked_until_trainer_approves(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(
            username="etudiant2",
            email="etudiant2@example.com",
            password="Secret12345",
        )
        pdf = SimpleUploadedFile(
            "attente.pdf",
            b"%PDF-1.4 pending",
            content_type="application/pdf",
        )
        category = Category.objects.create(name="Attente")
        month = CourseMonth.objects.create(category=category, month=2)
        course = Course.objects.create(
            title="Lecture Attente",
            slug="lecture-attente",
            short_description="Lecture",
            description="Cours",
            level="beginner",
            month=month,
            pdf_file=pdf,
        )
        Subscription.objects.create(
            user=user,
            plan=Subscription.PLAN_MONTHLY,
            month=month,
            category=category,
            trainer_approved=False,
            start_at=timezone.now() - timedelta(days=1),
            end_at=timezone.now() + timedelta(days=30),
        )
        self.client.login(username="etudiant2", password="Secret12345")
        response = self.client.get(reverse("course_pdf_read", args=[course.slug]))
        self.assertEqual(response.status_code, 404)

    def test_trainer_approves_subscription_then_student_reads_pdf(self):
        user_model = get_user_model()
        staff = user_model.objects.create_user(
            username="formateur2",
            email="formateur2@example.com",
            password="Secret12345",
            is_staff=True,
        )
        student = user_model.objects.create_user(
            username="etudiant3",
            email="etudiant3@example.com",
            password="Secret12345",
        )
        pdf = SimpleUploadedFile(
            "apres.pdf",
            b"%PDF-1.4 after approve",
            content_type="application/pdf",
        )
        category = Category.objects.create(name="Validation")
        month = CourseMonth.objects.create(category=category, month=3)
        course = Course.objects.create(
            title="Lecture Apres Validation",
            slug="lecture-apres-validation",
            short_description="Lecture",
            description="Cours",
            level="beginner",
            month=month,
            pdf_file=pdf,
        )
        sub = Subscription.objects.create(
            user=student,
            plan=Subscription.PLAN_MONTHLY,
            month=month,
            category=category,
            trainer_approved=False,
            start_at=timezone.now() - timedelta(days=1),
            end_at=timezone.now() + timedelta(days=30),
        )
        self.client.login(username="formateur2", password="Secret12345")
        self.client.post(
            reverse("formateur"),
            {"action": "approve_subscription", "subscription_id": str(sub.id)},
        )
        sub.refresh_from_db()
        self.assertTrue(sub.trainer_approved)
        self.client.logout()
        self.client.login(username="etudiant3", password="Secret12345")
        response = self.client.get(reverse("course_pdf_read", args=[course.slug]))
        self.assertEqual(response.status_code, 200)

    def test_staff_can_read_pdf_without_enrollment(self):
        self.client.login(username="formateur", password="Secret12345")
        pdf = SimpleUploadedFile(
            "staff.pdf",
            b"%PDF-1.4 staff content",
            content_type="application/pdf",
        )
        course = Course.objects.create(
            title="Lecture Staff",
            slug="lecture-staff",
            short_description="Lecture",
            description="Cours pour staff",
            level="beginner",
            pdf_file=pdf,
        )
        response = self.client.get(reverse("course_pdf_read", args=[course.slug]))
        self.assertEqual(response.status_code, 200)

    def test_course_pdf_read_requires_subscription(self):
        pdf = SimpleUploadedFile(
            "lecture-bloquee.pdf",
            b"%PDF-1.4 blocked content",
            content_type="application/pdf",
        )
        course = Course.objects.create(
            title="Lecture Bloquee",
            slug="lecture-bloquee",
            short_description="Lecture",
            description="Cours en mode lecture",
            level="beginner",
            pdf_file=pdf,
        )
        response = self.client.get(reverse("course_pdf_read", args=[course.slug]))
        self.assertEqual(response.status_code, 404)


class PdfQuizTests(TestCase):
    def test_build_structured_questions_from_text(self):
        text = """
1. Premiere question ?
a) Mauvaise
b) Bonne
c) Autre
d) Encore
Reponse : b

2. Deuxieme question ?
a) A
b) B
c) C
d) D
Reponse : c

3. Troisieme question ?
a) Un
b) Deux
c) Trois
d) Quatre
Reponse : a
"""
        items = build_questions_from_text(text, rng_seed=1)
        self.assertGreaterEqual(len(items), 3)
        self.assertEqual(items[0]["correct_index"], 1)
        self.assertEqual(len(items[0]["choices"]), 4)

    def test_build_returns_empty_when_no_explicit_quiz_structure(self):
        parts = []
        for i in range(20):
            parts.append(
                f"Ceci est la phrase numero {i} du document fictif. Elle contient assez de mots pour le test."
            )
        text = " ".join(parts)
        items = build_questions_from_text(text, rng_seed=42)
        self.assertEqual(items, [])

    def test_build_structured_questions_uses_reponses_column(self):
        lines = []
        for i in range(1, 61):
            lines.extend(
                [
                    f"{i}. Question numero {i} ?",
                    "a) Proposition A",
                    "b) Proposition B",
                    "c) Proposition C",
                    "d) Proposition D",
                    "",
                ]
            )
        lines.append("Reponses")
        for i in range(1, 61):
            lines.append(f"{i} b")
        text = "\n".join(lines)

        items = build_questions_from_text(text, rng_seed=7)
        self.assertEqual(len(items), 60)
        self.assertEqual(items[0]["correct_index"], 1)
        self.assertEqual(items[59]["correct_index"], 1)
        self.assertEqual(items[0].get("correct_indices"), [1])

    def test_build_structured_questions_supports_multi_letters_in_reponses_column(self):
        text = """
1. Question 1 ?
a) A1
b) B1
c) C1
d) D1

2. Question 2 ?
a) A2
b) B2
c) C2
d) D2

Reponses
1 AB
2 CD
"""
        items = build_questions_from_text(text, rng_seed=3)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["correct_indices"], [0, 1])
        self.assertEqual(items[1]["correct_indices"], [2, 3])

    def test_build_structured_questions_from_sentence_and_options_block(self):
        text = """
Premiere phrase du PDF pour la question 1 ?
a) Faux A
b) Bonne B
c) Faux C
d) Faux D

Deuxieme phrase du PDF pour la question 2 ?
a) Bonne A
b) Faux B
c) Faux C
d) Faux D

Reponses
1 b
2 a
"""
        items = build_questions_from_text(text, rng_seed=9)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["prompt"], "Premiere phrase du PDF pour la question 1 ?")
        self.assertEqual(items[0]["correct_index"], 1)
        self.assertEqual(items[1]["correct_index"], 0)

    def test_build_structured_questions_matches_correction_pdf_style(self):
        text = """
1
La passation d'un avenant répond aux conditions techniques suivantes :
A. La non modification de l'objet du contrat initial
B. La non dénaturation de l'objet du contrat
C. Le caractère détachable de l'objet de l'avenant
D. Le caractère non détachable de l'objet de l'avenant
NB : Article 176 alinéa 2 du décret N°2024-1748
ABD
2
Pour les marchés publics de l'administration centrale, qui est habilité à autoriser les avenants ?
A. Le ministre chargé du budget
B. L'ordonnateur du budget concerné
C. Le Premier ministre
D. Le représentant de l'entité administrative chargée du contrôle
NB : Article 176 alinéa 4 du décret N°2024-1748
B
3 Pour les marchés publics de la région, qui est habilité à autoriser les avenants ? B
A. Le Gouverneur de région
B. Le Conseil de collectivité
C. Le Haut-commissaire de la province
D. Le ministre chargé des collectivités territoriales
"""
        items = build_questions_from_text(text, rng_seed=1)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["correct_index"], 0)
        self.assertEqual(items[1]["correct_index"], 1)
        self.assertEqual(items[2]["correct_index"], 1)

    def test_rebuild_clears_questions_when_no_pdf(self):
        course = Course.objects.create(
            title="Sans PDF",
            slug="sans-pdf",
            short_description="S",
            description="D",
            level="beginner",
        )
        CourseQuizQuestion.objects.create(
            course=course,
            order=0,
            prompt="Q",
            choices=["a", "b", "c", "d"],
            correct_index=0,
        )
        rebuild_course_quiz(course)
        self.assertEqual(CourseQuizQuestion.objects.filter(course=course).count(), 0)

    def test_quiz_not_regenerated_when_pdf_unchanged(self):
        pdf = SimpleUploadedFile(
            "stable.pdf",
            b"%PDF-1.4 stable",
            content_type="application/pdf",
        )
        course = Course.objects.create(
            title="Stable Quiz",
            slug="stable-quiz",
            short_description="S",
            description="D",
            level="beginner",
            pdf_file=pdf,
        )
        CourseQuizQuestion.objects.filter(course=course).delete()
        CourseQuizQuestion.objects.create(
            course=course,
            order=0,
            prompt="Question figee",
            choices=["a", "b", "c", "d"],
            correct_index=2,
        )
        course.title = "Titre modifie"
        course.save()
        course.refresh_from_db()
        q = CourseQuizQuestion.objects.get(course=course)
        self.assertEqual(q.prompt, "Question figee")
        self.assertEqual(q.correct_index, 2)


class CourseQuizViewTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.student = self.user_model.objects.create_user(
            username="quiz-user",
            email="quiz-user@example.com",
            password="Secret12345",
        )
        self.category = Category.objects.create(name="Quiz Cat")
        self.month = CourseMonth.objects.create(category=self.category, month=1)
        self.course = Course.objects.create(
            title="Cours Quiz",
            slug="cours-quiz",
            short_description="S",
            description="D",
            level="beginner",
            month=self.month,
            content_type=Course.TYPE_CORRECTION,
        )
        self.q1 = CourseQuizQuestion.objects.create(
            course=self.course,
            order=0,
            prompt="Deux plus deux ?",
            choices=["3", "4", "5", "6"],
            correct_index=1,
        )
        self.q2 = CourseQuizQuestion.objects.create(
            course=self.course,
            order=1,
            prompt="Capitale de la France ?",
            choices=["Lyon", "Paris", "Marseille", "Nice"],
            correct_index=1,
        )
        Subscription.objects.create(
            user=self.student,
            plan=Subscription.PLAN_MONTHLY,
            month=self.month,
            category=self.category,
            trainer_approved=True,
            start_at=timezone.now() - timedelta(days=1),
            end_at=timezone.now() + timedelta(days=30),
        )

    def test_quiz_requires_access(self):
        outsider = self.user_model.objects.create_user(
            username="outsider",
            email="outsider@example.com",
            password="Secret12345",
        )
        self.client.login(username="outsider", password="Secret12345")
        response = self.client.get(reverse("course_quiz", args=[self.course.slug]))
        self.assertEqual(response.status_code, 404)

    def test_quiz_get_and_post_score(self):
        self.client.login(username="quiz-user", password="Secret12345")
        response = self.client.get(reverse("course_quiz", args=[self.course.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deux plus deux")
        post = self.client.post(
            reverse("course_quiz", args=[self.course.slug]),
            {
                f"q_{self.q1.pk}": "1",
                f"q_{self.q2.pk}": "1",
            },
        )
        self.assertEqual(post.status_code, 200)
        self.assertContains(post, "Votre score")
        self.assertContains(post, "<strong>2</strong> / 2")

    def test_quiz_multi_answer_requires_exact_match(self):
        self.q1.correct_index = 0
        self.q1.correct_indices = [0, 2]
        self.q1.save(update_fields=["correct_index", "correct_indices"])
        self.client.login(username="quiz-user", password="Secret12345")
        post = self.client.post(
            reverse("course_quiz", args=[self.course.slug]),
            {
                f"q_{self.q1.pk}": ["0"],
                f"q_{self.q2.pk}": ["1"],
            },
        )
        self.assertEqual(post.status_code, 200)
        self.assertContains(post, "<strong>1</strong> / 2")

    def test_quiz_is_locked_after_validation_until_restart(self):
        self.client.login(username="quiz-user", password="Secret12345")
        quiz_url = reverse("course_quiz", args=[self.course.slug])
        first_attempt = self.client.post(
            quiz_url,
            {
                f"q_{self.q1.pk}": "1",
                f"q_{self.q2.pk}": "1",
            },
        )
        self.assertEqual(first_attempt.status_code, 200)
        self.assertContains(first_attempt, "Votre score")

        locked_page = self.client.get(quiz_url)
        self.assertContains(locked_page, "Ce quiz est deja valide")
        self.assertNotContains(locked_page, "Valider et voir mon score")

        blocked_post = self.client.post(
            quiz_url,
            {
                f"q_{self.q1.pk}": "1",
                f"q_{self.q2.pk}": "1",
            },
            follow=True,
        )
        self.assertContains(blocked_post, "Ce quiz est deja valide")

        restarted = self.client.post(quiz_url, {"action": "restart"}, follow=True)
        self.assertContains(restarted, "Le quiz a ete reinitialise")
        self.assertContains(restarted, "Valider et voir mon score")


class AuthenticationTests(TestCase):
    def test_signup_creates_account(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "newuser",
                "email": "newuser@example.com",
                "first_name": "New",
                "last_name": "User",
                "password1": "StrongPass12345",
                "password2": "StrongPass12345",
            },
            follow=True,
        )
        self.assertContains(response, "Compte cree avec succes.")
        self.assertTrue(get_user_model().objects.filter(username="newuser").exists())
