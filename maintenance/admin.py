from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html
from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef
from django.forms.models import BaseInlineFormSet

from .models import (
    EcheancePlanning, Equipement, Local, Lot, NonConformite, OrdreTravail, PieceJointe,
    PlanMaintenance, PointControle, Procedure, Releve, Ronde, RondeRealisee,
)

admin.site.register(Local)


@admin.register(Lot)
class LotAdmin(admin.ModelAdmin):
    list_display = ("code", "nom")


@admin.register(Equipement)
class EquipementAdmin(admin.ModelAdmin):
    list_display = ("nom", "lot", "local", "quantite", "marque", "modele", "criticite", "statut")
    list_filter = ("lot", "local", "criticite", "statut")
    search_fields = ("nom", "numero_serie", "marque", "modele", "type_equipement")


# --- Procédures MOP / SOP ---------------------------------------------------------------

@admin.register(Procedure)
class ProcedureAdmin(admin.ModelAdmin):
    list_display = ("reference", "titre", "type_procedure", "version", "statut", "date_revision", "lot", "ouvrir")
    list_filter = ("type_procedure", "statut", "lot")
    search_fields = ("reference", "titre", "equipements__nom")
    autocomplete_fields = ("equipements", "plans")
    readonly_fields = ("ajoute_par", "date_ajout")

    @admin.display(description="Fichier")
    def ouvrir(self, obj):
        return format_html('<a href="{}" target="_blank">Ouvrir</a>', obj.fichier.url)

    def save_model(self, request, obj, form, change):
        if not change and not obj.ajoute_par_id:
            obj.ajoute_par = request.user
        super().save_model(request, obj, form, change)


class ProcedurePlanInline(admin.TabularInline):
    model = Procedure.plans.through
    extra = 0
    autocomplete_fields = ("procedure",)
    verbose_name = "procédure liée (MOP / SOP)"
    verbose_name_plural = "procédures liées (MOP / SOP)"


# --- Pièces jointes (rapports signés, photos) -------------------------------------------

class PieceJointeInline(admin.TabularInline):
    model = PieceJointe
    extra = 1
    fields = ("type_piece", "fichier", "legende", "apercu", "ajoute_par", "date_ajout")
    readonly_fields = ("apercu", "ajoute_par", "date_ajout")

    @admin.display(description="Aperçu")
    def apercu(self, obj):
        if not obj.pk or not obj.fichier:
            return "-"
        if obj.est_image:
            return format_html(
                '<a href="{0}" target="_blank"><img src="{0}" style="max-height:60px"></a>',
                obj.fichier.url,
            )
        return format_html('<a href="{}" target="_blank">Ouvrir</a>', obj.fichier.url)


class PiecesJointesMixin:
    """Enregistre qui a ajouté chaque pièce jointe."""

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for objet in formset.deleted_objects:
            objet.delete()
        for instance in instances:
            if isinstance(instance, PieceJointe) and not instance.ajoute_par_id:
                instance.ajoute_par = request.user
            instance.save()
        formset.save_m2m()


class EcheancePlanningInline(admin.TabularInline):
    model = EcheancePlanning
    extra = 0
    fields = ("semaine", "numero")


@admin.register(PlanMaintenance)
class PlanMaintenanceAdmin(admin.ModelAdmin):
    list_display = (
        "equipement", "tache", "periodicite_jours",
        "derniere_realisation", "prochaine_echeance", "actif",
    )
    list_filter = ("actif", "periodicite_jours")
    search_fields = ("equipement__nom", "tache")
    inlines = [EcheancePlanningInline, ProcedurePlanInline]


# --- Règle de clôture : un ordre ne passe à « Terminé » qu'avec un rapport signé ---------
# Pour désactiver la règle, mets False.
RAPPORT_SIGNE_OBLIGATOIRE = True


class RapportSigneFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if not RAPPORT_SIGNE_OBLIGATOIRE or self.instance.statut != "termine":
            return
        if self.instance.pk:
            ancien = (
                OrdreTravail.objects.filter(pk=self.instance.pk)
                .values_list("statut", flat=True).first()
            )
            if ancien == "termine":
                return  # ordre déjà terminé : la règle ne s'applique qu'à la clôture
        for form in self.forms:
            donnees = getattr(form, "cleaned_data", None) or {}
            if donnees.get("DELETE"):
                continue
            if donnees.get("type_piece") == "rapport_signe" and donnees.get("fichier"):
                return
        raise ValidationError(
            "Pour terminer cet ordre, joignez d'abord le rapport signé et scanné "
            "(type « Rapport signé (scan) »), puis enregistrez."
        )


class PieceJointeOrdreInline(PieceJointeInline):
    formset = RapportSigneFormSet


class RapportSigneFilter(admin.SimpleListFilter):
    title = "rapport signé"
    parameter_name = "rapport_signe"

    def lookups(self, request, model_admin):
        return [("manquant", "Terminés sans rapport signé"), ("joint", "Avec rapport signé")]

    def queryset(self, request, queryset):
        if self.value() == "manquant":
            return queryset.filter(statut="termine", a_rapport=False)
        if self.value() == "joint":
            return queryset.filter(a_rapport=True)
        return queryset


@admin.register(OrdreTravail)
class OrdreTravailAdmin(PiecesJointesMixin, admin.ModelAdmin):
    list_display = (
        "titre", "equipement", "type_ordre", "priorite", "statut", "date_prevue", "rapport_signe",
    )
    list_filter = ("type_ordre", "priorite", "statut", RapportSigneFilter)
    search_fields = ("titre", "equipement__nom")
    actions = ["imprimer_selection"]
    inlines = [PieceJointeOrdreInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            a_rapport=Exists(
                PieceJointe.objects.filter(ordre_travail=OuterRef("pk"), type_piece="rapport_signe")
            )
        )

    @admin.display(description="Rapport signé", ordering="a_rapport")
    def rapport_signe(self, obj):
        if obj.a_rapport:
            return "✔"
        return "⚠ manquant" if obj.statut == "termine" else "-"

    @admin.action(description="Imprimer les ordres sélectionnés")
    def imprimer_selection(self, request, queryset):
        ids = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        return HttpResponseRedirect(f"{reverse('imprimer_ordres')}?ids={ids}")


@admin.register(NonConformite)
class NonConformiteAdmin(PiecesJointesMixin, admin.ModelAdmin):
    list_display = (
        "reference", "titre", "lot", "equipement", "gravite",
        "statut", "date_detection", "date_echeance",
    )
    list_filter = ("lot", "gravite", "statut", "origine")
    search_fields = ("reference", "titre", "equipement__nom")
    readonly_fields = ("reference",)
    inlines = [PieceJointeInline]

    def save_model(self, request, obj, form, change):
        if not change and not obj.detecte_par_id:
            obj.detecte_par = request.user
        super().save_model(request, obj, form, change)


class PointControleInline(admin.TabularInline):
    model = PointControle
    extra = 1
    fields = ("ordre", "libelle", "equipement", "type_releve", "unite", "seuil_min", "seuil_max", "actif")


@admin.register(Ronde)
class RondeAdmin(admin.ModelAdmin):
    list_display = ("nom", "categorie", "equipement", "local", "horaire", "frequence", "actif")
    list_filter = ("categorie", "lot", "frequence", "actif")
    search_fields = ("nom", "equipement__nom", "local__nom")
    inlines = [PointControleInline]


class ReleveInline(admin.TabularInline):
    model = Releve
    extra = 0
    fields = ("point", "valeur", "etat_ok", "commentaire", "conforme")
    readonly_fields = ("conforme",)


@admin.register(RondeRealisee)
class RondeRealiseeAdmin(admin.ModelAdmin):
    list_display = ("ronde", "date_realisation", "intervenant", "statut", "nb_hors_seuil")
    list_filter = ("ronde", "statut")
    inlines = [ReleveInline]

    @admin.display(description="Hors seuil")
    def nb_hors_seuil(self, obj):
        return obj.releves.filter(conforme=False).count()

    def save_model(self, request, obj, form, change):
        if not change and not obj.intervenant_id:
            obj.intervenant = request.user
        super().save_model(request, obj, form, change)