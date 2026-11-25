import json
from datetime import time
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from maintenance.models import Equipement, Local, PointControle, Ronde


def _decimal(valeur):
    return None if valeur is None else Decimal(str(valeur))


class Command(BaseCommand):
    help = "Importe les équipements, rondes techniques et rondes de passage depuis un fichier JSON."

    def add_arguments(self, parser):
        parser.add_argument("fichier", help="Chemin du fichier JSON (ex. donnees/rondes.json)")
        parser.add_argument(
            "--simulation", action="store_true",
            help="Affiche ce qui serait importé, sans rien enregistrer",
        )

    def handle(self, *args, **options):
        try:
            with open(options["fichier"], encoding="utf-8") as f:
                donnees = json.load(f)
        except FileNotFoundError:
            raise CommandError(f"Fichier introuvable : {options['fichier']}")

        compte = {
            "locaux": 0, "equipements": 0, "equipements_maj": 0,
            "rondes": 0, "rondes_maj": 0, "points": 0,
        }
        sans_famille = set()
        cache_locaux = {}

        def local_pour(nom, emplacement):
            if not nom:
                return None
            if nom not in cache_locaux:
                local = Local.objects.filter(nom=nom).first()
                if local is None:
                    local = Local.objects.create(nom=nom, description=emplacement or "")
                    compte["locaux"] += 1
                cache_locaux[nom] = local
            return cache_locaux[nom]

        with transaction.atomic():
            # 1) Équipements individuels, rattachés à leur famille du planning
            for e in donnees.get("equipements", []):
                famille = Equipement.objects.filter(nom=e["famille"]).order_by("id").first()
                if famille is None:
                    sans_famille.add(e["famille"])
                valeurs = {
                    "type_equipement": e.get("type", ""),
                    "marque": e.get("marque", ""),
                    "modele": e.get("modele", ""),
                    "local": local_pour(e.get("local"), e.get("emplacement")),
                    "lot": famille.lot if famille else None,
                    "parent": famille,
                }
                equipement, cree = Equipement.objects.get_or_create(
                    nom=e["code"], defaults={**valeurs, "quantite": 1}
                )
                if cree:
                    compte["equipements"] += 1
                else:
                    # Équipement déjà saisi : on complète seulement ce qui est vide
                    for champ, valeur in valeurs.items():
                        actuel = getattr(equipement, champ)
                        if valeur and not actuel:
                            setattr(equipement, champ, valeur)
                    equipement.save()
                    compte["equipements_maj"] += 1

            # 2) Rondes et points de contrôle
            for r in donnees.get("rondes", []):
                equipement = None
                if r.get("equipement"):
                    equipement = Equipement.objects.filter(nom=r["equipement"]).first()
                heure, minute = (int(x) for x in r.get("horaire", "09:00").split(":"))
                valeurs = {
                    "categorie": r.get("categorie", "technique"),
                    "equipement": equipement,
                    "local": local_pour(r.get("local"), r.get("emplacement")),
                    "lot": equipement.lot if equipement else None,
                    "horaire": time(heure, minute),
                }
                ronde, cree = Ronde.objects.get_or_create(nom=r["nom"], defaults=valeurs)
                if cree:
                    compte["rondes"] += 1
                else:
                    for champ, valeur in valeurs.items():
                        setattr(ronde, champ, valeur)
                    ronde.save()
                    compte["rondes_maj"] += 1

                for ordre, p in enumerate(r.get("points", []), start=1):
                    _, cree = PointControle.objects.get_or_create(
                        ronde=ronde, libelle=p["libelle"],
                        defaults={
                            "ordre": ordre,
                            "equipement": equipement,
                            "type_releve": p.get("type", "mesure"),
                            "unite": p.get("unite", ""),
                            "seuil_min": _decimal(p.get("seuil_min")),
                            "seuil_max": _decimal(p.get("seuil_max")),
                        },
                    )
                    compte["points"] += int(cree)

            if options["simulation"]:
                transaction.set_rollback(True)

        if sans_famille:
            self.stdout.write(self.style.WARNING(
                "Familles absentes du planning (équipements créés sans parent) : "
                + ", ".join(sorted(sans_famille))
                + " -> importe d'abord le planning avec importer_planning."
            ))
        titre = "SIMULATION (rien n'a été enregistré)" if options["simulation"] else "Import terminé"
        self.stdout.write(self.style.SUCCESS(
            f"{titre} : {compte['locaux']} local(aux) créé(s), "
            f"{compte['equipements']} équipement(s) créé(s) et {compte['equipements_maj']} complété(s), "
            f"{compte['rondes']} ronde(s) créée(s) et {compte['rondes_maj']} mise(s) à jour, "
            f"{compte['points']} point(s) de contrôle créé(s)."
        ))