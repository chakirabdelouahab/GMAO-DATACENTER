"""Lecture du planning de maintenance préventive (Excel) - sans dépendance à Django."""
import re
from datetime import date, timedelta

from openpyxl import load_workbook

PREMIERE_COLONNE_SEMAINE = 8  # colonne I (index 8) : première semaine du planning

FREQUENCES = {
    "H": ("hebdomadaire", 7),
    "M": ("mensuelle", 30),
    "T": ("trimestrielle", 90),
    "S": ("semestrielle", 180),
    "A": ("annuelle", 365),
}


def _propre(valeur):
    return " ".join(str(valeur).split()) if valeur is not None else ""


def _entier(valeur):
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return 0


def _lundi_initial(feuille, lundi_force=None):
    if lundi_force:
        return lundi_force
    titre = str(feuille.cell(row=1, column=1).value or "")
    m = re.search(r"Date D[ée]but\s*:\s*(\d{2})/(\d{2})/(\d{4})", titre)
    if not m:
        raise ValueError("Date de début introuvable dans la 1re cellule : utilise --lundi AAAA-MM-JJ")
    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    while d.weekday() != 0:  # premier lundi à partir de la date de début
        d += timedelta(days=1)
    return d


def lire_planning(chemin, lundi_force=None):
    """Retourne (familles, avertissements).

    familles : liste de dicts {lot, famille, quantite, plans:[{freq, tache, jours, dates}]}
    """
    feuille = load_workbook(chemin, data_only=True).active
    lundi0 = _lundi_initial(feuille, lundi_force)
    familles = {}
    avertissements = []

    for num, ligne in enumerate(feuille.iter_rows(min_row=1, values_only=True), start=1):
        lot, famille = _propre(ligne[0]), _propre(ligne[1])
        if not lot or not famille or lot == "Lot Technique":
            continue  # titres, en-têtes répétés, lignes vides

        cle = (lot, famille)
        fiche = familles.setdefault(
            cle, {"lot": lot, "famille": famille, "quantite": 0, "plans": []}
        )
        fiche["quantite"] = max(fiche["quantite"], _entier(ligne[5]))

        par_lettre = {}
        for col in range(PREMIERE_COLONNE_SEMAINE, len(ligne)):
            marque = _propre(ligne[col])
            if not marque:
                continue
            lettre = re.sub(r"[\s\d]+", "", marque).upper()
            if lettre not in FREQUENCES:
                avertissements.append(f"Ligne {num} ({famille}) : code « {marque} » ignoré")
                continue
            lundi = lundi0 + timedelta(days=7 * (col - PREMIERE_COLONNE_SEMAINE))
            par_lettre.setdefault(lettre, []).append(lundi)

        for lettre, dates in par_lettre.items():
            libelle, jours = FREQUENCES[lettre]
            tache = f"Maintenance préventive {libelle}"
            deja = sum(1 for p in fiche["plans"] if p["freq"] == lettre)
            if deja:
                tache += f" ({deja + 1})"
            fiche["plans"].append(
                {"freq": lettre, "tache": tache, "jours": jours, "dates": sorted(dates)}
            )

    return list(familles.values()), avertissements


def premiere_echeance(plan, depuis):
    """Première date planifiée à partir de la semaine de `depuis`, sinon dernière date + périodicité."""
    lundi_courant = depuis - timedelta(days=depuis.weekday())
    futures = [d for d in plan["dates"] if d >= lundi_courant]
    if futures:
        return futures[0]
    return plan["dates"][-1] + timedelta(days=plan["jours"])