from datetime import date, time, timedelta

from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Lot(models.Model):
    code = models.CharField(max_length=20, unique=True)
    nom = models.CharField(max_length=100)

    class Meta:
        verbose_name = "lot technique"
        verbose_name_plural = "lots techniques"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.nom}"


class Local(models.Model):
    nom = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "local"
        verbose_name_plural = "locaux"

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
    lot = models.ForeignKey(Lot, on_delete=models.SET_NULL, null=True, blank=True)
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

    quantite = models.PositiveIntegerField("quantité", default=1, help_text="0 = non dénombré")

    class Meta:
        verbose_name = "équipement"
        verbose_name_plural = "équipements"

    def clean(self):
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError({"parent": "Un équipement ne peut pas être son propre parent."})

    def __str__(self):
        return self.nom


class PlanMaintenance(models.Model):
    PERIODICITE_CHOICES = [
        (7, "Hebdomadaire"),
        (30, "Mensuelle"),
        (90, "Trimestrielle"),
        (180, "Semestrielle"),
        (365, "Annuelle"),
    ]

    equipement = models.ForeignKey(Equipement, on_delete=models.CASCADE, related_name="plans")
    tache = models.CharField("tâche", max_length=200)
    periodicite_jours = models.PositiveIntegerField("périodicité", choices=PERIODICITE_CHOICES)
    duree_estimee_heures = models.DecimalField(
        "durée estimée (h)", max_digits=5, decimal_places=1, null=True, blank=True
    )
    derniere_realisation = models.DateField(null=True, blank=True)
    actif = models.BooleanField(default=True)
    premiere_echeance = models.DateField("première échéance", null=True, blank=True)

    class Meta:
        verbose_name = "plan de maintenance"
        verbose_name_plural = "plans de maintenance"

    @property
    def prochaine_echeance(self):
        if self.derniere_realisation:
            return self.derniere_realisation + timedelta(days=self.periodicite_jours)
        return self.premiere_echeance or date.today()

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

    class Meta:
        verbose_name = "ordre de travail"
        verbose_name_plural = "ordres de travail"

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


class NonConformite(models.Model):
    GRAVITE_CHOICES = [
        ("mineure", "Mineure"),
        ("majeure", "Majeure"),
        ("critique", "Critique"),
    ]
    STATUT_CHOICES = [
        ("ouverte", "Ouverte"),
        ("en_traitement", "En traitement"),
        ("corrigee", "Corrigée"),
        ("cloturee", "Clôturée"),
    ]
    ORIGINE_CHOICES = [
        ("audit", "Audit"),
        ("inspection", "Inspection"),
        ("maintenance", "Maintenance"),
        ("incident", "Incident"),
        ("autre", "Autre"),
    ]

    reference = models.CharField("référence", max_length=20, blank=True, editable=False)
    titre = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    lot = models.ForeignKey(Lot, on_delete=models.SET_NULL, null=True, blank=True)
    equipement = models.ForeignKey(
        Equipement, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="non_conformites", verbose_name="équipement",
    )
    local = models.ForeignKey(Local, on_delete=models.SET_NULL, null=True, blank=True)
    origine = models.CharField(max_length=15, choices=ORIGINE_CHOICES, default="inspection")
    gravite = models.CharField("gravité", max_length=10, choices=GRAVITE_CHOICES, default="mineure")
    statut = models.CharField(max_length=15, choices=STATUT_CHOICES, default="ouverte")
    date_detection = models.DateField("date de détection", default=date.today)
    detecte_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="nc_detectees", verbose_name="détectée par",
    )
    action_corrective = models.TextField("action corrective", blank=True)
    responsable_action = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="nc_a_traiter", verbose_name="responsable de l'action",
    )
    date_echeance = models.DateField("échéance de correction", null=True, blank=True)
    date_cloture = models.DateField("date de clôture", null=True, blank=True)
    ordre_travail = models.ForeignKey(
        OrdreTravail, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="non_conformites", verbose_name="ordre de travail associé",
    )

    class Meta:
        verbose_name = "non-conformité"
        verbose_name_plural = "non-conformités"
        ordering = ["-date_detection"]

    def save(self, *args, **kwargs):
        if self.equipement_id and not self.lot_id and self.equipement.lot_id:
            self.lot_id = self.equipement.lot_id
        if self.statut == "cloturee" and not self.date_cloture:
            self.date_cloture = date.today()
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f"NC-{self.pk:04d}"
            super().save(update_fields=["reference"])

    def __str__(self):
        return f"{self.reference} - {self.titre}"


    

class Ronde(models.Model):
    CATEGORIE_CHOICES = [
        ("technique", "Technique (relevés et mesures)"),
        ("passage", "Passage (contrôle visuel)"),
        ("ambiance", "Ambiance"),
    ]
    FREQUENCE_CHOICES = [
        ("quotidienne", "Quotidienne"),
        ("hebdomadaire", "Hebdomadaire"),
        ("mensuelle", "Mensuelle"),
    ]

    nom = models.CharField(max_length=150)
    categorie = models.CharField("catégorie", max_length=10, choices=CATEGORIE_CHOICES, default="technique")
    lot = models.ForeignKey(Lot, on_delete=models.SET_NULL, null=True, blank=True)
    local = models.ForeignKey(Local, on_delete=models.SET_NULL, null=True, blank=True)
    equipement = models.ForeignKey(
        Equipement, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="rondes", verbose_name="équipement",
    )
    horaire = models.TimeField(default=time(9, 0))
    frequence = models.CharField("fréquence", max_length=15, choices=FREQUENCE_CHOICES, default="quotidienne")
    consignes = models.TextField(blank=True)
    actif = models.BooleanField(default=True)

    class Meta:
        verbose_name = "ronde technique"
        verbose_name_plural = "rondes techniques"
        ordering = ["categorie", "nom"]

    def __str__(self):
        return self.nom


class PointControle(models.Model):
    TYPE_CHOICES = [
        ("mesure", "Mesure (valeur numérique)"),
        ("etat", "État (OK / NOK)"),
        ("observation", "Observation (texte)"),
    ]

    ronde = models.ForeignKey(Ronde, on_delete=models.CASCADE, related_name="points")
    ordre = models.PositiveIntegerField(default=1)
    libelle = models.CharField("libellé", max_length=200)
    equipement = models.ForeignKey(
        Equipement, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="points_controle", verbose_name="équipement",
    )
    type_releve = models.CharField("type de relevé", max_length=12, choices=TYPE_CHOICES, default="mesure")
    unite = models.CharField("unité", max_length=20, blank=True)
    seuil_min = models.DecimalField("seuil mini", max_digits=12, decimal_places=3, null=True, blank=True)
    seuil_max = models.DecimalField("seuil maxi", max_digits=12, decimal_places=3, null=True, blank=True)
    actif = models.BooleanField(default=True)

    class Meta:
        verbose_name = "point de contrôle"
        verbose_name_plural = "points de contrôle"
        ordering = ["ronde", "ordre"]

    def __str__(self):
        return f"{self.ronde} - {self.libelle}"


class RondeRealisee(models.Model):
    STATUT_CHOICES = [("en_cours", "En cours"), ("terminee", "Terminée")]

    ronde = models.ForeignKey(Ronde, on_delete=models.PROTECT, related_name="realisations")
    date_realisation = models.DateTimeField("date de réalisation", default=timezone.now)
    intervenant = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    statut = models.CharField(max_length=10, choices=STATUT_CHOICES, default="en_cours")
    observations = models.TextField(blank=True)

    class Meta:
        verbose_name = "ronde réalisée"
        verbose_name_plural = "rondes réalisées"
        ordering = ["-date_realisation"]

    def __str__(self):
        return f"{self.ronde} - {self.date_realisation:%d/%m/%Y %H:%M}"


class Releve(models.Model):
    ronde_realisee = models.ForeignKey(RondeRealisee, on_delete=models.CASCADE, related_name="releves")
    point = models.ForeignKey(PointControle, on_delete=models.PROTECT, related_name="releves")
    valeur = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    etat_ok = models.BooleanField("état OK", null=True, blank=True)
    commentaire = models.CharField(max_length=300, blank=True)
    conforme = models.BooleanField(default=True, editable=False)

    class Meta:
        verbose_name = "relevé"
        verbose_name_plural = "relevés"
        constraints = [
            models.UniqueConstraint(fields=["ronde_realisee", "point"], name="un_releve_par_point"),
        ]

    def evaluer(self):
        point = self.point
        if point.type_releve == "mesure":
            if self.valeur is None:
                return True
            if point.seuil_min is not None and self.valeur < point.seuil_min:
                return False
            if point.seuil_max is not None and self.valeur > point.seuil_max:
                return False
            return True
        if point.type_releve == "etat":
            return self.etat_ok is not False
        return True

    def save(self, *args, **kwargs):
        self.conforme = self.evaluer()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.point.libelle} : {self.valeur if self.valeur is not None else self.etat_ok}"


class EcheancePlanning(models.Model):
    plan = models.ForeignKey(PlanMaintenance, on_delete=models.CASCADE, related_name="echeances")
    semaine = models.DateField("semaine (lundi)")
    numero = models.PositiveSmallIntegerField("numéro", default=1, help_text="Rang dans la série : T1, T2...")

    class Meta:
        verbose_name = "échéance du planning"
        verbose_name_plural = "échéances du planning"
        ordering = ["plan", "semaine"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "semaine"], name="une_echeance_par_semaine_et_plan"),
        ]

    def __str__(self):
        return f"{self.plan} - semaine du {self.semaine:%d/%m/%Y}"