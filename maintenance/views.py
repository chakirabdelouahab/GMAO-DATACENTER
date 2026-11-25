from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .models import Equipement, Local, Lot, NonConformite, OrdreTravail


@login_required
def liste_equipements(request):
    equipements = Equipement.objects.select_related("local", "lot").order_by("nom")

    recherche = request.GET.get("q", "").strip()
    lot_id = request.GET.get("lot", "")
    local_id = request.GET.get("local", "")
    criticite = request.GET.get("criticite", "")
    statut = request.GET.get("statut", "")

    if recherche:
        equipements = equipements.filter(
            Q(nom__icontains=recherche)
            | Q(marque__icontains=recherche)
            | Q(modele__icontains=recherche)
            | Q(numero_serie__icontains=recherche)
            | Q(type_equipement__icontains=recherche)
        )
    if lot_id:
        equipements = equipements.filter(lot_id=lot_id)
    if local_id:
        equipements = equipements.filter(local_id=local_id)
    if criticite:
        equipements = equipements.filter(criticite=criticite)
    if statut:
        equipements = equipements.filter(statut=statut)

    contexte = {
        "equipements": equipements,
        "lots": Lot.objects.order_by("code"),
        "locaux": Local.objects.order_by("nom"),
        "criticite_choices": Equipement.CRITICITE_CHOICES,
        "statut_choices": Equipement.STATUT_CHOICES,
        "recherche": recherche,
        "lot_id": lot_id,
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

    # Tri sur la gravité : l'ordre alphabétique donne critique, majeure, mineure.
    nc_ouvertes = (
        NonConformite.objects.filter(statut__in=["ouverte", "en_traitement"])
        .select_related("equipement", "lot")
        .order_by("gravite", "date_echeance")
    )

    contexte = {
        "aujourdhui": aujourdhui,
        "nb_ouverts": OrdreTravail.objects.filter(statut="ouvert").count(),
        "nb_en_cours": OrdreTravail.objects.filter(statut="en_cours").count(),
        "nb_en_retard": en_retard.count(),
        "nb_a_venir": a_venir.count(),
        "en_retard": en_retard,
        "a_venir": a_venir,
        "sans_date": sans_date,
        "nb_nc_ouvertes": nc_ouvertes.count(),
        "nb_nc_critiques": nc_ouvertes.filter(gravite="critique").count(),
        "nc_ouvertes": nc_ouvertes,
        "equipements_alerte": Equipement.objects.filter(
            statut__in=["panne", "veille", "hors_service"]
        ).select_related("local").order_by("statut", "nom"),
    }
    return render(request, "maintenance/tableau_de_bord.html", contexte)


@login_required
def fiche_equipement(request, pk):
    equipement = get_object_or_404(
        Equipement.objects.select_related("local", "lot", "parent"), pk=pk
    )
    contexte = {
        "equipement": equipement,
        "plans": equipement.plans.order_by("tache"),
        "ordres": equipement.ordres.select_related("technicien").order_by("-date_creation"),
        "sous_equipements": equipement.sous_equipements.order_by("nom"),
        "non_conformites": equipement.non_conformites.order_by("-date_detection"),
    }
    return render(request, "maintenance/fiche_equipement.html", contexte)


@login_required
def imprimer_ordre(request, pk):
    ordre = get_object_or_404(
        OrdreTravail.objects.select_related(
            "equipement", "equipement__local", "equipement__lot", "plan", "technicien"
        ),
        pk=pk,
    )
    return render(request, "maintenance/imprimer_ordres.html", {"ordres": [ordre]})


@login_required
def imprimer_ordres(request):
    ordres = OrdreTravail.objects.select_related(
        "equipement", "equipement__local", "equipement__lot", "plan", "technicien"
    )
    ids = request.GET.get("ids", "")
    if ids:
        liste = [int(i) for i in ids.split(",") if i.isdigit()]
        ordres = ordres.filter(pk__in=liste)
    else:
        jours = request.GET.get("jours", "14")
        jours = int(jours) if jours.isdigit() else 14
        horizon = date.today() + timedelta(days=jours)
        ordres = ordres.filter(statut__in=["ouvert", "en_cours"]).filter(
            Q(date_prevue__lte=horizon) | Q(date_prevue__isnull=True)
        )
    ordres = ordres.order_by("date_prevue", "equipement__nom")
    return render(request, "maintenance/imprimer_ordres.html", {"ordres": ordres})