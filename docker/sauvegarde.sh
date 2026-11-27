#!/bin/bash
# Sauvegarde PostgreSQL : une au démarrage, puis une par jour à l'heure choisie.
export PGPASSWORD="$POSTGRES_PASSWORD"
JOURS="${SAUVEGARDE_JOURS:-14}"
HEURE="${SAUVEGARDE_HEURE:-02:00}"

faire_sauvegarde() {
  fichier="/sauvegardes/gmao_$(date +%Y-%m-%d_%H%M).dump"
  if pg_dump -h db -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" -f "$fichier.tmp"; then
    mv "$fichier.tmp" "$fichier"
    echo "[$(date '+%d/%m/%Y %H:%M')] Sauvegarde créée : $fichier"
    find /sauvegardes -name 'gmao_*.dump' -mtime +"$JOURS" -delete
  else
    rm -f "$fichier.tmp"
    echo "[$(date '+%d/%m/%Y %H:%M')] ÉCHEC de la sauvegarde"
  fi
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