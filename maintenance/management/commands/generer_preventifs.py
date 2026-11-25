from datetime import date, timedelta

from django.core.management.base import BaseCommand

from maintenance.models import OrdreTravail, PlanMaintenance


class Command(BaseCommand):
    help = "Génère les ordres de travail préventifs arrivant à échéance."

    def add_arguments(self, parser):
        parser.add_argument(
            "--jours", type=int, default=7,
            help="Horizon en jours : les échéances jusqu'à cette date sont traitées (défaut : 7)",
        )

    def handle(self, *args, **options):
        limite = date.today() + timedelta(days=options["jours"])
        crees = 0

        plans = PlanMaintenance.objects.filter(actif=True).select_related("equipement")
        for plan in plans:
            echeance = plan.prochaine_echeance
            if echeance > limite:
                continue

            deja_ouvert = OrdreTravail.objects.filter(
                plan=plan, statut__in=["ouvert", "en_cours"]
            ).exists()
            if deja_ouvert:
                continue

            OrdreTravail.objects.create(
                equipement=plan.equipement,
                plan=plan,
                titre=f"{plan.equipement.nom} - {plan.tache}",
                type_ordre="preventif",
                priorite="normale",
                description=f"Ordre généré automatiquement (périodicité : {plan.periodicite_jours} jours).",
                date_prevue=echeance,
            )
            crees += 1

        self.stdout.write(self.style.SUCCESS(f"{crees} ordre(s) préventif(s) créé(s)."))