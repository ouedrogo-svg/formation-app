from django.urls import path

from . import views

from django .contrib.staticfiles.urls import staticfiles_urlpatterns

urlpatterns = [ 
    path("", views.home, name="home"),
    path("recherche/", views.search_courses, name="search_courses"),
    path("inscription/", views.signup, name="signup"),
    path("formateur/", views.trainer_page, name="formateur"),
    path(
        "formateur/inscriptions-export.xlsx",
        views.trainer_enrollments_export_xlsx,
        name="formateur_enrollments_export",
    ),
    path(
        "formateur/abonnements-export.xlsx",
        views.trainer_subscriptions_export_xlsx,
        name="formateur_subscriptions_export",
    ),
    path("cours/<slug:slug>/", views.course_detail, name="course_detail"),
    path("cours/<slug:slug>/lecture-pdf/", views.read_course_pdf, name="course_pdf_read"),
    path("cours/<slug:slug>/quiz/", views.course_quiz, name="course_quiz"),
    path("examens/<int:exam_id>/", views.monthly_exam, name="monthly_exam"),
    path("examens/<int:exam_id>/editer/", views.edit_monthly_exam, name="edit_monthly_exam"),
]
urlpatterns += staticfiles_urlpatterns()