from datetime import date, timedelta

from django.conf import settings
from django.db import models


class Local(models.Model):
    nom = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.nom


class Equipement(models.Model):
    CRITICITE_CHOICES = [
        ("faible", "Faible"),
        ("moyenne", "Moyenne"),
        ("elevee", "Élevée"),
        ("critique", "Critique"),
    ]
    STATUT_CHOICES = [
        ("service", "En service"),
        ("veille", "En veille"),
        ("panne", "En panne"),
        ("hors_service", "Hors service"),
    ]

    nom = models.CharField(max_length=150)
    local = models.ForeignKey(Local, on_delete=models.SET_NULL, null=True, blank=True)
    type_equipement = models.CharField("type", max_length=100, blank=True)
    marque = models.CharField(max_length=100, blank=True)
    modele = models.CharField("modèle", max_length=100, blank=True)
    numero_serie = models.CharField("n° de série", max_length=100, blank=True)
    date_mise_en_service = models.DateField(null=True, blank=True)
    criticite = models.CharField(max_length=10, choices=CRITICITE_CHOICES, default="moyenne")
    statut = models.CharField(max_length=15, choices=STATUT_CHOICES, default="service")
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sous_equipements",
    )
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.nom


class PlanMaintenance(models.Model):
    equipement = models.ForeignKey(Equipement, on_delete=models.CASCADE, related_name="plans")
    tache = models.CharField("tâche", max_length=200)
    periodicite_jours = models.PositiveIntegerField("périodicité (jours)")
    duree_estimee_heures = models.DecimalField(
        "durée estimée (h)", max_digits=5, decimal_places=1, null=True, blank=True
    )
    derniere_realisation = models.DateField(null=True, blank=True)
    actif = models.BooleanField(default=True)

    @property
    def prochaine_echeance(self):
        if self.derniere_realisation:
            return self.derniere_realisation + timedelta(days=self.periodicite_jours)
        return date.today()

    def __str__(self):
        return f"{self.equipement} - {self.tache}"


class OrdreTravail(models.Model):
    TYPE_CHOICES = [("preventif", "Préventif"), ("correctif", "Correctif")]
    PRIORITE_CHOICES = [
        ("basse", "Basse"),
        ("normale", "Normale"),
        ("haute", "Haute"),
        ("urgente", "Urgente"),
    ]
    STATUT_CHOICES = [
        ("ouvert", "Ouvert"),
        ("en_cours", "En cours"),
        ("termine", "Terminé"),
        ("annule", "Annulé"),
    ]

    equipement = models.ForeignKey(Equipement, on_delete=models.CASCADE, related_name="ordres")
    plan = models.ForeignKey(PlanMaintenance, on_delete=models.SET_NULL, null=True, blank=True)
    titre = models.CharField(max_length=200)
    type_ordre = models.CharField("type", max_length=10, choices=TYPE_CHOICES, default="correctif")
    priorite = models.CharField("priorité", max_length=10, choices=PRIORITE_CHOICES, default="normale")
    statut = models.CharField(max_length=10, choices=STATUT_CHOICES, default="ouvert")
    technicien = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    description = models.TextField(blank=True)
    compte_rendu = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_prevue = models.DateField(null=True, blank=True)
    date_cloture = models.DateField("date de clôture", null=True, blank=True)

def save(self, *args, **kwargs):
        if self.statut == "termine" and not self.date_cloture:
            self.date_cloture = date.today()
        super().save(*args, **kwargs)
        if self.statut == "termine" and self.plan_id:
            plan = self.plan
            if not plan.derniere_realisation or self.date_cloture > plan.derniere_realisation:
                plan.derniere_realisation = self.date_cloture
                plan.save(update_fields=["derniere_realisation"])

def __str__(self):
        return f"{self.titre} ({self.equipement})"
