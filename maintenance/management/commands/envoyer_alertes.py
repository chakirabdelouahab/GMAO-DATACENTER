from datetime import date, timedelta

from django.core.management.base import BaseCommand

from maintenance import notifications as notif
from maintenance.models import NonConformite, OrdreTravail


def _ligne_ordre(o):
    return (
        f"- prévu le {o.date_prevue:%d/%m/%Y} | {o.equipement} | {o.titre} "
        f"| priorité : {o.get_priorite_display()}"
    )


def _ligne_nc(n):
    echeance = f"{n.date_echeance:%d/%m/%Y}" if n.date_echeance else "non définie"
    return (
        f"- {n.reference} | {n.get_gravite_display()} | {n.equipement or '-'} | {n.titre} "
        f"| échéance : {echeance}"
    )


class Command(BaseCommand):
    help = "Envoie le récapitulatif quotidien aux responsables et à chaque technicien concerné."

    def add_arguments(self, parser):
        parser.add_argument(
            "--jours", type=int, default=7,
            help="Horizon en jours pour les ordres à venir (défaut : 7)",
        )
        parser.add_argument(
            "--rondes", action="store_true",
            help="Ajoute les rondes du jour non réalisées (à lancer plutôt en fin de journée)",
        )
        parser.add_argument(
            "--sans-techniciens", action="store_true",
            help="N'envoie pas aux techniciens leur récapitulatif individuel",
        )

    def handle(self, *args, **options):
        aujourdhui = date.today()
        horizon = aujourdhui + timedelta(days=options["jours"])

        actifs = OrdreTravail.objects.filter(
            statut__in=["ouvert", "en_cours"]
        ).select_related("equipement", "technicien")
        en_retard = list(actifs.filter(date_prevue__lt=aujourdhui).order_by("date_prevue"))
        a_venir = list(
            actifs.filter(date_prevue__gte=aujourdhui, date_prevue__lte=horizon).order_by("date_prevue")
        )

        nc_ouvertes = NonConformite.objects.exclude(statut="cloturee").select_related("equipement")
        nc_depassees = list(nc_ouvertes.filter(date_echeance__lt=aujourdhui).order_by("date_echeance"))
        deja = [n.pk for n in nc_depassees]
        nc_critiques = list(
            nc_ouvertes.filter(gravite="critique").exclude(pk__in=deja).order_by("date_detection")
        )
        rondes = notif.rondes_non_realisees(aujourdhui) if options["rondes"] else []

        # --- Récapitulatif des responsables ---------------------------------------------
        if not (en_retard or a_venir or nc_depassees or nc_critiques or rondes):
            self.stdout.write("Rien à signaler : aucun e-mail envoyé.")
            return

        lignes = [f"Récapitulatif GMAO du {aujourdhui:%d/%m/%Y}", ""]
        if en_retard:
            lignes += [f"ORDRES EN RETARD ({len(en_retard)})"] + [_ligne_ordre(o) for o in en_retard] + [""]
        if a_venir:
            lignes += [f"ORDRES À VENIR ({len(a_venir)}, d'ici {options['jours']} jours)"]
            lignes += [_ligne_ordre(o) for o in a_venir] + [""]
        if nc_depassees:
            lignes += [f"NON-CONFORMITÉS À ÉCHÉANCE DÉPASSÉE ({len(nc_depassees)})"]
            lignes += [_ligne_nc(n) for n in nc_depassees] + [""]
        if nc_critiques:
            lignes += [f"NON-CONFORMITÉS CRITIQUES NON CLÔTURÉES ({len(nc_critiques)})"]
            lignes += [_ligne_nc(n) for n in nc_critiques] + [""]
        if rondes:
            lignes += [f"RONDES NON RÉALISÉES ({len(rondes)})"]
            lignes += [
                f"- {r.nom} ({r.get_categorie_display()}, prévue à {r.horaire:%H:%M})" for r in rondes
            ] + [""]

        sujet = (
            f"Récapitulatif : {len(en_retard)} ordre(s) en retard, "
            f"{len(nc_depassees) + len(nc_critiques)} non-conformité(s) à traiter"
        )
        responsables = notif.adresses_responsables()
        if not responsables:
            self.stdout.write(self.style.WARNING(
                "Aucun responsable à qui écrire : ajoute une adresse e-mail à un utilisateur "
                "du groupe « Responsable »."
            ))
        elif notif.envoyer(sujet, "\n".join(lignes), responsables):
            self.stdout.write(self.style.SUCCESS(f"Récapitulatif envoyé à {len(responsables)} responsable(s)."))
        else:
            self.stdout.write(self.style.ERROR("Échec de l'envoi du récapitulatif aux responsables."))

        # --- Récapitulatif individuel des techniciens -----------------------------------
        if options["sans_techniciens"]:
            return
        par_technicien = {}
        for o in en_retard:
            if o.technicien_id:
                par_technicien.setdefault(o.technicien, {"retard": [], "avenir": []})["retard"].append(o)
        for o in a_venir:
            if o.technicien_id:
                par_technicien.setdefault(o.technicien, {"retard": [], "avenir": []})["avenir"].append(o)

        envoyes = 0
        for technicien, ordres in par_technicien.items():
            corps = [f"Bonjour {notif._nom(technicien)},", "", f"Vos ordres de travail au {aujourdhui:%d/%m/%Y} :", ""]
            if ordres["retard"]:
                corps += [f"EN RETARD ({len(ordres['retard'])})"] + [_ligne_ordre(o) for o in ordres["retard"]] + [""]
            if ordres["avenir"]:
                corps += [f"À VENIR ({len(ordres['avenir'])})"] + [_ligne_ordre(o) for o in ordres["avenir"]] + [""]
            envoyes += int(notif.envoyer(
                f"Vos ordres de travail : {len(ordres['retard'])} en retard, {len(ordres['avenir'])} à venir",
                "\n".join(corps),
                notif.adresse(technicien),
            ))
        if par_technicien:
            self.stdout.write(self.style.SUCCESS(
                f"Récapitulatifs individuels envoyés : {envoyes} sur {len(par_technicien)} technicien(s)."
            ))