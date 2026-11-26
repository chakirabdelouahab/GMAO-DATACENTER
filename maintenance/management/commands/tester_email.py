from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Envoie un e-mail de test pour vérifier la configuration d'envoi."

    def add_arguments(self, parser):
        parser.add_argument("destinataire", help="Adresse e-mail qui recevra le test")

    def handle(self, *args, **options):
        moteur = settings.MAILERS.get("default", {}).get("BACKEND", "SMTP (par défaut)")
        self.stdout.write(f"Moteur d'envoi : {moteur}")
        try:
            send_mail(
                "[GMAO GBS DC] Test d'envoi",
                "Ceci est un e-mail de test envoyé par la GMAO GBS DC.\nSi tu le lis, l'envoi fonctionne.",
                None,
                [options["destinataire"]],
            )
        except Exception as erreur:
            raise CommandError(f"Échec de l'envoi : {erreur}")
        self.stdout.write(self.style.SUCCESS(f"E-mail de test envoyé à {options['destinataire']}."))