import csv
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    Equipement, Local, Lot, NonConformite, OrdreTravail, PointControle, Releve, Ronde,
    RondeRealisee,
)
from .notifications import notifier_hors_seuil


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
        ),
        pk=pk,
    )
    ecart = None
    if ordre.date_cloture and ordre.date_prevue:
        ecart = (ordre.date_cloture - ordre.date_prevue).days
    contexte = {
        "ordre": ordre,
        "ecart": ecart,
        "non_conformites": ordre.non_conformites.all(),
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
        ),
        pk=pk,
    )
    return render(request, "maintenance/fiche_non_conformite.html", {"nc": nc})


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