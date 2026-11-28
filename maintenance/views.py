import csv
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.conf import settings
from django.http import Http404, HttpResponse
from django.views.static import serve
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    EcheancePlanning, Equipement, Local, Lot, NonConformite, OrdreTravail, PieceJointe,
    PlanMaintenance, PointControle, Procedure, Releve, Ronde, RondeRealisee,
)
from .notifications import notifier_hors_seuil, rondes_non_realisees


def _prefetch_procedures(chemin):
    """Charge les MOP/SOP en vigueur dans l'attribut « procedures_en_vigueur »."""
    return Prefetch(
        chemin,
        queryset=Procedure.objects.filter(statut="en_vigueur").order_by("type_procedure", "reference"),
        to_attr="procedures_en_vigueur",
    )


def _trier_pieces(pieces):
    """Sépare les pièces jointes : rapports signés, photos, autres documents."""
    rapports = [p for p in pieces if p.type_piece == "rapport_signe"]
    photos = [p for p in pieces if p.type_piece != "rapport_signe" and p.est_image]
    autres = [p for p in pieces if p.type_piece != "rapport_signe" and not p.est_image]
    return rapports, photos, autres


def _ordres_sans_rapport():
    """Ordres terminés auxquels aucun rapport signé n'est joint."""
    avec_rapport = PieceJointe.objects.filter(
        ordre_travail=OuterRef("pk"), type_piece="rapport_signe"
    )
    return (
        OrdreTravail.objects.filter(statut="termine")
        .filter(~Exists(avec_rapport))
        .select_related("equipement", "technicien")
    )


MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
               "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def _debut_mois(jour, decalage=0):
    """Premier jour du mois, décalé de « decalage » mois (négatif = passé)."""
    rang = jour.year * 12 + (jour.month - 1) + decalage
    return date(rang // 12, rang % 12 + 1, 1)


def _mois_depuis_code(texte):
    """Lit « 2026-11 » dans l'adresse d'une page de détail."""
    try:
        annee, mois = texte.split("-")
        return date(int(annee), int(mois), 1)
    except ValueError:
        raise Http404


def _hauteur(valeur, maximum):
    """Hauteur d'une barre en % (entier : la virgule décimale française casserait le CSS)."""
    if not valeur or not maximum:
        return 0
    return max(4, round(100 * valeur / maximum))


def _anneau(segments):
    """Fond CSS d'un graphique en anneau. segments : liste de dicts avec « valeur » et « couleur »."""
    total = sum(s["valeur"] for s in segments)
    if not total:
        return "#e5e5e5"
    morceaux, debut = [], 0.0
    for s in segments:
        fin = debut + 100 * s["valeur"] / total
        if s["valeur"]:
            morceaux.append(f"{s['couleur']} {debut:.2f}% {fin:.2f}%")
        debut = fin
    return "conic-gradient(" + ", ".join(morceaux) + ")"


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
    lundi = aujourdhui - timedelta(days=aujourdhui.weekday())

    actifs = OrdreTravail.objects.filter(statut__in=["ouvert", "en_cours"])
    en_retard = actifs.filter(date_prevue__lt=aujourdhui)
    a_venir = actifs.filter(date_prevue__gte=aujourdhui, date_prevue__lte=horizon)
    sans_date = actifs.filter(date_prevue__isnull=True)

    # --- Maintenance préventive -----------------------------------------------------
    preventifs = actifs.filter(type_ordre="preventif")
    prev = {
        "retard_ouverts": preventifs.filter(date_prevue__lt=aujourdhui, statut="ouvert").count(),
        "retard_en_cours": preventifs.filter(date_prevue__lt=aujourdhui, statut="en_cours").count(),
        "a_venir": preventifs.filter(date_prevue__gte=aujourdhui, date_prevue__lte=horizon).count(),
        "en_cours": preventifs.filter(statut="en_cours").count(),
    }

    # --- Non-conformités ------------------------------------------------------------
    nc_ouvertes = NonConformite.objects.exclude(statut="cloturee")
    compte_nc = dict(
        NonConformite.objects.order_by().values_list("statut").annotate(n=Count("id"))
    )
    nc_par_statut = [
        {"code": code, "libelle": libelle, "nb": compte_nc.get(code, 0)}
        for code, libelle in NonConformite.STATUT_CHOICES
        if code != "cloturee"
    ]
    nc_a_cloturer = nc_ouvertes.filter(date_echeance__lt=aujourdhui).count()

    # --- Équipements ----------------------------------------------------------------
    compte_criticite = dict(
        Equipement.objects.order_by().values_list("criticite").annotate(n=Count("id"))
    )
    equip_par_criticite = [
        {"code": code, "libelle": libelle, "nb": compte_criticite.get(code, 0)}
        for code, libelle in Equipement.CRITICITE_CHOICES
    ]
    nb_equip_alerte = Equipement.objects.filter(
        statut__in=["panne", "veille", "hors_service"]
    ).count()

    # --- Rondes du jour -------------------------------------------------------------
    rondes = {
        "realisees": RondeRealisee.objects.filter(
            date_realisation__date=aujourdhui, statut="terminee"
        ).count(),
        "non_realisees": len(rondes_non_realisees(aujourdhui)),
        "hors_seuil": Releve.objects.filter(
            conforme=False, ronde_realisee__date_realisation__date=aujourdhui
        ).count(),
    }

    # === Graphiques ==================================================================

    # 1. Ordres par statut (anneau)
    libelles_statut = dict(OrdreTravail.STATUT_CHOICES)
    couleurs_statut = {
        "ouvert": "#1a9fd9", "en_cours": "#e8761a", "termine": "#2fb82f", "annule": "#9aa0a6",
    }
    compte_statut = dict(
        OrdreTravail.objects.order_by().values_list("statut").annotate(n=Count("id"))
    )
    graphe_statuts = [
        {
            "code": code, "libelle": libelles_statut[code],
            "valeur": compte_statut.get(code, 0), "couleur": couleurs_statut[code],
        }
        for code in ("ouvert", "en_cours", "termine", "annule")
    ]

    # 2. Non-conformités non clôturées par gravité (anneau)
    couleurs_gravite = {"mineure": "#e8c21a", "majeure": "#e8761a", "critique": "#d6194f"}
    compte_gravite = dict(
        nc_ouvertes.order_by().values_list("gravite").annotate(n=Count("id"))
    )
    graphe_gravite = [
        {
            "code": code, "libelle": libelle,
            "valeur": compte_gravite.get(code, 0), "couleur": couleurs_gravite[code],
        }
        for code, libelle in NonConformite.GRAVITE_CHOICES
    ]

    # 3. Charge à venir : en retard, puis 8 semaines
    nb_semaines = 8
    charge = [0] * (nb_semaines + 1)  # indice 0 = en retard
    dates_prevues = actifs.filter(date_prevue__isnull=False).values_list("date_prevue", flat=True)
    for jour in dates_prevues:
        if jour < aujourdhui:
            charge[0] += 1
        else:
            rang = (jour - lundi).days // 7
            if rang < nb_semaines:
                charge[rang + 1] += 1
    maximum = max(charge)
    graphe_charge = [{
        "libelle": "Retard", "sous": "", "valeur": charge[0],
        "hauteur": _hauteur(charge[0], maximum), "classe": "c-rouge", "code": "ordres_retard",
    }]
    for i in range(nb_semaines):
        debut = lundi + timedelta(weeks=i)
        graphe_charge.append({
            "libelle": f"S{debut.isocalendar().week}", "sous": f"{debut:%d/%m}",
            "valeur": charge[i + 1], "hauteur": _hauteur(charge[i + 1], maximum),
            "classe": "c-bleu", "code": f"charge_{debut.isoformat()}",
        })

    # 4 et 5. Six derniers mois : ordres terminés, taux de réalisation du préventif
    mois = [_debut_mois(aujourdhui, d) for d in range(-5, 1)]
    fin_periode = _debut_mois(aujourdhui, 1)

    par_mois = {m: {"preventif": 0, "correctif": 0} for m in mois}
    termines = OrdreTravail.objects.filter(
        statut="termine", date_cloture__gte=mois[0], date_cloture__lt=fin_periode
    ).values_list("date_cloture", "type_ordre")
    for cloture, type_ordre in termines:
        par_mois[cloture.replace(day=1)][type_ordre] += 1
    maximum = max((v["preventif"] + v["correctif"] for v in par_mois.values()), default=0)
    graphe_termines = []
    for m in mois:
        v = par_mois[m]
        total = v["preventif"] + v["correctif"]
        part_prev = round(100 * v["preventif"] / total) if total else 0
        graphe_termines.append({
            "libelle": MOIS_COURTS[m.month - 1], "sous": str(m.year), "valeur": total,
            "prev": v["preventif"], "corr": v["correctif"],
            "hauteur": _hauteur(total, maximum),
            "part_prev": part_prev, "part_corr": 100 - part_prev if total else 0,
            "code": f"termines_{m:%Y-%m}",
        })

    taux = {m: [0, 0] for m in mois}  # [prévus, terminés]
    prevus = OrdreTravail.objects.filter(
        type_ordre="preventif", date_prevue__gte=mois[0], date_prevue__lt=fin_periode
    ).exclude(statut="annule").values_list("date_prevue", "statut")
    for prevue, statut in prevus:
        ligne = taux[prevue.replace(day=1)]
        ligne[0] += 1
        if statut == "termine":
            ligne[1] += 1
    graphe_taux = []
    for m in mois:
        prevus_mois, faits = taux[m]
        pct = round(100 * faits / prevus_mois) if prevus_mois else None
        if pct is None:
            classe = "c-gris"
        elif pct >= 80:
            classe = "c-vert"
        elif pct >= 50:
            classe = "c-orange"
        else:
            classe = "c-rouge"
        graphe_taux.append({
            "libelle": MOIS_COURTS[m.month - 1], "sous": str(m.year),
            "valeur": f"{pct} %" if pct is not None else "-",
            "titre": f"{faits} terminé(s) sur {prevus_mois} prévu(s)",
            "hauteur": max(3, pct) if pct else 0, "classe": classe,
            "code": f"prev_mois_{m:%Y-%m}",
        })

    contexte = {
        "aujourdhui": aujourdhui,
        "nb_ouverts": OrdreTravail.objects.filter(statut="ouvert").count(),
        "nb_en_cours": OrdreTravail.objects.filter(statut="en_cours").count(),
        "nb_en_retard": en_retard.count(),
        "nb_a_venir": a_venir.count(),
        "nb_sans_date": sans_date.count(),
        "nb_sans_rapport": _ordres_sans_rapport().count(),
        "prev": prev,
        "nb_nc_ouvertes": nc_ouvertes.count(),
        "nb_nc_critiques": nc_ouvertes.filter(gravite="critique").count(),
        "nb_nc_a_cloturer": nc_a_cloturer,
        "nc_par_statut": nc_par_statut,
        "equip_par_criticite": equip_par_criticite,
        "nb_equip_alerte": nb_equip_alerte,
        "rondes": rondes,
        "graphe_statuts": graphe_statuts,
        "fond_statuts": _anneau(graphe_statuts),
        "total_ordres": sum(s["valeur"] for s in graphe_statuts),
        "graphe_gravite": graphe_gravite,
        "fond_gravite": _anneau(graphe_gravite),
        "total_nc": sum(s["valeur"] for s in graphe_gravite),
        "graphe_charge": graphe_charge,
        "graphe_termines": graphe_termines,
        "graphe_taux": graphe_taux,
    }
    return render(request, "maintenance/tableau_de_bord.html", contexte)

@login_required
def detail_indicateur(request, code):
    """Page de détail ouverte en cliquant sur un chiffre du tableau de bord."""
    aujourdhui = date.today()
    horizon = aujourdhui + timedelta(days=14)

    actifs = OrdreTravail.objects.filter(
        statut__in=["ouvert", "en_cours"]
    ).select_related("equipement", "technicien")
    preventifs = actifs.filter(type_ordre="preventif")
    nc_base = NonConformite.objects.select_related("equipement", "lot")
    nc_ouvertes = nc_base.exclude(statut="cloturee")
    equipements = Equipement.objects.select_related("local", "lot")

    type_liste = "ordres"
    if code == "ordres_ouverts":
        titre, objets = "Ordres ouverts", actifs.filter(statut="ouvert")
    elif code == "ordres_en_cours":
        titre, objets = "Ordres en cours", actifs.filter(statut="en_cours")
    elif code == "ordres_retard":
        titre, objets = "Ordres en retard", actifs.filter(date_prevue__lt=aujourdhui)
    elif code == "ordres_avenir":
        titre = "Ordres à venir (14 jours)"
        objets = actifs.filter(date_prevue__gte=aujourdhui, date_prevue__lte=horizon)
    elif code == "ordres_sans_date":
        titre, objets = "Ordres actifs sans date prévue", actifs.filter(date_prevue__isnull=True)
    elif code == "ordres_sans_rapport":
        titre, objets = "Ordres terminés sans rapport signé", _ordres_sans_rapport()
    elif code == "prev_retard_ouverts":
        titre = "Préventifs en retard non entamés"
        objets = preventifs.filter(date_prevue__lt=aujourdhui, statut="ouvert")
    elif code == "prev_retard_en_cours":
        titre = "Préventifs en retard en cours"
        objets = preventifs.filter(date_prevue__lt=aujourdhui, statut="en_cours")
    elif code == "prev_avenir":
        titre = "Préventifs à venir (14 jours)"
        objets = preventifs.filter(date_prevue__gte=aujourdhui, date_prevue__lte=horizon)
    elif code == "prev_en_cours":
        titre, objets = "Préventifs en cours", preventifs.filter(statut="en_cours")
    elif code.startswith("nc_statut_"):
        statut = code[len("nc_statut_"):]
        libelles = dict(NonConformite.STATUT_CHOICES)
        if statut not in libelles:
            raise Http404
        type_liste = "nc"
        titre, objets = f"Non-conformités : {libelles[statut]}", nc_base.filter(statut=statut)
    elif code == "nc_critiques":
        type_liste = "nc"
        titre, objets = "Non-conformités critiques non clôturées", nc_ouvertes.filter(gravite="critique")
    elif code == "nc_depassees":
        type_liste = "nc"
        titre = "Non-conformités à échéance dépassée"
        objets = nc_ouvertes.filter(date_echeance__lt=aujourdhui)
    elif code == "rondes_realisees":
        type_liste = "realisations"
        titre = "Rondes réalisées aujourd'hui"
        objets = RondeRealisee.objects.filter(
            date_realisation__date=aujourdhui, statut="terminee"
        ).select_related("ronde", "intervenant").order_by("date_realisation")
    elif code == "rondes_non_realisees":
        type_liste = "rondes"
        titre, objets = "Rondes non réalisées", rondes_non_realisees(aujourdhui)
    elif code == "rondes_hors_seuil":
        type_liste = "releves"
        titre = "Valeurs hors seuil du jour"
        objets = Releve.objects.filter(
            conforme=False, ronde_realisee__date_realisation__date=aujourdhui
        ).select_related(
            "point", "ronde_realisee", "ronde_realisee__ronde", "ronde_realisee__intervenant"
        ).order_by("-ronde_realisee__date_realisation")
    elif code.startswith("equip_criticite_"):
        criticite = code[len("equip_criticite_"):]
        libelles = dict(Equipement.CRITICITE_CHOICES)
        if criticite not in libelles:
            raise Http404
        type_liste = "equipements"
        titre = f"Équipements de criticité : {libelles[criticite]}"
        objets = equipements.filter(criticite=criticite).order_by("nom")
    elif code == "equip_non_service":
        type_liste = "equipements"
        titre = "Équipements non en service"
        objets = equipements.filter(statut__in=["panne", "veille", "hors_service"]).order_by("statut", "nom")
    # --- Pages ouvertes depuis les graphiques ---
    elif code.startswith("ordres_statut_"):
        statut = code[len("ordres_statut_"):]
        libelles = dict(OrdreTravail.STATUT_CHOICES)
        if statut not in libelles:
            raise Http404
        titre = f"Ordres : {libelles[statut]}"
        objets = OrdreTravail.objects.filter(statut=statut).select_related("equipement", "technicien")
    elif code.startswith("charge_"):
        try:
            lundi_semaine = date.fromisoformat(code[len("charge_"):])
        except ValueError:
            raise Http404
        titre = f"Ordres prévus la semaine du {lundi_semaine:%d/%m/%Y}"
        objets = actifs.filter(
            date_prevue__gte=max(lundi_semaine, aujourdhui),
            date_prevue__lt=lundi_semaine + timedelta(days=7),
        )
    elif code.startswith("termines_"):
        mois = _mois_depuis_code(code[len("termines_"):])
        titre = f"Ordres terminés en {mois:%m/%Y}"
        objets = OrdreTravail.objects.filter(
            statut="termine", date_cloture__year=mois.year, date_cloture__month=mois.month
        ).select_related("equipement", "technicien")
    elif code.startswith("prev_mois_"):
        mois = _mois_depuis_code(code[len("prev_mois_"):])
        titre = f"Préventifs prévus en {mois:%m/%Y} (hors annulés)"
        objets = OrdreTravail.objects.filter(
            type_ordre="preventif", date_prevue__year=mois.year, date_prevue__month=mois.month
        ).exclude(statut="annule").select_related("equipement", "technicien")
    elif code.startswith("nc_gravite_"):
        gravite = code[len("nc_gravite_"):]
        libelles = dict(NonConformite.GRAVITE_CHOICES)
        if gravite not in libelles:
            raise Http404
        type_liste = "nc"
        titre = f"Non-conformités {libelles[gravite].lower()}s non clôturées"
        objets = nc_ouvertes.filter(gravite=gravite)
    else:
        raise Http404

    if type_liste == "ordres":
        objets = objets.order_by("date_prevue", "equipement__nom")
    elif type_liste == "nc":
        objets = objets.order_by("gravite", "date_echeance")

    contexte = {
        "titre": titre,
        "type_liste": type_liste,
        "objets": objets,
        "aujourdhui": aujourdhui,
    }
    return render(request, "maintenance/detail_indicateur.html", contexte)


@login_required
def fiche_equipement(request, pk):
    equipement = get_object_or_404(
        Equipement.objects.select_related("local", "lot", "parent"), pk=pk
    )
    plans = equipement.plans.order_by("tache").prefetch_related(_prefetch_procedures("procedures"))
    ordres = (
        equipement.ordres.select_related("technicien")
        .annotate(
            nb_rapports=Count(
                "pieces_jointes", filter=Q(pieces_jointes__type_piece="rapport_signe")
            ),
            nb_autres=Count(
                "pieces_jointes",
                filter=Q(pieces_jointes__type_piece__in=["photo_avant", "photo_apres", "photo", "autre"]),
            ),
        )
        .order_by("-date_creation")
    )
    non_conformites = (
        equipement.non_conformites.annotate(nb_pieces=Count("pieces_jointes"))
        .order_by("-date_detection")
    )
    contexte = {
        "equipement": equipement,
        "plans": plans,
        "procedures": equipement.procedures.filter(statut="en_vigueur").order_by(
            "type_procedure", "reference"
        ),
        "ordres": ordres,
        "sous_equipements": equipement.sous_equipements.order_by("nom"),
        "non_conformites": non_conformites,
    }
    return render(request, "maintenance/fiche_equipement.html", contexte)


@login_required
def imprimer_ordre(request, pk):
    ordre = get_object_or_404(
        OrdreTravail.objects.select_related(
            "equipement", "equipement__local", "equipement__lot", "plan", "technicien"
        ).prefetch_related(_prefetch_procedures("plan__procedures")),
        pk=pk,
    )
    return render(request, "maintenance/imprimer_ordres.html", {"ordres": [ordre]})


@login_required
def imprimer_ordres(request):
    ordres = OrdreTravail.objects.select_related(
        "equipement", "equipement__local", "equipement__lot", "plan", "technicien"
    ).prefetch_related(_prefetch_procedures("plan__procedures"))
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
                notifier_hors_seuil(ronde, realisation, hors, nc, existante)
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




# ----------------------------------------------------------------------------
# Rapports
# ----------------------------------------------------------------------------

def _periode(request, debut_defaut=None):
    """Lit les dates « du » et « au » de l'adresse (par défaut : début du mois -> aujourd'hui)."""
    aujourdhui = date.today()

    def lire(nom, defaut):
        try:
            return date.fromisoformat(request.GET[nom]) if request.GET.get(nom) else defaut
        except ValueError:
            return defaut

    debut = lire("du", debut_defaut or aujourdhui.replace(day=1))
    fin = lire("au", aujourdhui)
    if debut > fin:
        debut, fin = fin, debut
    return debut, fin


def _d(jour):
    return jour.strftime("%d/%m/%Y") if jour else ""


def _csv_response(nom_fichier, entetes, lignes):
    """Fichier CSV (séparateur « ; », accents corrects) qui s'ouvre directement dans Excel."""
    reponse = HttpResponse(content_type="text/csv; charset=utf-8")
    reponse["Content-Disposition"] = f'attachment; filename="{nom_fichier}"'
    reponse.write("\ufeff")
    ecrivain = csv.writer(reponse, delimiter=";")
    ecrivain.writerow(entetes)
    for ligne in lignes:
        ecrivain.writerow(ligne)
    return reponse


BASES_DATE = {
    "prevue": ("date prévue", "date_prevue"),
    "cloture": ("date de clôture", "date_cloture"),
    "creation": ("date de création", "date_creation__date"),
}


def _ordres_filtres(request):
    debut, fin = _periode(request)
    base = request.GET.get("base", "prevue")
    if base not in BASES_DATE:
        base = "prevue"
    champ = BASES_DATE[base][1]
    lot_id = request.GET.get("lot", "")
    type_ordre = request.GET.get("type", "")
    statut = request.GET.get("statut", "")
    recherche = request.GET.get("q", "").strip()

    ordres = OrdreTravail.objects.select_related(
        "equipement", "equipement__lot", "technicien"
    ).filter(**{f"{champ}__gte": debut, f"{champ}__lte": fin})
    if lot_id:
        ordres = ordres.filter(equipement__lot_id=lot_id)
    if type_ordre:
        ordres = ordres.filter(type_ordre=type_ordre)
    if statut:
        ordres = ordres.filter(statut=statut)
    if recherche:
        ordres = ordres.filter(Q(titre__icontains=recherche) | Q(equipement__nom__icontains=recherche))
    ordres = ordres.order_by("date_prevue", "equipement__nom")

    filtres = {
        "debut": debut, "fin": fin, "base": base, "base_libelle": BASES_DATE[base][0],
        "lot_id": lot_id, "type": type_ordre, "statut": statut, "q": recherche,
    }
    return ordres, filtres


@login_required
def rapport_interventions(request):
    ordres, filtres = _ordres_filtres(request)
    liste = list(ordres)

    if request.GET.get("export") == "csv":
        lignes = [
            [
                f"OT-{o.id:05d}", o.titre, o.equipement.nom,
                o.equipement.lot.code if o.equipement.lot else "",
                o.get_type_ordre_display(), o.get_priorite_display(), o.get_statut_display(),
                str(o.technicien) if o.technicien else "",
                _d(o.date_prevue), _d(o.date_cloture), o.description, o.compte_rendu,
            ]
            for o in liste
        ]
        return _csv_response(
            f"interventions_{filtres['debut']}_{filtres['fin']}.csv",
            ["N° OT", "Titre", "Équipement", "Lot", "Type", "Priorité", "Statut",
             "Technicien", "Date prévue", "Date de clôture", "Description", "Compte rendu"],
            lignes,
        )

    aujourdhui = date.today()
    termines = [o for o in liste if o.statut == "termine"]
    preventifs = [o for o in liste if o.type_ordre == "preventif"]
    preventifs_termines = [o for o in preventifs if o.statut == "termine"]
    en_retard = [
        o for o in liste
        if o.statut in ("ouvert", "en_cours") and o.date_prevue and o.date_prevue < aujourdhui
    ]
    ecarts = [(o.date_cloture - o.date_prevue).days for o in termines if o.date_cloture and o.date_prevue]
    stats = {
        "total": len(liste),
        "termines": len(termines),
        "preventifs": len(preventifs),
        "correctifs": len(liste) - len(preventifs),
        "taux_preventif": round(100 * len(preventifs_termines) / len(preventifs)) if preventifs else None,
        "en_retard": len(en_retard),
        "ecart_moyen": round(sum(ecarts) / len(ecarts), 1) if ecarts else None,
    }

    par_lot = {}
    for o in liste:
        code = o.equipement.lot.code if o.equipement.lot else "—"
        ligne = par_lot.setdefault(code, {"lot": code, "total": 0, "termines": 0, "en_retard": 0})
        ligne["total"] += 1
        if o.statut == "termine":
            ligne["termines"] += 1
        if o in en_retard:
            ligne["en_retard"] += 1

    contexte = {
        "f": filtres,
        "ordres": liste,
        "stats": stats,
        "par_lot": sorted(par_lot.values(), key=lambda x: x["lot"]),
        "lots": Lot.objects.order_by("code"),
        "bases": [(code, libelle) for code, (libelle, _) in BASES_DATE.items()],
        "type_choices": OrdreTravail.TYPE_CHOICES,
        "statut_choices": OrdreTravail.STATUT_CHOICES,
    }
    return render(request, "maintenance/rapport_interventions.html", contexte)


@login_required
def rapport_intervention(request, pk):
    ordre = get_object_or_404(
        OrdreTravail.objects.select_related(
            "equipement", "equipement__local", "equipement__lot", "plan", "technicien"
        ).prefetch_related(
            _prefetch_procedures("plan__procedures"),
            Prefetch(
                "pieces_jointes",
                queryset=PieceJointe.objects.select_related("ajoute_par").order_by("date_ajout"),
            ),
        ),
        pk=pk,
    )
    ecart = None
    if ordre.date_cloture and ordre.date_prevue:
        ecart = (ordre.date_cloture - ordre.date_prevue).days
    rapports, photos, autres = _trier_pieces(ordre.pieces_jointes.all())
    contexte = {
        "ordre": ordre,
        "ecart": ecart,
        "non_conformites": ordre.non_conformites.all(),
        "rapports": rapports,
        "photos": photos,
        "autres_pieces": autres,
    }
    return render(request, "maintenance/rapport_intervention.html", contexte)


def _nc_filtrees(request):
    aujourdhui = date.today()
    debut, fin = _periode(request, debut_defaut=aujourdhui.replace(month=1, day=1))
    lot_id = request.GET.get("lot", "")
    gravite = request.GET.get("gravite", "")
    statut = request.GET.get("statut", "")
    recherche = request.GET.get("q", "").strip()

    nc = NonConformite.objects.select_related("equipement", "lot", "local").filter(
        date_detection__gte=debut, date_detection__lte=fin
    )
    if lot_id:
        nc = nc.filter(lot_id=lot_id)
    if gravite:
        nc = nc.filter(gravite=gravite)
    if statut == "non_cloturees":
        nc = nc.exclude(statut="cloturee")
    elif statut:
        nc = nc.filter(statut=statut)
    if recherche:
        nc = nc.filter(
            Q(titre__icontains=recherche) | Q(reference__icontains=recherche)
            | Q(equipement__nom__icontains=recherche)
        )
    nc = nc.order_by("-date_detection", "-id")
    filtres = {
        "debut": debut, "fin": fin, "lot_id": lot_id,
        "gravite": gravite, "statut": statut, "q": recherche,
    }
    return nc, filtres


@login_required
def rapport_non_conformites(request):
    nc, filtres = _nc_filtrees(request)
    liste = list(nc)
    aujourdhui = date.today()
    for n in liste:
        n.duree = ((n.date_cloture or aujourdhui) - n.date_detection).days

    if request.GET.get("export") == "csv":
        lignes = [
            [
                n.reference, n.titre, n.lot.code if n.lot else "",
                n.equipement.nom if n.equipement else "", n.local.nom if n.local else "",
                n.get_origine_display(), n.get_gravite_display(), n.get_statut_display(),
                _d(n.date_detection), _d(n.date_echeance), _d(n.date_cloture), n.duree,
                n.description, n.action_corrective,
            ]
            for n in liste
        ]
        return _csv_response(
            f"non_conformites_{filtres['debut']}_{filtres['fin']}.csv",
            ["Référence", "Titre", "Lot", "Équipement", "Local", "Origine", "Gravité", "Statut",
             "Détectée le", "Échéance", "Clôturée le", "Durée (jours)", "Description", "Action corrective"],
            lignes,
        )

    non_cloturees = [n for n in liste if n.statut != "cloturee"]
    cloturees = [n for n in liste if n.statut == "cloturee" and n.date_cloture]
    stats = {
        "total": len(liste),
        "non_cloturees": len(non_cloturees),
        "cloturees": len([n for n in liste if n.statut == "cloturee"]),
        "critiques": len([n for n in non_cloturees if n.gravite == "critique"]),
        "depassees": len([n for n in non_cloturees if n.date_echeance and n.date_echeance < aujourdhui]),
        "delai_moyen": round(sum(n.duree for n in cloturees) / len(cloturees), 1) if cloturees else None,
    }

    par_lot, par_gravite = {}, {}
    for n in liste:
        code = n.lot.code if n.lot else "—"
        a = par_lot.setdefault(code, {"cle": code, "total": 0, "non_cloturees": 0})
        b = par_gravite.setdefault(n.gravite, {"cle": n.get_gravite_display(), "total": 0, "non_cloturees": 0})
        for ligne in (a, b):
            ligne["total"] += 1
            if n.statut != "cloturee":
                ligne["non_cloturees"] += 1

    contexte = {
        "f": filtres,
        "aujourdhui": aujourdhui,
        "non_conformites": liste,
        "stats": stats,
        "par_lot": sorted(par_lot.values(), key=lambda x: x["cle"]),
        "par_gravite": sorted(par_gravite.values(), key=lambda x: x["cle"]),
        "lots": Lot.objects.order_by("code"),
        "gravite_choices": NonConformite.GRAVITE_CHOICES,
        "statut_choices": NonConformite.STATUT_CHOICES,
    }
    return render(request, "maintenance/rapport_non_conformites.html", contexte)


@login_required
def fiche_non_conformite(request, pk):
    nc = get_object_or_404(
        NonConformite.objects.select_related(
            "equipement", "lot", "local", "detecte_par", "responsable_action", "ordre_travail"
        ).prefetch_related(
            Prefetch(
                "pieces_jointes",
                queryset=PieceJointe.objects.select_related("ajoute_par").order_by("date_ajout"),
            ),
        ),
        pk=pk,
    )
    rapports, photos, autres = _trier_pieces(nc.pieces_jointes.all())
    contexte = {"nc": nc, "rapports": rapports, "photos": photos, "autres_pieces": autres}
    return render(request, "maintenance/fiche_non_conformite.html", contexte)

def _graphique(releves, point):
    """Coordonnées d'une courbe SVG (sans bibliothèque externe). Tout est renvoyé en texte
    pour éviter que la virgule décimale française ne casse les attributs SVG."""
    if not releves:
        return None
    largeur, hauteur = 760, 280
    gauche, droite, haut, bas = 60, 20, 15, 40
    valeurs = [float(r["valeur"]) for r in releves]
    plancher = min(valeurs + ([float(point.seuil_min)] if point.seuil_min is not None else []))
    plafond = max(valeurs + ([float(point.seuil_max)] if point.seuil_max is not None else []))
    if plafond == plancher:
        plancher, plafond = plancher - 1, plafond + 1
    marge = (plafond - plancher) * 0.1
    plancher, plafond = plancher - marge, plafond + marge

    t0, t1 = releves[0]["quand"], releves[-1]["quand"]
    etendue = (t1 - t0).total_seconds()
    zone_x = largeur - gauche - droite
    zone_y = hauteur - haut - bas

    def x(t):
        if etendue == 0:
            return gauche + zone_x / 2
        return gauche + (t - t0).total_seconds() / etendue * zone_x

    def y(v):
        return haut + (plafond - v) / (plafond - plancher) * zone_y

    def txt(nombre):
        return f"{nombre:.1f}"

    points = [
        {
            "x": txt(x(r["quand"])),
            "y": txt(y(float(r["valeur"]))),
            "conforme": r["conforme"],
            "titre": f"{r['quand']:%d/%m/%Y %H:%M} : {_fmt(r['valeur'])} {point.unite}",
        }
        for r in releves
    ]
    seuils = []
    if point.seuil_min is not None:
        seuils.append({"y": txt(y(float(point.seuil_min))), "libelle": f"mini {_fmt(point.seuil_min)}"})
    if point.seuil_max is not None:
        seuils.append({"y": txt(y(float(point.seuil_max))), "libelle": f"maxi {_fmt(point.seuil_max)}"})
    graduations = []
    for i in range(5):
        valeur = plancher + (plafond - plancher) * i / 4
        graduations.append({"y": txt(y(valeur)), "texte": f"{valeur:.1f}".replace(".", ",")})

    return {
        "largeur": str(largeur),
        "hauteur": str(hauteur),
        "x_debut": txt(gauche),
        "x_fin": txt(largeur - droite),
        "y_axe": txt(hauteur - bas),
        "y_label": txt(hauteur - bas + 22),
        "ligne": " ".join(f"{p['x']},{p['y']}" for p in points),
        "points": points,
        "seuils": seuils,
        "graduations": graduations,
        "label_debut": f"{t0:%d/%m/%Y}",
        "label_fin": f"{t1:%d/%m/%Y}",
    }


@login_required
def historique_mesures(request):
    debut, fin = _periode(request, debut_defaut=date.today() - timedelta(days=30))
    points = (
        PointControle.objects.filter(type_releve="mesure", actif=True, ronde__actif=True)
        .select_related("ronde").order_by("ronde__nom", "ordre", "id")
    )

    point, releves, stats, courbe = None, [], None, None
    point_id = request.GET.get("point", "")
    if point_id.isdigit():
        point = PointControle.objects.select_related("ronde", "equipement").filter(pk=int(point_id)).first()

    if point:
        enregistrements = (
            Releve.objects.filter(
                point=point, valeur__isnull=False,
                ronde_realisee__date_realisation__date__gte=debut,
                ronde_realisee__date_realisation__date__lte=fin,
            )
            .select_related("ronde_realisee", "ronde_realisee__intervenant")
            .order_by("ronde_realisee__date_realisation")
        )
        releves = [
            {
                "quand": timezone.localtime(r.ronde_realisee.date_realisation),
                "valeur": r.valeur,
                "conforme": r.conforme,
                "intervenant": r.ronde_realisee.intervenant,
                "commentaire": r.commentaire,
                "realisation_id": r.ronde_realisee_id,
            }
            for r in enregistrements
        ]

        if request.GET.get("export") == "csv":
            return _csv_response(
                f"mesures_{point.ronde.nom}_{point.libelle}_{debut}_{fin}.csv".replace(" ", "_").replace("/", "-"),
                ["Date et heure", "Ronde", "Point de contrôle", "Valeur", "Unité", "Résultat", "Intervenant", "Commentaire"],
                [
                    [
                        r["quand"].strftime("%d/%m/%Y %H:%M"), point.ronde.nom, point.libelle,
                        str(r["valeur"]).replace(".", ","), point.unite,
                        "Conforme" if r["conforme"] else "Hors seuil",
                        str(r["intervenant"]) if r["intervenant"] else "", r["commentaire"],
                    ]
                    for r in releves
                ],
            )

        if releves:
            valeurs = [r["valeur"] for r in releves]
            stats = {
                "nb": len(releves),
                "mini": min(valeurs),
                "maxi": max(valeurs),
                "moyenne": round(sum(float(v) for v in valeurs) / len(valeurs), 2),
                "hors_seuil": len([r for r in releves if not r["conforme"]]),
                "derniere": releves[-1]["valeur"],
            }
            courbe = _graphique(releves, point)

    contexte = {
        "debut": debut,
        "fin": fin,
        "points": points,
        "point": point,
        "releves": releves,
        "stats": stats,
        "courbe": courbe,
    }
    return render(request, "maintenance/historique_mesures.html", contexte)


# ----------------------------------------------------------------------------
# Planning de maintenance préventive (grille par semaine)
# ----------------------------------------------------------------------------

LETTRES_PERIODICITE = {7: "H", 30: "M", 90: "T", 180: "S", 365: "A"}


@login_required
def planning_preventif(request):
    aujourdhui = date.today()

    def lire(nom, defaut):
        try:
            return date.fromisoformat(request.GET[nom]) if request.GET.get(nom) else defaut
        except ValueError:
            return defaut

    debut = lire("du", date(aujourdhui.year, 1, 1))
    fin = lire("au", date(aujourdhui.year, 12, 31))
    if debut > fin:
        debut, fin = fin, debut
    debut -= timedelta(days=debut.weekday())              # la grille commence un lundi
    lundi_courant = aujourdhui - timedelta(days=aujourdhui.weekday())

    # Colonnes : une par semaine
    semaines, lundi = [], debut
    while lundi <= fin:
        semaines.append({
            "lundi": lundi,
            "num": lundi.isocalendar()[1],
            "courante": lundi == lundi_courant,
        })
        lundi += timedelta(weeks=1)

    # Bandeau des mois : on regroupe les semaines par mois du lundi
    mois = []
    for s in semaines:
        cle = (s["lundi"].year, s["lundi"].month)
        if mois and mois[-1]["cle"] == cle:
            mois[-1]["n"] += 1
        else:
            mois.append({"cle": cle, "lundi": s["lundi"], "n": 1})

    ordres = (
        OrdreTravail.objects
        .filter(type_ordre="preventif", date_prevue__range=(debut, fin))
        .exclude(statut="annule")
        .select_related("equipement__lot", "plan")
        .order_by("date_prevue", "id")
    )
    lot_id = request.GET.get("lot", "")
    if lot_id:
        ordres = ordres.filter(equipement__lot_id=lot_id)

    lignes, rangs = {}, {}
    for ot in ordres:
        eq = ot.equipement
        lot_nom = eq.lot.nom if eq.lot else "Sans lot"
        famille = eq.type_equipement or "Non classé"
        cle = (lot_nom, famille)
        ligne = lignes.setdefault(cle, {"lot": lot_nom, "famille": famille, "cases": {}})

        lettre = LETTRES_PERIODICITE.get(ot.plan.periodicite_jours if ot.plan else None, "?")
        lundi_ot = ot.date_prevue - timedelta(days=ot.date_prevue.weekday())

        # Numéro d'occurrence : T1, T2... (une valeur par semaine distincte de la ligne)
        vues = rangs.setdefault((cle, lettre), [])
        if lundi_ot not in vues:
            vues.append(lundi_ot)
        numero = vues.index(lundi_ot) + 1

        if ot.statut == "termine":
            css = "termine"
        elif ot.date_prevue < aujourdhui:
            css = "retard"
        else:
            css = "planifie"

        pastille = ligne["cases"].setdefault(lundi_ot, {}).setdefault(lettre, {
            "label": f"{lettre}{numero}", "css": css, "ot": ot.pk, "n": 0, "titre": ot.titre,
        })
        pastille["n"] += 1
        if css == "retard":                    # un seul ordre en retard colore toute la case
            pastille["css"] = "retard"

    for ligne in lignes.values():
        ligne["cellules"] = [
            {"courante": s["courante"], "pastilles": list(ligne["cases"].get(s["lundi"], {}).values())}
            for s in semaines
        ]

    contexte = {
        "semaines": semaines,
        "mois": mois,
        "lignes": sorted(lignes.values(), key=lambda l: (l["lot"], l["famille"])),
        "debut": debut,
        "fin": fin,
        "lots": Lot.objects.order_by("code"),
        "lot_id": lot_id,
    }
    return render(request, "maintenance/planning.html", contexte)


# ----------------------------------------------------------------------------
# Planning de maintenance (vue par semaines)
# ----------------------------------------------------------------------------

MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
               "juil.", "août", "sept.", "oct.", "nov.", "déc."]
LETTRES_PERIODICITE = {7: "H", 30: "M", 90: "T", 180: "S", 365: "A"}
COLONNES_PLANNING = [
    ("lot", "Lot technique", 170),
    ("famille", "Famille d'équipement", 230),
    ("periodicite", "Périodicité", 115),
    ("quantite", "Nombre", 75),
    ("criticite", "Criticité", 95),
]
ETATS_PLANNING = [
    ("prevue", "Prévue au planning"),
    ("planifiee", "Ordre généré"),
    ("en_cours", "En cours"),
    ("realisee", "Réalisée"),
    ("en_retard", "En retard"),
    ("annulee", "Annulée"),
    ("passee", "Passée, sans ordre"),
]


def _lundi(jour):
    return jour - timedelta(days=jour.weekday())


def _en_tetes(semaines, lundi_courant):
    """En-têtes du planning : mois (selon le lundi, comme dans l'Excel) et numéros de semaine."""
    mois = []
    for lundi in semaines:
        cle = (lundi.year, lundi.month)
        if not mois or mois[-1]["cle"] != cle:
            mois.append({"cle": cle, "libelle": f"{MOIS_COURTS[lundi.month - 1]} {lundi.year}", "n": 0})
        mois[-1]["n"] += 1
    entetes = [
        {"num": lundi.isocalendar()[1], "jour": f"{lundi:%d}", "courante": lundi == lundi_courant}
        for lundi in semaines
    ]
    return mois, entetes


def _etat_cellule(ordre, lundi, aujourdhui):
    """Couleur d'une case : l'ordre de travail prime, sinon la semaine prévue au planning."""
    fin_semaine = lundi + timedelta(days=6)
    if ordre:
        if ordre["statut"] == "termine":
            return "realisee"
        if ordre["statut"] == "annule":
            return "annulee"
        if ordre["statut"] == "en_cours":
            return "en_cours"
        return "en_retard" if fin_semaine < aujourdhui else "planifiee"
    return "passee" if fin_semaine < aujourdhui else "prevue"


def _colonnes_choisies(choisies):
    """Colonnes d'étiquettes affichées, avec le décalage nécessaire pour les figer à gauche."""
    colonnes, gauche = [], 0
    for cle, titre, largeur in COLONNES_PLANNING:
        if cle in choisies:
            colonnes.append({
                "cle": cle, "titre": titre,
                "style": f"left:{gauche}px;min-width:{largeur}px;max-width:{largeur}px;",
            })
            gauche += largeur
    return colonnes


def _valeur_colonne(cle, plan):
    equipement = plan.equipement
    if cle == "lot":
        return equipement.type_equipement or (equipement.lot.nom if equipement.lot else "-")
    if cle == "famille":
        return equipement.nom
    if cle == "periodicite":
        return plan.get_periodicite_jours_display()
    if cle == "quantite":
        return str(equipement.quantite)
    return equipement.get_criticite_display()


@login_required
def planning_maintenance(request):
    aujourdhui = date.today()
    lundi_courant = _lundi(aujourdhui)

    def lire_date(nom, defaut):
        try:
            return date.fromisoformat(request.GET[nom]) if request.GET.get(nom) else defaut
        except ValueError:
            return defaut

    debut = _lundi(lire_date("du", lundi_courant - timedelta(weeks=4)))
    fin = _lundi(lire_date("au", lundi_courant + timedelta(weeks=26)))
    if fin < debut:
        debut, fin = fin, debut
    if (fin - debut).days // 7 + 1 > 104:  # au plus deux ans affichés
        fin = debut + timedelta(weeks=103)
    nb_semaines = (fin - debut).days // 7 + 1
    semaines = [debut + timedelta(weeks=i) for i in range(nb_semaines)]
    mois, entetes = _en_tetes(semaines, lundi_courant)
    semaine_courante = (lundi_courant - debut).days // 7
    if not 0 <= semaine_courante < nb_semaines:
        semaine_courante = None

    cles_valides = [cle for cle, _, _ in COLONNES_PLANNING]
    if "envoi" in request.GET:
        choisies = [c for c in request.GET.getlist("cols") if c in cles_valides] or ["famille"]
    else:
        choisies = ["lot", "famille"]
    colonnes = _colonnes_choisies(choisies)

    lot_id = request.GET.get("lot", "")
    periodicite = request.GET.get("periodicite", "")
    recherche = request.GET.get("q", "").strip()
    masquer_vides = request.GET.get("vides") == "1"

    plans = (
        PlanMaintenance.objects.filter(actif=True)
        .select_related("equipement", "equipement__lot")
        .order_by("equipement__lot__code", "equipement__type_equipement",
                  "equipement__nom", "periodicite_jours", "tache")
    )
    if lot_id.isdigit():
        plans = plans.filter(equipement__lot_id=int(lot_id))
    if periodicite.isdigit():
        plans = plans.filter(periodicite_jours=int(periodicite))
    if recherche:
        plans = plans.filter(
            Q(equipement__nom__icontains=recherche)
            | Q(equipement__type_equipement__icontains=recherche)
            | Q(tache__icontains=recherche)
        )

    echeances = {
        (plan_id, (semaine - debut).days // 7): rang
        for plan_id, semaine, rang in EcheancePlanning.objects.filter(
            plan__in=plans, semaine__gte=debut, semaine__lte=fin
        ).values_list("plan_id", "semaine", "numero")
    }
    ordres = {}
    for ordre in (
        OrdreTravail.objects.filter(
            plan__in=plans, date_prevue__gte=debut, date_prevue__lte=fin + timedelta(days=6)
        )
        .order_by("id")
        .values("id", "plan_id", "date_prevue", "statut")
    ):
        ordres[(ordre["plan_id"], (ordre["date_prevue"] - debut).days // 7)] = ordre

    libelles = dict(ETATS_PLANNING)
    compteurs = Counter()
    lignes, precedent = [], None
    for plan in plans:
        lettre = LETTRES_PERIODICITE.get(plan.periodicite_jours, "?")
        cellules = []
        for i, lundi in enumerate(semaines):
            rang = echeances.get((plan.id, i))
            ordre = ordres.get((plan.id, i))
            if rang is None and ordre is None:
                cellules.append(None)
                continue
            etat = _etat_cellule(ordre, lundi, aujourdhui)
            compteurs[etat] += 1
            cellules.append({
                "code": f"{lettre}{rang}" if rang is not None else lettre,
                "etat": etat,
                "ordre_id": ordre["id"] if ordre else None,
                "titre": f"{plan.tache} - semaine du {lundi:%d/%m/%Y} - {libelles[etat]}",
            })
        if masquer_vides and not any(cellules):
            continue
        labels = [
            {"texte": _valeur_colonne(c["cle"], plan), "style": c["style"]} for c in colonnes
        ]
        groupe = _valeur_colonne("lot", plan)
        lignes.append({
            "labels": labels,
            "cellules": cellules,
            "nouveau_lot": precedent is not None and groupe != precedent,
            "tache": plan.tache,
        })
        precedent = groupe

    contexte = {
        "debut": debut,
        "fin": fin,
        "mois": mois,
        "semaines": entetes,
        "semaine_courante": semaine_courante,
        "nb_colonnes_total": len(colonnes) + nb_semaines,
        "colonnes": colonnes,
        "choix_colonnes": [(cle, titre, cle in choisies) for cle, titre, _ in COLONNES_PLANNING],
        "lignes": lignes,
        "legende": ETATS_PLANNING,
        "compteurs": [(etat, libelle, compteurs[etat]) for etat, libelle in ETATS_PLANNING],
        "lots": Lot.objects.order_by("code"),
        "periodicite_choices": PlanMaintenance.PERIODICITE_CHOICES,
        "lot_id": lot_id,
        "periodicite": periodicite,
        "recherche": recherche,
        "masquer_vides": masquer_vides,
    }
    return render(request, "maintenance/planning_maintenance.html", contexte)


@login_required
def servir_media(request, chemin):
    """Fichiers téléversés : accessibles uniquement aux utilisateurs connectés."""
    reponse = serve(request, chemin, document_root=settings.MEDIA_ROOT)
    reponse["X-Content-Type-Options"] = "nosniff"
    return reponse