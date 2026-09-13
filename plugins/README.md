# Plugins FrameViewer

Ce dossier vit **a cote de `FrameViewer.exe`** (ou a la racine du depot en
mode source). Les plugins qu'il contient sont **decouverts mais decoches
par defaut** : ils n'affectent rien tant qu'on ne les active pas depuis
**Parametres > Plugins**. Voir `docs/plugins.md` pour l'API complete.

## Deux facons de faire un plugin

1. **En code** -- un `plugin.py` qui expose `PLUGIN = MaClasse` (sous-classe
   de `FrameViewerPlugin`). Controle total (hooks `get_overlays`,
   `render_patch`, `build_panel`, `accepts_drop`/`on_drop`...).
2. **En blocs (blueprint)** -- l'**editeur de graphe** (Parametres >
   Plugins > *Editeur de graphe (blocs)...* ou *Nouveau plugin graphe...*).
   Un mini-langage visuel : on pose des blocs (colonne CSV, calcul,
   condition, couleur, forme, sortie), on relie une sortie a une entree, on
   enregistre. Cela produit un `graph.json` (la logique) + un `plugin.py`
   wrapper genere : un plugin graphe reste un plugin normal pour le loader.
   L'editeur est **non bloquant** : on peut naviguer dans la sequence et
   cliquer *Tester (frame courante)* pour voir le JSON produit en direct.

Structure d'un plugin :

```
plugins/
  plugins_mon_plugin/
    plugin.py          # code, ou wrapper genere par l'editeur de graphe
    graph.json         # (plugins blocs uniquement) la logique du graphe
    donnees.csv        # fichiers annexes libres (CSV, config...)
```

## Plugins de test fournis

Tous decoches au demarrage -- cocher dans Parametres > Plugins pour activer,
puis **glisser-deposer le CSV du dossier sur la vue** pour alimenter le
plugin.

Version **code** :

- `plugins_code_bbox/` -- LE plus complet : glisse-depose n'importe quel CSV
  de detections (colonnes libres, associees a la volee via un dialogue),
  dessine les boites avec couleur/score/ellipse de covariance optionnels.
  CSV d'essai : `sample_detections.csv`.
- `plugins_code_patch/` -- incruste une mini-vignette (aiguille) dans un coin
  de la vue (hook `render_patch`).
- `plugins_code_points/` -- lit un CSV fixe et dessine un point par frame
  (hook `get_overlays`). CSV : `points.csv`.

Version **blueprint (graphe de blocs)** -- meme genre de rendu, mais construit
visuellement, editable dans l'editeur de graphe :

- `plugins_graph_bbox/` -- boites depuis un CSV `frame,x1,y1,x2,y2,rgb`
  (colonne CSV -> rectangle, couleur analysee depuis `rgb`). CSV :
  `detections.csv`.
- `plugins_graph_keypoints/` -- correspondances de keypoints : chaque ligne
  `frame,x1,y1,x2,y2` devient un **segment** reliant les deux points, avec une
  croix a chaque extremite (visuel type appariement SIFT dans une meme vue).
  CSV : `keypoints.csv`.

## Palette de blocs (editeur de graphe)

- **Source** : Colonne CSV, Constante, Numero de frame.
- **Calcul** : Milieu, Operation (a op b : + - * /).
- **Logique** : Comparer (>, <, >=, <=, ==).
- **Apparence** : Couleur (R,G,B), Couleur fixe, Analyser couleur ("R,G,B").
- **Forme** : Rectangle, Ellipse, Point/Croix, Segment (2 points), Texte.
- **Sortie** : Calques (collecte les formes de la frame).

## Depuis l'appli

Parametres > **Plugins** : cocher/decocher pour activer, *Recharger* apres
edition d'un fichier, *Editeur de graphe (blocs)* / *Editeur de code* /
*Nouveau plugin graphe* pour creer ou modifier. L'etat coche est memorise
entre deux lancements.
