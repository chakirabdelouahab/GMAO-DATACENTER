from django.urls import path

from . import views

urlpatterns = [
    path("equipements/", views.liste_equipements, name="liste_equipements"),
]