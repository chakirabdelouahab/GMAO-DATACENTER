"""Déclenchement automatique des notifications lors des enregistrements."""
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import NonConformite, OrdreTravail
from .notifications import notifier_non_conformite, notifier_ordre_assigne


# --- Ordre de travail : e-mail au technicien quand il est assigné ou changé -------------

@receiver(pre_save, sender=OrdreTravail)
def memoriser_technicien(sender, instance, raw=False, **kwargs):
    if raw:
        return
    instance._ancien_technicien_id = (
        OrdreTravail.objects.filter(pk=instance.pk).values_list("technicien_id", flat=True).first()
        if instance.pk else None
    )


@receiver(post_save, sender=OrdreTravail)
def notifier_assignation(sender, instance, created, raw=False, **kwargs):
    if raw or not instance.technicien_id:
        return
    if instance.technicien_id == getattr(instance, "_ancien_technicien_id", None):
        return
    if instance.statut in ("ouvert", "en_cours"):
        transaction.on_commit(lambda: notifier_ordre_assigne(instance))


# --- Non-conformité : e-mail aux responsables si critique ou majeure ------------------

def _grave(nc):
    return nc.gravite in ("critique", "majeure") and nc.statut != "cloturee"


@receiver(pre_save, sender=NonConformite)
def memoriser_gravite(sender, instance, raw=False, **kwargs):
    if raw:
        return
    instance._ancienne_gravite = (
        NonConformite.objects.filter(pk=instance.pk).values_list("gravite", flat=True).first()
        if instance.pk else None
    )


@receiver(post_save, sender=NonConformite)
def notifier_nc(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    if created:
        # La référence « NC-xxxx » est posée par un second enregistrement juste après.
        instance._nc_nouvelle = True
        return
    if getattr(instance, "_nc_nouvelle", False) and instance.reference:
        instance._nc_nouvelle = False
        if _grave(instance):
            transaction.on_commit(lambda: notifier_non_conformite(instance, nouvelle=True))
        return
    ancienne = getattr(instance, "_ancienne_gravite", None)
    if ancienne is not None and ancienne != instance.gravite and _grave(instance):
        transaction.on_commit(lambda: notifier_non_conformite(instance, nouvelle=False))