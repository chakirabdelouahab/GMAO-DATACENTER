from django.urls import path

from . import views

urlpatterns = [
    path("", views.tableau_de_bord, name="tableau_de_bord"),
    path("equipements/", views.liste_equipements, name="liste_equipements"),
    path("equipements/<int:pk>/", views.fiche_equipement, name="fiche_equipement"),
    path("ordres/imprimer/", views.imprimer_ordres, name="imprimer_ordres"),
    path("ordres/<int:pk>/imprimer/", views.imprimer_ordre, name="imprimer_ordre"),
    path("rondes/", views.rondes_du_jour, name="rondes_du_jour"),
    path("indicateur/<str:code>/", views.detail_indicateur, name="detail_indicateur"),
    path("rondes/<int:pk>/saisir/", views.saisir_ronde, name="saisir_ronde"),
    path("rondes/<int:pk>/passage/", views.valider_passage, name="valider_passage"),
    path("rondes/realisations/<int:pk>/", views.detail_realisation, name="detail_realisation"),
    path("rapports/interventions/", views.rapport_interventions, name="rapport_interventions"),
    path("rapports/interventions/<int:pk>/", views.rapport_intervention, name="rapport_intervention"),
    path("rapports/non-conformites/", views.rapport_non_conformites, name="rapport_non_conformites"),
    path("rapports/non-conformites/<int:pk>/", views.fiche_non_conformite, name="fiche_non_conformite"),
    path("rapports/mesures/", views.historique_mesures, name="historique_mesures"),
    path("planning/", views.planning_maintenance, name="planning_maintenance"),
    path("medias/<path:chemin>", views.servir_media, name="servir_media"),
]