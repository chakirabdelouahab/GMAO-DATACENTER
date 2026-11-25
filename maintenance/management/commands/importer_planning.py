from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from maintenance.import_planning import lire_planning, premiere_echeance
from maintenance.models import Equipement, Lot, PlanMaintenance

# Lot du planning Excel -> (code, nom du lot dans la GMAO).
# Proposition de regroupement : modifie-la librement avant de lancer l'import.
CFO = ("CFO", "Courants forts")
CFA = ("CFA", "Courants faibles")
SSI = ("SSI", "Sécurité incendie")
CVC = ("CVC", "CVC (climatisation, ventilation)")
PLB = ("PLB", "Plomberie")
TRV = ("TRV", "Équipements transverses")
SO = ("SO", "Seconds œuvres")

LOTS = {
    "Courant Fort": CFO,
    "Contrôle d'Accès / Barrière Physique": CFA,
    "Détection Intrusion": CFA,
    "Détection Inondation": CFA,
    "Vidéosurveillance": CFA,
    "Voix - Données - Images": CFA,
    "Report d'alarme": CFA,
    "GTC - GTB": CFA,
    "Détection Incendie": SSI,
    "Extinction Automatique": SSI,
    "Protection Incendie": SSI,
    "Climatisation": CVC,
    "Ventilation - Traitement d'air": CVC,
    "Plomberie - Sanitaire - Assainissement": PLB,
    "Traitement Des Eaux": PLB,
    "Pompage": PLB,
    "Gestion d'Arrosage": PLB,
    "Equipements Transverses": TRV,
    "Seconds-Oeuvres": SO,
}
LOT_PAR_DEFAUT = ("AUTRE", "Autres lots")


class Command(BaseCommand):
    help = "Importe les équipements (familles) et les plans de maintenance depuis le planning Excel."

    def add_arguments(self, parser):
        parser.add_argument("fichier", help="Chemin du fichier Excel (.xlsx)")
        parser.add_argument(
            "--simulation", action="store_true",
            help="Affiche ce qui serait importé, sans rien enregistrer",
        )
        parser.add_argument(
            "--depuis", type=date.fromisoformat, default=date.today(),
            help="Date de référence AAAA-MM-JJ : la première échéance est la première semaine planifiée à partir de cette date (défaut : aujourd'hui)",
        )
        parser.add_argument(
            "--lundi", type=date.fromisoformat, default=None,
            help="Lundi de la 1re semaine du planning, AAAA-MM-JJ (par défaut : lu dans le titre du fichier)",
        )

    def handle(self, *args, **options):
        try:
            familles, avertissements = lire_planning(options["fichier"], options["lundi"])
        except FileNotFoundError:
            raise CommandError(f"Fichier introuvable : {options['fichier']}")

        for message in avertissements:
            self.stdout.write(self.style.WARNING(message))

        compte = {"lots": 0, "equipements": 0, "equipements_maj": 0, "plans": 0, "plans_maj": 0}
        inconnus = set()

        with transaction.atomic():
            for fiche in familles:
                if fiche["lot"] not in LOTS:
                    inconnus.add(fiche["lot"])
                code, nom_lot = LOTS.get(fiche["lot"], LOT_PAR_DEFAUT)
                lot, cree = Lot.objects.get_or_create(code=code, defaults={"nom": nom_lot})
                compte["lots"] += int(cree)

                equipement, cree = Equipement.objects.get_or_create(
                    nom=fiche["famille"], type_equipement=fiche["lot"],
                    defaults={"lot": lot, "quantite": fiche["quantite"]},
                )
                if cree:
                    compte["equipements"] += 1
                else:
                    equipement.lot = lot
                    equipement.quantite = fiche["quantite"]
                    equipement.save(update_fields=["lot", "quantite"])
                    compte["equipements_maj"] += 1

                for plan in fiche["plans"]:
                    echeance = premiere_echeance(plan, options["depuis"])
                    objet, cree = PlanMaintenance.objects.get_or_create(
                        equipement=equipement, tache=plan["tache"],
                        defaults={
                            "periodicite_jours": plan["jours"],
                            "premiere_echeance": echeance,
                            "actif": True,
                        },
                    )
                    if cree:
                        compte["plans"] += 1
                    elif objet.derniere_realisation is None:
                        objet.periodicite_jours = plan["jours"]
                        objet.premiere_echeance = echeance
                        objet.save(update_fields=["periodicite_jours", "premiere_echeance"])
                        compte["plans_maj"] += 1

            if options["simulation"]:
                transaction.set_rollback(True)

        if inconnus:
            self.stdout.write(self.style.WARNING(
                "Lots absents du tableau LOTS (classés dans « Autres lots ») : " + ", ".join(sorted(inconnus))
            ))
        titre = "SIMULATION (rien n'a été enregistré)" if options["simulation"] else "Import terminé"
        self.stdout.write(self.style.SUCCESS(
            f"{titre} : {compte['lots']} lot(s) créé(s), "
            f"{compte['equipements']} équipement(s) créé(s), {compte['equipements_maj']} mis à jour, "
            f"{compte['plans']} plan(s) créé(s), {compte['plans_maj']} mis à jour."
        ))