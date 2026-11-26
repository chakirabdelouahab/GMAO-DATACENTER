from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    Equipement, Local, Lot, NonConformite, OrdreTravail, Releve, Ronde, RondeRealisee,
)


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
        NonConformite.objects.exclude(statut="cloturee")
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


# ----------------------------------------------------------------------------
# Rondes techniques
# ----------------------------------------------------------------------------

CATEGORIES_AFFICHAGE = [
    ("technique", "Rondes techniques"),
    ("passage", "Rondes de passage"),
    ("ambiance", "Rondes d'ambiance"),
]


def _debut_periode(frequence, jour):
    """Début de la période pendant laquelle une seule réalisation est attendue."""
    if frequence == "hebdomadaire":
        return jour - timedelta(days=jour.weekday())
    if frequence == "mensuelle":
        return jour.replace(day=1)
    return jour


def _fmt(nombre):
    """Affiche un Decimal sans zéros inutiles (27.000 -> 27)."""
    return format(nombre.normalize(), "f")


@login_required
def rondes_du_jour(request):
    aujourdhui = date.today()
    try:
        jour = date.fromisoformat(request.GET["date"]) if request.GET.get("date") else aujourdhui
    except ValueError:
        jour = aujourdhui

    debut_min = min(jour - timedelta(days=jour.weekday()), jour.replace(day=1))
    realisations = (
        RondeRealisee.objects
        .filter(date_realisation__date__gte=debut_min, date_realisation__date__lte=jour)
        .annotate(nb_hors=Count("releves", filter=Q(releves__conforme=False)))
        .select_related("intervenant")
        .order_by("date_realisation")
    )
    par_ronde = {}
    for r in realisations:
        par_ronde.setdefault(r.ronde_id, []).append(r)

    rondes = (
        Ronde.objects.filter(actif=True)
        .select_related("equipement", "local")
        .order_by("horaire", "nom")
    )

    groupes = []
    for code, titre in CATEGORIES_AFFICHAGE:
        lignes = []
        for ronde in rondes:
            if ronde.categorie != code:
                continue
            debut = _debut_periode(ronde.frequence, jour)
            faites = [
                r for r in par_ronde.get(ronde.id, [])
                if timezone.localtime(r.date_realisation).date() >= debut
            ]
            realisation = faites[-1] if faites else None
            if realisation is None:
                etat = "a_faire"
            elif realisation.statut == "en_cours":
                etat = "en_cours"
            elif realisation.nb_hors:
                etat = "hors_seuil"
            else:
                etat = "faite"
            lignes.append({
                "ronde": ronde,
                "realisation": realisation,
                "etat": etat,
                "heure": timezone.localtime(realisation.date_realisation) if realisation else None,
            })
        if not lignes:
            continue
        groupes.append({
            "code": code,
            "titre": titre,
            "lignes": lignes,
            "planifiees": len(lignes),
            "faites": sum(1 for l in lignes if l["etat"] in ("faite", "hors_seuil")),
            "non_realisees": sum(1 for l in lignes if l["etat"] in ("a_faire", "en_cours")),
            "hors_seuil": sum(1 for l in lignes if l["etat"] == "hors_seuil"),
        })

    contexte = {
        "jour": jour,
        "modifiable": jour == aujourdhui,
        "groupes": groupes,
        "total_planifiees": sum(g["planifiees"] for g in groupes),
        "total_faites": sum(g["faites"] for g in groupes),
        "total_non_realisees": sum(g["non_realisees"] for g in groupes),
        "total_hors_seuil": sum(g["hors_seuil"] for g in groupes),
    }
    return render(request, "maintenance/rondes_du_jour.html", contexte)


def _creer_nc_hors_seuil(ronde, realisation, releves_hors, utilisateur):
    """Crée une non-conformité pour les relevés hors seuil, sauf si une est déjà ouverte."""
    equipement = ronde.equipement
    titre = f"Relevé hors seuil - {equipement.nom if equipement else ronde.nom}"
    existante = NonConformite.objects.filter(titre=titre).exclude(statut="cloturee").first()
    if existante:
        return None, existante

    quand = timezone.localtime(realisation.date_realisation)
    lignes = [f"Constaté lors de la ronde « {ronde.nom} » du {quand:%d/%m/%Y à %H:%M}.", ""]
    for releve in releves_hors:
        p = releve.point
        if p.type_releve == "mesure":
            seuils = []
            if p.seuil_min is not None:
                seuils.append(f"mini {_fmt(p.seuil_min)}")
            if p.seuil_max is not None:
                seuils.append(f"maxi {_fmt(p.seuil_max)}")
            lignes.append(
                f"- {p.libelle} : {_fmt(releve.valeur)} {p.unite} ({', '.join(seuils)})".replace("  ", " ")
            )
        else:
            lignes.append(f"- {p.libelle} : état NOK")

    nc = NonConformite.objects.create(
        titre=titre,
        description="\n".join(lignes),
        lot=ronde.lot,
        equipement=equipement,
        local=ronde.local,
        origine="inspection",
        gravite="mineure",
        detecte_par=utilisateur,
    )
    return nc, None


@login_required
def saisir_ronde(request, pk):
    ronde = get_object_or_404(Ronde.objects.select_related("equipement", "local"), pk=pk)
    points = list(
        ronde.points.filter(actif=True).select_related("equipement").order_by("ordre", "id")
    )
    envoi = request.method == "POST"
    lignes = [
        {
            "point": p,
            "valeur": request.POST.get(f"valeur_{p.id}", "") if envoi else "",
            "commentaire": request.POST.get(f"commentaire_{p.id}", "") if envoi else "",
        }
        for p in points
    ]
    observations = request.POST.get("observations", "").strip() if envoi else ""

    if envoi:
        erreurs, saisies = [], []
        for ligne in lignes:
            p = ligne["point"]
            brut = ligne["valeur"].strip()
            commentaire = ligne["commentaire"].strip()
            valeur, etat = None, None
            if p.type_releve == "mesure":
                if brut:
                    try:
                        valeur = Decimal(brut.replace(",", "."))
                        if not valeur.is_finite() or abs(valeur) >= Decimal("1e10"):
                            raise InvalidOperation
                    except InvalidOperation:
                        erreurs.append(f"{p.libelle} : valeur invalide « {brut} »")
                        continue
            elif p.type_releve == "etat":
                if brut == "ok":
                    etat = True
                elif brut == "nok":
                    etat = False
            else:
                commentaire = brut or commentaire
            if valeur is not None or etat is not None or commentaire:
                saisies.append((p, valeur, etat, commentaire[:300]))

        if not erreurs and not saisies and not observations:
            erreurs.append("Aucune valeur saisie.")

        if erreurs:
            for message in erreurs:
                messages.error(request, message)
        else:
            with transaction.atomic():
                realisation = RondeRealisee.objects.create(
                    ronde=ronde, intervenant=request.user,
                    statut="terminee", observations=observations,
                )
                hors = []
                for p, valeur, etat, commentaire in saisies:
                    releve = Releve.objects.create(
                        ronde_realisee=realisation, point=p,
                        valeur=valeur, etat_ok=etat, commentaire=commentaire,
                    )
                    if not releve.conforme:
                        hors.append(releve)
                nc, existante = None, None
                if hors and request.POST.get("creer_nc"):
                    nc, existante = _creer_nc_hors_seuil(ronde, realisation, hors, request.user)

            messages.success(request, f"Ronde enregistrée : {ronde.nom} ({len(saisies)} relevé(s)).")
            if hors:
                messages.warning(request, f"{len(hors)} valeur(s) hors seuil sur cette ronde.")
            if nc:
                messages.warning(request, f"Non-conformité {nc.reference} créée automatiquement.")
            elif existante:
                messages.info(
                    request,
                    f"Une non-conformité est déjà ouverte pour cet équipement ({existante.reference}) : aucune nouvelle créée.",
                )
            return redirect("rondes_du_jour")

    contexte = {"ronde": ronde, "lignes": lignes, "observations": observations}
    return render(request, "maintenance/saisir_ronde.html", contexte)


@login_required
@require_POST
def valider_passage(request, pk):
    ronde = get_object_or_404(Ronde, pk=pk, categorie__in=["passage", "ambiance"])
    RondeRealisee.objects.create(
        ronde=ronde, intervenant=request.user, statut="terminee",
        observations=request.POST.get("observations", "").strip()[:500],
    )
    messages.success(request, f"Passage validé : {ronde.nom}")
    return redirect("rondes_du_jour")


@login_required
def detail_realisation(request, pk):
    realisation = get_object_or_404(
        RondeRealisee.objects.select_related(
            "ronde", "ronde__equipement", "ronde__local", "intervenant"
        ),
        pk=pk,
    )
    releves = realisation.releves.select_related("point").order_by("point__ordre", "point__id")
    contexte = {
        "realisation": realisation,
        "releves": releves,
        "quand": timezone.localtime(realisation.date_realisation),
    }
    return render(request, "maintenance/detail_realisation.html", contexte)