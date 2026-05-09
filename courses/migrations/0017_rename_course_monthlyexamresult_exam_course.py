from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0016_alter_monthlyexam_month_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="monthlyexamresult",
            old_name="course",
            new_name="exam_course",
        ),
    ]
