# GMAO GBS DC : commandes Docker

Toutes les commandes se tapent depuis le dossier du projet (celui qui contient `docker-compose.yml`).

## Démarrer, arrêter, mettre à jour

| Action | Commande |
|---|---|
| Démarrer (ou appliquer une modification) | `docker compose up -d` |
| Démarrer en reconstruisant l'application | `docker compose up -d --build` |
| Arrêter sans rien supprimer | `docker compose stop` |
| Redémarrer l'application seule | `docker compose restart web` |
| Voir l'état des services | `docker compose ps` |
| Voir les journaux de l'application | `docker compose logs web --tail 50` |
| Suivre les journaux en direct | `docker compose logs -f web` |

Mise à jour après des modifications du code :

```
git pull
docker compose up -d --build
```

Après une modification du fichier `.env` :

```
docker compose up -d --force-recreate web
```

## Sauvegardes

Une sauvegarde est faite à chaque démarrage du service, puis chaque nuit à l'heure réglée dans `.env` (`SAUVEGARDE_HEURE`). Les fichiers sont dans le dossier `sauvegardes/`, conservés `SAUVEGARDE_JOURS` jours.

| Action | Commande |
|---|---|
| Sauvegarde immédiate | `docker compose restart sauvegarde` |
| Vérifier la dernière sauvegarde | `docker compose logs sauvegarde --tail 3` |

Restaurer une sauvegarde (remplace les données actuelles). Remplace le nom du fichier :

```
docker compose stop web
$dump = "gmao_2026-11-27_0115.dump"
docker compose cp "sauvegardes/$dump" db:/tmp/restauration.dump
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner /tmp/restauration.dump'
docker compose exec -T db rm /tmp/restauration.dump
docker compose start web
```

Tester une sauvegarde sans toucher à la base réelle :

```
$dump = (Get-ChildItem sauvegardes\*.dump | Sort-Object LastWriteTime | Select-Object -Last 1).Name
docker compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" test_restauration'
docker compose cp "sauvegardes/$dump" db:/tmp/test.dump
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d test_restauration --no-owner /tmp/test.dump'
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d test_restauration -t -A -c "select ''equipements = '' || count(*) from maintenance_equipement"'
docker compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" test_restauration; rm -f /tmp/test.dump'
```

Le dossier `sauvegardes/` est sur la même machine que la base : il faut le copier régulièrement sur un autre support.

## Commandes de la GMAO

| Action | Commande |
|---|---|
| Générer les ordres préventifs | `docker compose exec -T web python manage.py generer_preventifs --jours 14` |
| Récapitulatif quotidien | `docker compose exec -T web python manage.py envoyer_alertes` |
| Récapitulatif de fin de journée (rondes) | `docker compose exec -T web python manage.py envoyer_alertes --rondes --sans-techniciens` |
| Tester l'envoi d'e-mail | `docker compose exec -T web python manage.py tester_email ton.adresse@domaine.ma` |
| Créer un compte administrateur | `docker compose exec web python manage.py createsuperuser` |
| Vérifier le projet | `docker compose exec -T web python manage.py check` |

## Dépannage

| Problème | Piste |
|---|---|
| `failed to connect to the docker API` | Docker n'est pas démarré |
| `port is already allocated` | un autre programme utilise le port 8000 |
| Page sans mise en forme | consulter `docker compose logs web` |
| Erreur « CSRF » à la connexion | vérifier `DJANGO_CSRF_TRUSTED_ORIGINS` dans `.env` |
| Le service `web` redémarre en boucle | consulter `docker compose logs web --tail 50` |

## À ne jamais faire

- `docker compose down -v` : l'option `-v` supprime le volume de la base, donc toutes les données.
- Publier le fichier `.env` ou le dossier `donnees/` sur GitHub.
- Changer `POSTGRES_PASSWORD` dans `.env` après la première création de la base.