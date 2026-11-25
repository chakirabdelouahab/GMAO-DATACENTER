from django.urls import path

from . import views

urlpatterns = [
    path("", views.tableau_de_bord, name="tableau_de_bord"),
    path("equipements/", views.liste_equipements, name="liste_equipements"),
    path("equipements/<int:pk>/", views.fiche_equipement, name="fiche_equipement"),
]