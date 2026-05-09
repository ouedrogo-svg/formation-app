import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Course
from .pdf_quiz import rebuild_course_quiz

logger = logging.getLogger(__name__)


@receiver(pre_save, sender=Course)
def course_quiz_cache_pdf_name(sender, instance, **kwargs):
    """Memorise le chemin du PDF avant sauvegarde pour ne regenrer le quiz que si le fichier change."""
    if instance.pk:
        try:
            previous = Course.objects.only("pdf_file").get(pk=instance.pk).pdf_file
            instance._quiz_pdf_before = previous.name if previous else ""
        except Course.DoesNotExist:
            instance._quiz_pdf_before = ""
    else:
        instance._quiz_pdf_before = None


@receiver(post_save, sender=Course)
def course_sync_quiz_from_pdf(sender, instance, created, update_fields, **kwargs):
    if not instance.quiz_enabled:
        return

    current = instance.pdf_file.name if instance.pdf_file else ""

    if update_fields is not None:
        if "pdf_file" not in update_fields and not created:
            return
    elif not created:
        before = getattr(instance, "_quiz_pdf_before", None)
        if before is None:
            before = ""
        if before == current:
            return

    try:
        rebuild_course_quiz(instance)
    except Exception:
        logger.exception("Echec regeneration du quiz pour le cours pk=%s", instance.pk)
