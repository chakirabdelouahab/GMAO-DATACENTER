from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render

from .models import Equipement, Local


@login_required
def liste_equipements(request):
    equipements = Equipement.objects.select_related("local").order_by("nom")

    recherche = request.GET.get("q", "").strip()
    local_id = request.GET.get("local", "")
    criticite = request.GET.get("criticite", "")
    statut = request.GET.get("statut", "")

    if recherche:
        equipements = equipements.filter(
            Q(nom__icontains=recherche)
            | Q(marque__icontains=recherche)
            | Q(modele__icontains=recherche)
            | Q(numero_serie__icontains=recherche)
        )
    if local_id:
        equipements = equipements.filter(local_id=local_id)
    if criticite:
        equipements = equipements.filter(criticite=criticite)
    if statut:
        equipements = equipements.filter(statut=statut)

    contexte = {
        "equipements": equipements,
        "locaux": Local.objects.order_by("nom"),
        "criticite_choices": Equipement.CRITICITE_CHOICES,
        "statut_choices": Equipement.STATUT_CHOICES,
        "recherche": recherche,
        "local_id": local_id,
        "criticite": criticite,
        "statut": statut,
    }
    return render(request, "maintenance/liste_equipements.html", contexte)