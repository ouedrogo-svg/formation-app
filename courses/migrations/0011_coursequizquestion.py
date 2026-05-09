import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0010_subscription_trainer_rejected"),
    ]

    operations = [
        migrations.CreateModel(
            name="CourseQuizQuestion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order", models.PositiveIntegerField(default=0)),
                ("prompt", models.TextField()),
                ("choices", models.JSONField()),
                ("correct_index", models.PositiveSmallIntegerField()),
                (
                    "course",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="quiz_questions",
                        to="courses.course",
                    ),
                ),
            ],
            options={
                "verbose_name": "Question de quiz (cours)",
                "verbose_name_plural": "Questions de quiz (cours)",
                "ordering": ["order", "id"],
            },
        ),
    ]
