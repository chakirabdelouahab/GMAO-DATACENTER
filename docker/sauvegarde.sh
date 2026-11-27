#!/bin/bash
# Sauvegarde : base PostgreSQL + fichiers téléversés. Une au démarrage, puis une par jour.
export PGPASSWORD="$POSTGRES_PASSWORD"
JOURS="${SAUVEGARDE_JOURS:-14}"
HEURE="${SAUVEGARDE_HEURE:-02:00}"

faire_sauvegarde() {
  horodatage=$(date +%Y-%m-%d_%H%M)

  fichier="/sauvegardes/gmao_${horodatage}.dump"
  if pg_dump -h db -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" -f "$fichier.tmp"; then
    mv "$fichier.tmp" "$fichier"
    echo "[$(date '+%d/%m/%Y %H:%M')] Sauvegarde créée : $fichier"
  else
    rm -f "$fichier.tmp"
    echo "[$(date '+%d/%m/%Y %H:%M')] ÉCHEC de la sauvegarde de la base"
  fi

  if [ -n "$(ls -A /medias 2>/dev/null)" ]; then
    archive="/sauvegardes/medias_${horodatage}.tar.gz"
    if tar -czf "$archive.tmp" -C /medias .; then
      mv "$archive.tmp" "$archive"
      echo "[$(date '+%d/%m/%Y %H:%M')] Fichiers archivés : $archive"
    else
      rm -f "$archive.tmp"
      echo "[$(date '+%d/%m/%Y %H:%M')] ÉCHEC de l'archivage des fichiers"
    fi
  fi

  find /sauvegardes \( -name 'gmao_*.dump' -o -name 'medias_*.tar.gz' \) -mtime +"$JOURS" -delete
}

faire_sauvegarde
while true; do
  maintenant=$(date +%s)
  prochaine=$(date -d "today $HEURE" +%s)
  if [ "$prochaine" -le "$maintenant" ]; then
    prochaine=$(date -d "tomorrow $HEURE" +%s)
  fi
  sleep $((prochaine - maintenant))
  faire_sauvegarde
done