from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .models import Equipement, Local, OrdreTravail


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

@login_required
def tableau_de_bord(request):
    aujourdhui = date.today()
    horizon = aujourdhui + timedelta(days=14)

    actifs = OrdreTravail.objects.filter(
        statut__in=["ouvert", "en_cours"]
    ).select_related("equipement", "technicien")

    en_retard = actifs.filter(date_prevue__lt=aujourdhui).order_by("date_prevue")
    a_venir = actifs.filter(
        date_prevue__gte=aujourdhui, date_prevue__lte=horizon
    ).order_by("date_prevue")
    sans_date = actifs.filter(date_prevue__isnull=True).order_by("-date_creation")

    contexte = {
        "nb_ouverts": OrdreTravail.objects.filter(statut="ouvert").count(),
        "nb_en_cours": OrdreTravail.objects.filter(statut="en_cours").count(),
        "nb_en_retard": en_retard.count(),
        "nb_a_venir": a_venir.count(),
        "en_retard": en_retard,
        "a_venir": a_venir,
        "sans_date": sans_date,
        "equipements_alerte": Equipement.objects.filter(
            statut__in=["panne", "veille", "hors_service"]
        ).select_related("local").order_by("statut", "nom"),
    }
    return render(request, "maintenance/tableau_de_bord.html", contexte)


@login_required
def fiche_equipement(request, pk):
    equipement = get_object_or_404(Equipement.objects.select_related("local", "parent"), pk=pk)
    contexte = {
        "equipement": equipement,
        "plans": equipement.plans.order_by("tache"),
        "ordres": equipement.ordres.select_related("technicien").order_by("-date_creation"),
        "sous_equipements": equipement.sous_equipements.order_by("nom"),
    }
    return render(request, "maintenance/fiche_equipement.html", contexte)