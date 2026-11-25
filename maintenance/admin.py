from django.contrib import admin
from .models import Local, Equipement, PlanMaintenance, OrdreTravail

admin.site.register(Local)



@admin.register(Equipement)
class EquipementAdmin(admin.ModelAdmin):
    list_display = ("nom", "local", "marque", "modele", "criticite", "statut")
    list_filter = ("local", "criticite", "statut")
    search_fields = ("nom", "numero_serie", "marque", "modele")


@admin.register(OrdreTravail)
class OrdreTravailAdmin(admin.ModelAdmin):
    list_display = ("titre", "equipement", "type_ordre", "priorite", "statut", "date_prevue")
    list_filter = ("type_ordre", "priorite", "statut")
    search_fields = ("titre", "equipement__nom")

@admin.register(PlanMaintenance)
class PlanMaintenanceAdmin(admin.ModelAdmin):
    list_display = (
        "equipement", "tache", "periodicite_jours",
        "derniere_realisation", "prochaine_echeance", "actif",
    )
    list_filter = ("actif",)