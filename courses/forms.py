from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from decimal import Decimal, InvalidOperation

from .models import Category, Course, CourseMonth, Enrollment, Lesson, MonthlyExam, Subscription, SubscriptionPricing


class EnrollmentForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.course = kwargs.pop("course", None)
        super().__init__(*args, **kwargs)

    def clean_full_name(self):
        full_name = self.cleaned_data["full_name"].strip()
        if len(full_name) < 3:
            raise forms.ValidationError("Le nom complet doit contenir au moins 3 caracteres.")
        return full_name

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if self.course and Enrollment.objects.filter(course=self.course, email__iexact=email).exists():
            raise forms.ValidationError("Cet e-mail est deja inscrit pour ce cours.")
        return email

    class Meta:
        model = Enrollment
        fields = ["full_name", "email"]


class CourseForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["month"].required = True

    def clean_pdf_file(self):
        pdf_file = self.cleaned_data.get("pdf_file")
        if not pdf_file:
            return pdf_file
        if not pdf_file.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Le fichier doit etre au format PDF.")
        return pdf_file

    class Meta:
        model = Course
        fields = ["month", "title", "slug", "short_description", "description", "level", "pdf_file"]


class TrainerCourseForm(forms.ModelForm):
    def clean_pdf_file(self):
        pdf_file = self.cleaned_data.get("pdf_file")
        if not pdf_file:
            return pdf_file
        if not pdf_file.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Le fichier doit etre au format PDF.")
        return pdf_file

    class Meta:
        model = Course
        fields = ["month", "content_type", "title", "pdf_file"]


class TrainerLessonForm(forms.ModelForm):
    class Meta:
        model = Lesson
        fields = []


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name"]


class CourseMonthForm(forms.ModelForm):
    class Meta:
        model = CourseMonth
        fields = ["category", "month"]


class LessonForm(forms.ModelForm):
    class Meta:
        model = Lesson
        fields = ["course", "title", "position", "content", "video_url"]


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)
    first_name = forms.CharField(required=False, max_length=150)
    last_name = forms.CharField(required=False, max_length=150)

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "password1", "password2"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Cet e-mail est deja utilise.")
        return email


class SubscriptionForm(forms.Form):
    plan = forms.ChoiceField(
        choices=[("", "Option d'abonnement"), *Subscription.PLAN_CHOICES],
        label="Option d'abonnement",
    )


class SubscriptionPricingForm(forms.ModelForm):
    amount = forms.CharField()

    def clean_amount(self):
        raw_amount = (self.cleaned_data.get("amount") or "").strip()
        normalized = raw_amount.replace(" ", "").replace(",", ".")
        try:
            amount = Decimal(normalized)
        except InvalidOperation as exc:
            raise forms.ValidationError("Saisissez un montant valide (ex: 15000 ou 15000,50).") from exc
        if amount <= 0:
            raise forms.ValidationError("Le montant doit etre superieur a zero.")
        return amount

    class Meta:
        model = SubscriptionPricing
        fields = ["plan", "amount"]


class MonthlyExamForm(forms.ModelForm):
    def clean_passing_score(self):
        score = self.cleaned_data.get('passing_score')
        if score < 0 or score > 20:
            raise forms.ValidationError("La note de passage doit être entre 0 et 20.")
        return score

    def clean_pdf_file(self):
        pdf_file = self.cleaned_data.get("pdf_file")
        if not pdf_file:
            return pdf_file
        if not pdf_file.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Le fichier doit etre au format PDF.")
        return pdf_file

    class Meta:
        model = MonthlyExam
        fields = ['month', 'title', 'description', 'pdf_file', 'passing_score', 'duration_minutes', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }
