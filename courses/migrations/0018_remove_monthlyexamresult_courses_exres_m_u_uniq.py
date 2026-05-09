from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0017_rename_course_monthlyexamresult_exam_course"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="monthlyexamresult",
            name="courses_exres_m_u_uniq",
        ),
    ]
