# Demo 2 vues - keypoints inter-vues

Demonstration de la capacite **inter-connexion inter-vues** pour DEUX vues.
Relie des points appartes entre les deux vues (ronds + trait, code couleur par
piste). Aucune logique metier dans l'app : tout passe par le hook plugin
`get_multiview_overlays(api, views)`.

## Modes couverts
- **Cote a cote (h2)** et **Temporel (i-N | i)** : vue A = gauche, vue B = droite.
- **Empile (v2)** : vue A = haut, vue B = bas.

## Utilisation
1. Ouvrir une source, choisir le mode (Temporel, cote a cote, ou empile).
2. Activer ce plugin dans le dialogue Plugins.
3. Deplier la carte et glisser le CSV dans l'entree `pairs_csv`.
4. Ouvrir le panneau du plugin pour regler le rendu.
5. L'inter-connexion est **automatique** (aucun bouton) des qu'un plugin la
   fournit et qu'au moins deux vues sont affichees.

Le fichier `data/multiview_keypoints.csv` est produit hors de l'application par
un pipeline de matching puis filtre par RANSAC. Il est fourni comme exemple,
mais doit etre glisse explicitement dans `pairs_csv` avant tout affichage. Le
plugin ne genere aucune donnee synthetique et ne charge aucun fichier par
defaut.

## CSV (adaptable sans le modifier)
Colonnes par defaut (gauche/droite) :
```
frame_left,frame_right,xL,yL,xR,yR,id,confidence
```
Empile (haut/bas) : `frame_top,frame_bottom,xT,yT,xB,yB,id,confidence`.

Si vos noms de colonnes different, **editez le dictionnaire `COLS` en haut de
`plugin.py`** (roles -> vos colonnes, par mode) : rien d'autre a toucher, pas
besoin de reformater le CSV. `id` est optionnel (couleur par piste) et
`confidence` pilote le filtrage et la taille des points.

Le panneau distingue les 600 lignes fixes du CSV du nombre de correspondances
visibles sur la paire de frames courante. Il permet de regler le seuil minimal,
la taille de base des points (11 px par defaut), l'epaisseur des liens et le
dimensionnement proportionnel au score. La mediane de l'etendue choisie garde
exactement la taille de base de 11 px.

La taille et la colormap `Coolwarm_r` utilisent la meme normalisation :

- `Adaptatif a la frame` recalcule le minimum et le maximum sur la paire
  courante ;
- `Absolu sur tout le CSV` conserve une echelle fixe pendant la navigation.

Un score faible est rouge et un score fort est bleu. Le CSV de demonstration
couvre les frames 0 a 9.
