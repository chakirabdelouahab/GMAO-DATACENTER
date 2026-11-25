from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse

from .models import (
    Equipement, Local, Lot, NonConformite, OrdreTravail, PlanMaintenance,
    PointControle, Releve, Ronde, RondeRealisee,
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


@admin.register(PlanMaintenance)
class PlanMaintenanceAdmin(admin.ModelAdmin):
    list_display = (
        "equipement", "tache", "periodicite_jours",
        "derniere_realisation", "prochaine_echeance", "actif",
    )
    list_filter = ("actif", "periodicite_jours")


@admin.register(OrdreTravail)
class OrdreTravailAdmin(admin.ModelAdmin):
    list_display = ("titre", "equipement", "type_ordre", "priorite", "statut", "date_prevue")
    list_filter = ("type_ordre", "priorite", "statut")
    search_fields = ("titre", "equipement__nom")
    actions = ["imprimer_selection"]

    @admin.action(description="Imprimer les ordres sélectionnés")
    def imprimer_selection(self, request, queryset):
        ids = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        return HttpResponseRedirect(f"{reverse('imprimer_ordres')}?ids={ids}")


@admin.register(NonConformite)
class NonConformiteAdmin(admin.ModelAdmin):
    list_display = (
        "reference", "titre", "lot", "equipement", "gravite",
        "statut", "date_detection", "date_echeance",
    )
    list_filter = ("lot", "gravite", "statut", "origine")
    search_fields = ("reference", "titre", "equipement__nom")
    readonly_fields = ("reference",)

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
    list_display = ("nom", "lot", "local", "frequence", "actif")
    list_filter = ("lot", "frequence", "actif")
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