from django.db import migrations, models


def backfill_correct_indices(apps, schema_editor):
    CourseQuizQuestion = apps.get_model("courses", "CourseQuizQuestion")
    for q in CourseQuizQuestion.objects.all().only("id", "correct_index", "correct_indices"):
        if isinstance(q.correct_indices, list) and q.correct_indices:
            continue
        q.correct_indices = [int(q.correct_index)]
        q.save(update_fields=["correct_indices"])


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0011_coursequizquestion"),
    ]

    operations = [
        migrations.AddField(
            model_name="coursequizquestion",
            name="correct_indices",
            field=models.JSONField(default=list),
        ),
        migrations.RunPython(backfill_correct_indices, migrations.RunPython.noop),
    ]

