"""Notifications par e-mail de la GMAO (ordres assignés, non-conformités, valeurs hors seuil)."""
import logging
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


def _lien(nom_url, *args):
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    return f"{base}{reverse(nom_url, args=args)}"


def _fmt(nombre):
    """Affiche un Decimal sans zéros inutiles (27.000 -> 27)."""
    return format(nombre.normalize(), "f")


def _nom(utilisateur):
    return utilisateur.get_full_name() or utilisateur.get_username()


def adresses_responsables():
    """Adresses des utilisateurs actifs du groupe « Responsable »."""
    User = get_user_model()
    return list(
        User.objects.filter(groups__name="Responsable", is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
        .distinct()
    )


def adresse(utilisateur):
    if utilisateur and utilisateur.is_active and utilisateur.email:
        return [utilisateur.email]
    return []


def envoyer(sujet, corps, destinataires):
    """Envoie un e-mail sans jamais faire échouer l'action de l'utilisateur."""
    destinataires = sorted({d for d in destinataires if d})
    if not destinataires:
        logger.info("Notification « %s » ignorée : aucun destinataire.", sujet)
        return False
    try:
        send_mail(f"[GMAO GBS DC] {sujet}", corps, None, destinataires)
    except Exception:
        logger.exception("Échec de l'envoi de la notification « %s ».", sujet)
        return False
    return True


def notifier_ordre_assigne(ordre):
    technicien = ordre.technicien
    prevue = ordre.date_prevue.strftime("%d/%m/%Y") if ordre.date_prevue else "non définie"
    lignes = [
        f"Bonjour {_nom(technicien)},",
        "",
        "Un ordre de travail vous a été assigné :",
        "",
        f"  N° : OT-{ordre.id:05d}",
        f"  Titre : {ordre.titre}",
        f"  Équipement : {ordre.equipement}",
        f"  Type : {ordre.get_type_ordre_display()}",
        f"  Priorité : {ordre.get_priorite_display()}",
        f"  Date prévue : {prevue}",
    ]
    if ordre.description:
        lignes += ["", "Description :", ordre.description]
    lignes += ["", f"Fiche à imprimer : {_lien('imprimer_ordre', ordre.id)}"]
    return envoyer(
        f"Ordre assigné : OT-{ordre.id:05d} - {ordre.titre}",
        "\n".join(lignes),
        adresse(technicien),
    )


def notifier_non_conformite(nc, nouvelle=True):
    destinataires = adresses_responsables() + adresse(nc.responsable_action)
    echeance = nc.date_echeance.strftime("%d/%m/%Y") if nc.date_echeance else "non définie"
    lignes = [
        "Une non-conformité nécessite votre attention :" if nouvelle
        else "La gravité d'une non-conformité a été modifiée :",
        "",
        f"  Référence : {nc.reference}",
        f"  Titre : {nc.titre}",
        f"  Gravité : {nc.get_gravite_display()}",
        f"  Statut : {nc.get_statut_display()}",
        f"  Lot : {nc.lot or '-'}",
        f"  Équipement : {nc.equipement or '-'}",
        f"  Local : {nc.local or '-'}",
        f"  Échéance de correction : {echeance}",
    ]
    if nc.description:
        lignes += ["", "Description :", nc.description]
    lignes += ["", f"Fiche : {_lien('fiche_non_conformite', nc.id)}"]
    quand = "" if nouvelle else " (gravité modifiée)"
    return envoyer(
        f"Non-conformité {nc.get_gravite_display().lower()} {nc.reference}{quand} : {nc.titre}",
        "\n".join(lignes),
        destinataires,
    )


def notifier_hors_seuil(ronde, realisation, releves_hors, nc=None, nc_existante=None):
    quand = timezone.localtime(realisation.date_realisation)
    intervenant = _nom(realisation.intervenant) if realisation.intervenant else "-"
    lignes = [
        f"Des valeurs hors seuil ont été relevées lors de la ronde « {ronde.nom} ».",
        "",
        f"  Date : {quand:%d/%m/%Y à %H:%M}",
        f"  Intervenant : {intervenant}",
        f"  Équipement : {ronde.equipement or '-'}",
        f"  Local : {ronde.local or '-'}",
        "",
        "Valeurs hors seuil :",
    ]
    for releve in releves_hors:
        p = releve.point
        if p.type_releve == "mesure":
            seuils = []
            if p.seuil_min is not None:
                seuils.append(f"mini {_fmt(p.seuil_min)}")
            if p.seuil_max is not None:
                seuils.append(f"maxi {_fmt(p.seuil_max)}")
            valeur = f"{_fmt(releve.valeur)} {p.unite}".strip()
            lignes.append(f"- {p.libelle} : {valeur} ({', '.join(seuils)})")
        else:
            lignes.append(f"- {p.libelle} : état NOK")
    if nc:
        lignes += ["", f"Non-conformité créée : {nc.reference} - {_lien('fiche_non_conformite', nc.id)}"]
    elif nc_existante:
        lignes += [
            "", f"Une non-conformité est déjà ouverte pour cet équipement : {nc_existante.reference} "
            f"- {_lien('fiche_non_conformite', nc_existante.id)}",
        ]
    lignes += ["", f"Détail de la ronde : {_lien('detail_realisation', realisation.id)}"]
    return envoyer(
        f"Valeur hors seuil : {ronde.equipement or ronde.nom}",
        "\n".join(lignes),
        adresses_responsables(),
    )


def rondes_non_realisees(jour=None):
    """Rondes actives qui n'ont pas été réalisées pour la période en cours (jour / semaine / mois)."""
    from .models import Ronde, RondeRealisee

    jour = jour or date.today()
    debut_min = min(jour - timedelta(days=jour.weekday()), jour.replace(day=1))
    dates = {}
    for ligne in RondeRealisee.objects.filter(
        statut="terminee",
        date_realisation__date__gte=debut_min,
        date_realisation__date__lte=jour,
    ).values("ronde_id", "date_realisation"):
        dates.setdefault(ligne["ronde_id"], []).append(timezone.localtime(ligne["date_realisation"]).date())

    manquantes = []
    rondes = Ronde.objects.filter(actif=True).select_related("local", "equipement")
    for ronde in rondes.order_by("categorie", "horaire", "nom"):
        if ronde.frequence == "hebdomadaire":
            debut = jour - timedelta(days=jour.weekday())
        elif ronde.frequence == "mensuelle":
            debut = jour.replace(day=1)
        else:
            debut = jour
        if not any(d >= debut for d in dates.get(ronde.id, [])):
            manquantes.append(ronde)
    return manquantes