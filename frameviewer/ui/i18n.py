"""Runtime translation support for the hand-built Qt interface.

FrameViewer predates a translation catalog and creates most widgets directly
in Python.  This module keeps the original French labels as the source of
truth, applies an English catalog at runtime, and restores the exact source
text when the user switches back to French.  Plugin-owned widgets are skipped:
plugins remain responsible for translating their own interface.
"""
from __future__ import annotations

import re
from functools import lru_cache

from PySide6 import QtCore, QtGui, QtWidgets


LANGUAGE_SETTING = "interface/language"
SUPPORTED_LANGUAGES = {"fr", "en"}


# Exact labels are preferred. Phrase replacements cover status messages and
# explanatory text assembled dynamically by the existing interface.
EXACT_ENGLISH = {
    "Tutoriel": "Tutorial",
    "Quitter": "Exit",
    "Fermer": "Close",
    "Annuler": "Cancel",
    "Appliquer": "Apply",
    "Réinitialiser": "Reset",
    "Reinitialiser": "Reset",
    "Aide": "Help",
    "Outils TI": "Image tools",
    "Historique": "History",
    "Paramètres": "Settings",
    "Parametres": "Settings",
    "Plugins": "Plugins",
    "Conversions": "Conversions",
    "Hist / Calque": "Histogram / Layers",
    "Calque / Hist / Conversion / Param": "Layers / Histogram / Convert / Settings",
    "☰  Calque / Hist / Conversion / Param": "☰  Layers / Histogram / Convert / Settings",
    "Vue unique": "Single view",
    "Deux vues côte à côte": "Two side-by-side views",
    "Deux vues empilées": "Two stacked views",
    "Trois vues en colonnes": "Three column views",
    "Quatre vues en grille": "Four views in a grid",
    "Superposition alpha de 2 sources": "Alpha blend of two sources",
    "Disposition des vues (split / fusion)": "View layout (split / blend)",
    "Lier": "Link",
    "Liaison active": "Link enabled",
    "Ouvrir...": "Open...",
    "Ouvrir fichier": "Open file",
    "Ouvrir dossier": "Open folder",
    "Convertir": "Convert",
    "Extraire": "Extract",
    "Extraire...": "Extract...",
    "Enregistrer": "Save",
    "Effacer": "Clear",
    "Supprimer": "Delete",
    "Actif": "Enabled",
    "Inactif": "Disabled",
    "Console": "Console",
    "Config": "Config",
    "Noir": "Black",
    "Blanc": "White",
    "Pleine plage": "Full range",
    "Précédent": "Previous",
    "Precedent": "Previous",
    "Suivant": "Next",
    "Terminer": "Finish",
    "Aucun": "None",
    "Aucune": "None",
    "Oui": "Yes",
    "Non": "No",
    "Toutes": "All",
    "Tous": "All",
    "Image": "Image",
    "Dossier": "Folder",
    "Cadence": "Frame rate",
    "Opacité": "Opacity",
    "Opacite": "Opacity",
    "Épaisseur": "Thickness",
    "Epaisseur": "Thickness",
    "Remplissage": "Fill",
    "Confiance minimale": "Minimum confidence",
    "Taille des points": "Point size",
    "Aperçu": "Preview",
    "Apercu": "Preview",
    "Fichier": "File",
    "Format de sortie": "Output format",
    "Destination": "Destination",
    "Plage de frames": "Frame range",
    "De :": "From:",
    "à :": "to:",
    "Pret.": "Ready.",
    "Pret. Glisse un fichier ou dossier, ou utilise Ouvrir...":
        "Ready. Drag a file or folder here, or use Open...",
    "Action attendue dans l'interface": "Action required in the interface",
    "A faire :": "To do:",
    "Fichiers du tutoriel": "Tutorial files",
    "Glisse les dossiers vers la vue indiquee": "Drag the folders to the indicated view",
    "Trafic RGB": "RGB traffic",
    "Trafic IR": "IR traffic",
    "Annotations YOLO": "YOLO annotations",
    "CSV boites du plugin": "Plugin boxes CSV",
    "CSV correspondances multivues": "Multi-view matches CSV",

    # Main window and navigation.
    "Interface en français": "French interface",
    "↻ Rot.": "↻ Rotate",
    "▦  Vues ▾": "▦  Views ▾",
    "Résolution": "Resolution",
    "Pleine (1:1)": "Full (1:1)",
    "Aucune (couleur)": "None (color)",
    "Niveaux de gris": "Grayscale",
    "Lecture": "Play",
    "Pause": "Pause",
    "Ouvrir fichier...": "Open file...",
    "Ouvrir dossier...": "Open folder...",
    "Ajouter morceau": "Add segment",
    "Ajouter un morceau": "Add a segment",
    "Extraire morceaux...": "Extract segments...",
    "Annuler clic": "Undo click",
    "Effacer clics": "Clear clicks",
    "Vue / source sélectionnée": "Selected view / source",
    "Vue / source selectionnee": "Selected view / source",
    "Outils de capture / rognage": "Capture / crop tools",
    "Extraction IN / OUT": "IN / OUT extraction",
    "Découpe par morceaux": "Segment extraction",
    "aucun morceau": "no segment",
    "Retirer": "Remove",
    "Vider": "Clear",
    "Enregistrement de clics": "Click recording",
    "Réticule": "Crosshair",
    "Afficher le réticule": "Show crosshair",
    "Global — toutes les vues / application": "Global — all views / application",
    "Frames en avance :": "Frames ahead:",
    "Désactivé. Lecture directe depuis la source.": "Disabled. Reading directly from source.",
    "Conversions": "Conversions",
    "Journal des conversions": "Conversion log",
    "Effacer les terminées": "Clear completed",
    "Vue :": "View:",
    "Histogramme (vue sélectionnée)": "Histogram (selected view)",
    "Histogramme (vue selectionnee)": "Histogram (selected view)",
    "Rendu — toutes les vues": "Rendering — all views",
    "Bords": "Edges",
    "Netteté": "Sharpen",
    "Nettete": "Sharpen",
    "Inverser": "Invert",
    "Calque SIDECAR / annotations — vue sélectionnée": "SIDECAR layer / annotations — selected view",
    "Calque SIDECAR / annotations — vue selectionnee": "SIDECAR layer / annotations — selected view",
    "Afficher les overlays": "Show overlays",
    "Decalage:": "Offset:",
    "Aucun SIDECAR charge": "No SIDECAR loaded",
    "Calques :": "Layers:",
    "Tout": "All",
    "Suppr. tout  🗑": "Delete all  🗑",
    "Export custom des calques...": "Custom layer export...",
    "Convertir .ver <-> YOLO...": "Convert .ver <-> YOLO...",
    ".ver -> YOLO  (exporter un dataset)": ".ver -> YOLO  (export a dataset)",
    "YOLO -> .ver  (importer)": "YOLO -> .ver  (import)",
    "Dossier YOLO (un .txt par frame) ou fichier .txt fusionné":
        "YOLO folder (one .txt per frame) or merged .txt file",
    "Largeur": "Width",
    "Hauteur": "Height",
    "Logs (frame courante) :": "Logs (current frame):",
    "ROI — sélection / mesures": "ROI — selection / measurements",
    "ROI — selection / mesures": "ROI — selection / measurements",
    "(pas de selection)": "(no selection)",
    "Selection vide.": "Empty selection.",
    "Histogramme du crop:": "Crop histogram:",
    "FFT 2D (spectre spatial)": "2D FFT (spatial spectrum)",
    "Spectre FFT 2D : tracer une ROI puis cliquer.": "2D FFT spectrum: draw an ROI, then click.",
    "Profil de ligne": "Line profile",
    "Règle — distance / angle": "Ruler — distance / angle",
    "Règle": "Ruler",
    "Clic = ajouter un point\nClic droit = réinitialiser": "Click = add a point\nRight-click = reset",
    "Soustraction temporelle": "Temporal subtraction",
    "Activer  |frame N − frame N−k|": "Enable  |frame N − frame N−k|",
    "Révèle mouvements et variations thermiques.": "Reveals movement and thermal variations.",
    "Sources ouvertes cette session :": "Sources opened this session:",
    " [1] — vide —": " [1] — empty —",
    "⇄ Convertir": "⇄ Convert",
    "Première frame": "First frame",
    "Frame précédente": "Previous frame",
    "Lecture / pause": "Play / pause",
    "Frame suivante": "Next frame",
    "Dernière frame": "Last frame",
    "FPS (toutes les vues) :": "FPS (all views):",

    # Host-side plugin management. Plugin-provided widgets are excluded.
    "Glissez-déposez vos fichiers sur les entrées du plugin.": "Drag and drop files onto the plugin inputs.",
    "Plugin Code": "Code plugin",
    "Plugin Graphe": "Graph plugin",
    "+ Plugin Code": "+ Code plugin",
    "Importer": "Import",
    "Actualiser": "Refresh",
    "Éléments affichés (plugin sélectionné)": "Displayed elements (selected plugin)",
    "Elements affiches (plugin selectionne)": "Displayed elements (selected plugin)",
    "Le plugin sélectionné ne déclare pas d'éléments.": "The selected plugin declares no elements.",
    "Le plugin selectionne ne declare pas d'elements.": "The selected plugin declares no elements.",
    "(aucun aperçu pour cette frame)": "(no preview for this frame)",
    "Éditer le plugin": "Edit plugin",
    "Ouvrir le dossier": "Open folder",
    "Supprimer le plugin": "Delete plugin",
    "Ouvrir le panneau (dock)": "Open panel (dock)",
    "Aucune entrée fichier.": "No file input.",
    "Aucune entree fichier.": "No file input.",
    "+ Entrée": "+ Input",
    "+ Entree": "+ Input",
    "Actions": "Actions",

    # Common dialogs.
    "Format de capture :": "Capture format:",
    "Avec calques (boîtes .ver + overlays SIDECAR)": "With layers (.ver boxes + SIDECAR overlays)",
    "Données brutes 16 bits (LUT/filtres/calques ignorés).": "Raw 16-bit data (LUT/filters/layers ignored).",
    "Image affichée (LUT + filtres). Les calques sont gravés si coché.": "Displayed image (LUT + filters). Layers are burned in when enabled.",
    "MP4 (rendu, contraste/LUT inclus)": "MP4 (rendered, contrast/LUT included)",
    "Dossier PNG (rendu)": "PNG folder (rendered)",
    "MP4 sans re-compression  (ffmpeg, vidéo source uniquement)":
        "MP4 without re-encoding  (ffmpeg, video source only)",
    "PNG 8 bits  (affiché tel quel)": "8-bit PNG  (as displayed)",
    "TIFF 16 bits  (données brutes, sans LUT)": "16-bit TIFF  (raw data, without LUT)",
    "Image X = frame, Y = niveau de gris (0-255), couleur = nombre\nde pixels : histogramme de la ROI empilé sur toute la séquence.":
        "Image X = frame, Y = grayscale level (0-255), color = pixel count;\nROI histograms are stacked across the full sequence.",
    "Grave directement dans chaque frame exportée :\n• le contraste/LUT/filtres courants\n• les boîtes .ver visibles\n• le calque SIDECAR (si coché)\nS'applique aux formats d'images rendues et aux vidéos ré-encodées.":
        "Burn directly into every exported frame:\n• current contrast/LUT/filters\n• visible .ver boxes\n• SIDECAR layer (when enabled)\nApplies to rendered image formats and re-encoded videos.",
    "Lier les vues": "Link views",
    "Gauche": "Left",
    "Droite": "Right",
    "Haut": "Top",
    "Bas": "Bottom",
    "Centre": "Center",
    "Haut gauche": "Top left",
    "Haut droite": "Top right",
    "Bas gauche": "Bottom left",
    "Bas droite": "Bottom right",
    "Aucune source": "No source",
    "Chaque valeur est la frame de depart du slot. Ensuite, la navigation\nconserve exactement ces ecarts entre les vues.": "Each value is the slot's starting frame. Navigation then\npreserves these offsets exactly across views.",
    "Le mode Temporel utilise deja N pour definir l'ecart entre i-N et i.\nLa liaison synchronise ici uniquement le zoom et le deplacement.": "Temporal mode already uses N for the i-N to i offset.\nLinking here synchronizes zoom and pan only.",
    "Format de sortie (identique pour tous les morceaux)": "Output format (same for every segment)",
    "Nom commun :": "Common name:",
    "Extraire tous les morceaux...": "Extract all segments...",
    "Convertir la ROI": "Convert ROI",
    "Padding autour du crop (px) :": "Padding around crop (px):",
    "Exporter aussi la heatmap des histogrammes (PNG, à côté)": "Also export the histogram heatmap (PNG, alongside)",
    "Export custom des calques": "Custom layer export",
    "Plage de frames": "Frame range",
    "Calques .ver (décoche ceux à exclure)": ".ver layers (clear those to exclude)",
    "Répartition des fichiers de sortie": "Output file distribution",
    "Éclater : un fichier .ver PAR TRACK cochée (mono-track chacun)": "Split: one .ver file PER selected TRACK (one track each)",
    "Fusionner : toutes les tracks cochées dans un seul fichier .ver": "Merge: all selected tracks into one .ver file",
    "Coche au moins une track .ver.": "Select at least one .ver track.",
    "Aucune annotation dans la plage/les tracks choisies.": "No annotation in the selected range/tracks.",
    "Import YUV / gris brut": "Import YUV / raw grayscale",
    "Format invalide.": "Invalid format.",
    "ffmpeg échoué — basculement cv2.": "ffmpeg failed — falling back to cv2.",
    "Impossible de traiter la frame source.": "Unable to process the source frame.",
    "Impossible d'ouvrir la source vidéo.": "Unable to open the video source.",
    "Aucun morceau valide à extraire.": "No valid segment to extract.",
    "Image + métadonnées copiées dans le presse-papier (Ctrl+V pour coller)":
        "Image + metadata copied to the clipboard (Ctrl+V to paste)",
    "FFT 2D : charger une source et tracer une ROI d'abord":
        "2D FFT: load a source and draw an ROI first",
    "FFT 2D : ROI trop petite": "2D FFT: ROI is too small",
    "Fusion : bloc haut-gauche = source principale, déposez une 2ᵉ source en haut-droite. α réglable en bas (orange).":
        "Blend: upper-left slot = primary source; drop a second source in the upper-right. Adjust α at the bottom (orange).",
    "Temporel : type de source non clonable.": "Temporal: source type cannot be cloned.",
    "Temporel : ouvrez d'abord une source.": "Temporal: open a source first.",
    "Vues déliées": "Views unlinked",
    "Fusion : charger une source dans les deux vues du haut":
        "Blend: load a source in both upper views",
    "Fusion : séquences vides": "Blend: sequences are empty",
    "Aucune source chargée.": "No source loaded.",
    "Aucune frame composée (vues vides ?).": "No composed frame (empty views?).",
    "Trace un rectangle vert dans la vue : seule cette zone sera exportée.":
        "Draw a green rectangle in the view: only this area will be exported.",
    "Suivi ROI annulé.": "ROI tracking canceled.",
    "Dernier clic annule.": "Last click undone.",
    "Clics effaces.": "Clicks cleared.",
    "Cadence de lecture cible": "Target playback rate",
    "Cadence de lecture cible (toutes les vues)": "Target playback rate (all views)",
    "Où créer ce plugin ?": "Where should this plugin be created?",
    "Trace d'abord une ROI (glisser sur l'image), ou clique\nune boîte .ver pour la suivre, avant de convertir.":
        "Draw an ROI first (drag on the image), or click\na .ver box to track it, before converting.",
    "Définissez d'abord :\n  [  IN  → marque la frame de début\n  OUT ]  → marque la frame de fin\n\nAstuce : activer la boucle (↺) pour prévisualiser la zone.":
        "Define first:\n  [  IN  → sets the start frame\n  OUT ]  → sets the end frame\n\nTip: enable looping (↺) to preview the range.",
    "Définis d'abord IN puis OUT (OUT > IN) avant d'ajouter un morceau.":
        "Set IN, then OUT (OUT > IN) before adding a segment.",
    "Ajoute d'abord au moins un morceau (IN→OUT puis « Ajouter morceau »).":
        "Add at least one segment first (IN→OUT, then “Add segment”).",
    "Impossible d'ecrire l'image.": "Unable to write the image.",
    "Ouvre d'abord la séquence correspondante sur cette vue : les coordonnées YOLO sont normalisées (0..1) et ont besoin des dimensions de l'image pour être converties en pixels.":
        "Open the corresponding sequence on this view first: YOLO coordinates are normalized (0..1) and require the image dimensions for conversion to pixels.",
    "Aucun calque .ver chargé sur la vue active.\n\n(Cet export réorganise des tracks .ver — il ne s'applique pas aux calques issus d'un dossier YOLO, qui n'ont pas de concept de suivi entre frames.)":
        "No .ver layer is loaded on the active view.\n\n(This export reorganizes .ver tracks; it does not apply to layers from a YOLO folder, which have no cross-frame tracking concept.)",
    "Nouveau plugin graphe": "New graph plugin",
    "Nouveau plugin": "New plugin",
    "Nom invalide.": "Invalid name.",
    "Nom invalide (lettres/chiffres/underscore, doit commencer par une lettre).":
        "Invalid name (letters/digits/underscore; must start with a letter).",
    "Le dossier ne contient pas de plugin.py.": "The folder does not contain plugin.py.",
    "Aucune séquence ouverte.": "No sequence open.",
    "Aucun plugin actif : coche « Actif » sur au moins un plugin dans l'onglet Plugins avant d'exporter une frame de debug.":
        "No plugin enabled: enable at least one plugin in the Plugins tab before exporting a debug frame.",
    "Plugin à debugger :": "Plugin to debug:",
    "Ou sans argument (le runner demande le plugin puis la frame), idem que la config VS Code « Debug plugin (frame courante) » (F5) :":
        "Or without arguments (the runner asks for the plugin and frame), equivalent to the VS Code “Debug plugin (current frame)” configuration (F5):",
    "Dossier déposé": "Folder dropped",
    "SIDECAR charge. Depose aussi la sequence correspondante (SPECIALIZED / video / dossier d'images).":
        "Overlay loaded. Also drop the corresponding media sequence.",
    "Aucune source sur la vue principale.": "No source on the primary view.",
    "Effacer tous les clics enregistres ?": "Clear all recorded clicks?",
    "Impossible d'ouvrir le fichier vidéo.": "Unable to open the video file.",
    "Extraire le rendu fusionné (MP4 / PNG / SPECIALIZED) sur la plage IN→OUT\n(ou toute la séquence si IN/OUT non définis)":
        "Extract the blended result over the IN→OUT range\n(or the full sequence when IN/OUT is not defined)",
    "Rechercher un bloc…  (tape, ↑↓, Entrée)": "Search for a block…  (type, ↑↓, Enter)",
    "Ajoute au canvas un petit graphe complet qui dessine un segment par paire de points d'un CSV.":
        "Add a small complete graph to the canvas that draws one segment per CSV point pair.",
    "Test du graphe": "Graph test",
    "Vider ce fichier (sous-entrée)": "Clear this file (sub-input)",
    "Supprimer l'entrée du plugin (manifest + code)": "Delete the plugin input (manifest + code)",
    "Ajouter une entrée (change le format du plugin : nouvelle self.<nom>)":
        "Add an input (changes the plugin format: new self.<name>)",
    "Supprimer l'entrée": "Delete input",
    "Nouvelle entrée": "New input",
    "Glisser-déposer un fichier ici": "Drag and drop a file here",
    "Afficher / masquer ce jeu d'entrées": "Show / hide this input set",
    "Supprimer ce jeu d'entrées complet": "Delete this complete input set",
    "Utiliser le calque YOLO déjà chargé sur cette vue": "Use the YOLO layer already loaded on this view",
    "Dimensions image (W x H) :": "Image dimensions (W x H):",
    "Aucune séquence ouverte sur cette vue : ajuste ces valeurs à la main (dimensions des images sources du dataset YOLO).":
        "No sequence is open on this view: adjust these values manually to match the source image dimensions of the YOLO dataset.",
    "Id de classe YOLO écrit dans les .txt pour les boîtes de ce track (0 par défaut). Le nom correspondant à cet id est la position N dans le champ Classes ci-dessus.":
        "YOLO class ID written to .txt files for this track's boxes (0 by default). The corresponding name is item N in the Classes field above.",
    "Temporel : même flux, frame i-N à gauche et i à droite. Réglez N dans la barre d'outils.":
        "Temporal: same stream, frame i-N on the left and i on the right. Set N in the toolbar.",
    "Vue fusion sélectionnée : trace une ROI dessus (mesures), règle α, ou « ⤓ Extraire » pour récupérer le blend.":
        "Blend view selected: draw an ROI for measurements, adjust α, or use ⤓ Extract to save the blend.",
    "Colonnes manquantes": "Missing columns",
    "* obligatoire": "* required",
    "<b>Aperçu du fichier</b> :": "<b>File preview</b>:",
    "Associer les colonnes du CSV": "Map CSV columns",
    "Indique quelle colonne du fichier correspond à chaque information attendue (les noms de colonnes peuvent varier d'un fichier à l'autre -- ce choix sera mémorisé pour ce fichier).":
        "Specify which file column matches each expected field (column names may vary between files; this choice is remembered for this file).",
    "(aucune)": "(none)",

    # Tutorial shell and all static tutorial steps.
    "Orientation": "Orientation",
    "Les zones de travail": "Workspace areas",
    "Calques, histogramme et parametres": "Layers, histogram and settings",
    "Explorateur": "File explorer",
    "Donnees de demonstration": "Demo data",
    "Chargement": "Loading",
    "Deposer Trafic RGB": "Drop RGB traffic",
    "Formats": "Formats",
    "Sources acceptees": "Accepted sources",
    "Canvas": "Canvas",
    "Zoom, panoramique et ajustement": "Zoom, pan and fit",
    "Rendu": "Rendering",
    "Histogramme et LUT": "Histogram and LUT",
    "Parametres": "Settings",
    "Ouvrir l'onglet Parametres": "Open the Settings tab",
    "REC clics": "Click recording",
    "REC clics  [ON]": "Click recording  [ON]",
    "Activer et enregistrer un point": "Enable and record a point",
    "Arreter l'enregistrement": "Stop recording",
    "Extraction": "Extraction",
    "Marquer le debut IN": "Set the IN point",
    "Naviguer puis marquer OUT": "Navigate, then set the OUT point",
    "Extraire ou ajouter un morceau": "Extract or add a segment",
    "Liste et export des morceaux": "Segment list and export",
    "Annotations": "Annotations",
    "Ouvrir Hist / Calque": "Open Histogram / Layers",
    "Deposer les annotations YOLO": "Drop YOLO annotations",
    "Masquer uniquement le calque YOLO": "Hide only the YOLO layer",
    "Plugin monovue": "Single-view plugin",
    "Ouvrir Plugins": "Open Plugins",
    "Activer le showcase": "Enable the showcase",
    "Deplier la carte": "Expand the card",
    "Deposer le CSV des boites": "Drop the boxes CSV",
    "Ouvrir le panneau du plugin": "Open the plugin panel",
    "Regler puis exporter": "Adjust, then export",
    "Ouvrir les logs plugins": "Open plugin logs",
    "Configuration": "Configuration",
    "Dossiers et plugins personnels": "Folders and custom plugins",
    "Desactiver le showcase": "Disable the showcase",
    "Multivue": "Multi-view",
    "Choisir Empile haut / bas": "Choose top / bottom stack",
    "Deposer Trafic IR": "Drop IR traffic",
    "Liaison": "Linking",
    "Meme base temporelle": "Same time base",
    "Tester un decalage": "Test an offset",
    "Verifier la navigation synchronisee": "Verify synchronized navigation",
    "Revenir a [0, 0]": "Return to [0, 0]",
    "Plugin multivue": "Multi-view plugin",
    "Rouvrir le panneau Plugins": "Reopen the Plugins panel",
    "Activer les correspondances": "Enable matches",
    "Deplier la carte multivue": "Expand the multi-view card",
    "Deposer le CSV des correspondances": "Drop the matches CSV",
    "Ouvrir les reglages multivues": "Open multi-view settings",
    "Naviguer avec les correspondances": "Navigate with matches",
    "Confiance, taille et couleur": "Confidence, size and color",
    "Fin": "Finish",
    "Flux complet maitrise": "Complete workflow mastered",
    "glisser Trafic RGB dans le canvas": "drag RGB traffic onto the canvas",
    "realiser les trois gestes": "perform all three gestures",
    "deplacer une poignee de l'histogramme ou changer la LUT": "move a histogram handle or change the LUT",
    "cliquer sur l'onglet Parametres": "click the Settings tab",
    "activer REC clics puis cliquer dans l'image": "enable click recording, then click the image",
    "decocher REC clics": "clear click recording",
    "choisir une frame puis cliquer IN": "choose a frame, then click IN",
    "deplacer la timeline puis cliquer OUT": "move the timeline, then click OUT",
    "cliquer Ajouter morceau pour cette demonstration": "click Add segment for this demonstration",
    "cliquer sur Hist / Calque": "click Histogram / Layers",
    "glisser Annotations YOLO dans le canvas": "drag YOLO annotations onto the canvas",
    "decocher le calque annotations_yolo": "clear the annotations_yolo layer",
    "laisser YOLO masque puis cliquer sur Plugins": "leave YOLO hidden, then click Plugins",
    "cocher Actif sur Demo showcase": "enable Demo showcase",
    "deplier la carte Demo showcase": "expand the Demo showcase card",
    "deposer le CSV dans l'entree boxes": "drop the CSV onto the boxes input",
    "cliquer Ouvrir le panneau (dock)": "click Open panel (dock)",
    "cliquer sur Console": "click Console",
    "decocher Actif sur Demo showcase": "disable Demo showcase",
    "choisir la disposition Empile haut / bas": "choose the top / bottom stack layout",
    "glisser Trafic IR dans la vue inferieure": "drag IR traffic onto the lower view",
    "appliquer la liaison [0, 0]": "apply the [0, 0] link",
    "appliquer la liaison [2, 0]": "apply the [2, 0] link",
    "cliquer une vue puis deplacer sa mini-timeline": "click a view, then move its mini-timeline",
    "reappliquer [0, 0]": "apply [0, 0] again",
    "ouvrir le panneau Plugins": "open the Plugins panel",
    "cocher Actif sur le plugin multivue": "enable the multi-view plugin",
    "deplier la carte multivue": "expand the multi-view card",
    "deposer le CSV dans pairs_csv": "drop the CSV onto pairs_csv",
    "ouvrir le panneau du plugin multivue": "open the multi-view plugin panel",
    "cliquer une vue puis changer de frame": "click a view, then change frame",
    "Zoomer avec la molette": "Zoom with the mouse wheel",
    "Deplacer avec le bouton central maintenu": "Pan while holding the middle button",
    "Recadrer avec un clic central": "Fit with a middle-click",
    "IN defini": "IN set",
    "Timeline placee apres IN": "Timeline positioned after IN",
    "OUT defini apres IN": "OUT set after IN",
    "Cliquer dans une vue": "Click a view",
    "Changer de frame": "Change frame",

    # Tutorial body copy. These strings are cached and translated only when a
    # tutorial step changes or the user changes language.
    "La barre regroupe les commandes, le canvas affiche la source, les mini-barres naviguent par vue et les panneaux restent ancrables.":
        "The toolbar groups commands, the canvas displays the source, each mini-bar navigates its view, and panels remain dockable.",
    "Le panneau droit contient Hist / Calque, Plugins et Parametres. Chaque onglet agit sur la vue selectionnee ou sur toutes les vues lorsque cela est precise.":
        "The right panel contains Histogram / Layers, Plugins, and Settings. Each tab applies to the selected view or to all views when stated.",
    "Les donnees sont des copies ecrites dans le workspace utilisateur. Seul Trafic RGB est deposable maintenant; IR et les CSV seront deverrouilles au moment utile.":
        "The demo data is copied into the user workspace. Only RGB traffic can be dropped now; IR and CSV files unlock when needed.",
    "Glisse Trafic RGB depuis le pseudo-explorateur vers la vue unique. La fenetre de fichiers se fermera des que la source sera chargee.":
        "Drag RGB traffic from the simulated explorer onto the single view. The file window closes as soon as the source is loaded.",
    "Les dossiers d'images, images isolees, MP4, WebM, TIFF, JPEG, PNG et YUV sont natifs. L'Aide enumere les extensions optionnelles vraiment disponibles.":
        "Image folders, individual images, MP4, WebM, TIFF, JPEG, PNG, and YUV are supported natively. Help lists the optional extensions actually available.",
    "Realise les trois gestes dans l'ordre de ton choix. Chaque case se coche des que le geste est detecte.":
        "Perform the three gestures in any order. Each item is checked as soon as the gesture is detected.",
    "Toute la zone est mise en avant : l'histogramme mesure la vue active et le bloc Rendu applique LUT, contraste et filtres. Deplace simplement la barre gauche ou droite de l'histogramme; choisir une LUT predefinie fonctionne aussi.":
        "The whole area is highlighted: the histogram measures the active view and Rendering applies LUT, contrast, and filters. Move either histogram handle; choosing a preset LUT also works.",
    "Clique sur Parametres. Les commandes REC clics et Extraction IN / OUT se trouvent dans cet onglet.":
        "Click Settings. Click recording and IN / OUT extraction controls are in this tab.",
    "Le point est enregistre dans ": "The point is recorded in ",
    ". Desactive maintenant REC clics pour retrouver un clic normal sur le canvas.":
        ". Disable click recording now to restore normal canvas clicks.",
    "Place la timeline sur la frame de debut souhaitee, puis clique IN.":
        "Move the timeline to the required start frame, then click IN.",
    "La zone IN / OUT reste eclairee. Deplace d'abord la timeline sous les images, puis clique OUT ici. OUT doit etre strictement apres IN.":
        "The IN / OUT area remains highlighted. First move the timeline below the images, then click OUT here. OUT must be strictly after IN.",
    "Extraire ouvre directement l'export de la plage IN/OUT. Pour construire plusieurs plages, clique Ajouter morceau; definis ensuite un nouveau couple IN/OUT.":
        "Extract opens export for the IN/OUT range directly. To build several ranges, click Add segment, then define another IN/OUT pair.",
    "La liste montre toutes les plages ajoutees. Extraire morceaux ouvre un export unique dans le format choisi et traite toutes les plages en une fois.":
        "The list shows every added range. Extract segments opens one export setup in the chosen format and processes all ranges at once.",
    "Les fichiers YOLO .txt sont des annotations natives, pas des donnees plugin. Ouvre donc Hist / Calque avant leur chargement.":
        "YOLO .txt files are native annotations, not plugin data. Open Histogram / Layers before loading them.",
    "Glisse Annotations YOLO sur le canvas. Chaque .txt porte le meme nom que son image et classes.txt nomme la classe vehicle.":
        "Drag YOLO annotations onto the canvas. Each .txt has the same name as its image and classes.txt names class 0 vehicle.",
    "Dans Hist / Calque, decoche annotations_yolo (ou son enfant vehicle). Le bouton global Afficher les overlays n'est pas utilise : les overlays des plugins restent independants.":
        "In Histogram / Layers, clear annotations_yolo (or its vehicle child). The global Show overlays switch is not used: plugin overlays remain independent.",
    "Laisse le calque YOLO masque pour eviter toute superposition. Ouvre Plugins; les deux demos doivent etre Inactif et sans fichier restaure.":
        "Leave the YOLO layer hidden to avoid overlap. Open Plugins; both demos must be disabled with no restored file.",
    "Clique Actif sur Demo showcase. Sans fichier dans l'entree boxes, son overlay reste vide.":
        "Enable Demo showcase. Without a file in the boxes input, its overlay remains empty.",
    "Clique sur la fleche de la carte pour afficher son entree boxes, son apercu et ses actions.":
        "Click the card arrow to display its boxes input, preview, and actions.",
    "Glisse CSV boites du plugin dans l'entree boxes. Ce fichier est fourni dans le pseudo-explorateur, mais aucune entree n'est remplie automatiquement.":
        "Drag the plugin boxes CSV onto the boxes input. The file is provided in the simulated explorer, but no input is filled automatically.",
    "Clique Ouvrir le panneau (dock). Il contient opacite globale, remplissage, epaisseur des boites et epaisseur de grille.":
        "Click Open panel (dock). It contains global opacity, fill opacity, box thickness, and grid thickness.",
    "Teste les quatre reglages du dock. La capture et les exports peuvent graver les overlays visibles dans l'image produite.":
        "Try the four dock settings. Capture and export can burn visible overlays into the produced image.",
    "Le bouton Console ouvre les logs, prints, erreurs, diagnostics et exports de debug, isoles par plugin. Clique-le maintenant.":
        "Console opens logs, print output, errors, diagnostics, and debug exports isolated by plugin. Click it now.",
    "Config donne acces a settings.ini, au dossier de plugins utilisateur et a la documentation. Un dossier plugins voisin de l'executable est egalement detecte.":
        "Config provides access to settings.ini, the user plugin folder, and documentation. A plugins folder beside the executable is also detected.",
    "Avant la multivue, decoche Actif sur Demo showcase. Aucun overlay du plugin monovue ne doit rester sur les vues suivantes.":
        "Before multi-view, disable Demo showcase. No single-view plugin overlay should remain on the following views.",
    "La console est fermee. Utilise le bouton de disposition des vues et choisis Empile haut / bas.":
        "The console is closed. Use the view layout button and choose top / bottom stack.",
    "Trafic IR est maintenant deverrouille. Glisse-le dans la vue inferieure; le plugin showcase etant inactif, il ne dessine rien.":
        "IR traffic is now unlocked. Drag it onto the lower view; because the showcase plugin is disabled, it draws nothing.",
    "Clique Lier, conserve Liaison active et les departs [0, 0], puis clique Appliquer. Les compteurs +/- et la saisie clavier sont actifs.":
        "Click Link, keep Link enabled and starts [0, 0], then click Apply. The +/- steppers and keyboard input are active.",
    "Rouvre Lier : Liaison active et les anciennes valeurs restent memorisees. Utilise les fleches +/- ou saisis [2, 0], puis Appliquer. La vue haute commence a 2, celle du bas a 0.":
        "Open Link again: Link enabled and the previous values remain stored. Use the +/- arrows or enter [2, 0], then Apply. The upper view starts at 2 and the lower view at 0.",
    "Clique d'abord dans une des deux vues, puis deplace sa mini-timeline. Les deux vues avancent ensemble en conservant exactement deux frames d'ecart.":
        "First click either view, then move its mini-timeline. Both views advance together while preserving exactly two frames of offset.",
    "Remets les departs a [0, 0] avant les correspondances. Le CSV contient des appariements pour chacune des frames 0 a 9 avec les deux vues au meme index.":
        "Reset starts to [0, 0] before matching. The CSV contains matches for every frame from 0 to 9 with both views at the same index.",
    "Ouvre le panneau droit puis l'onglet Plugins. Le plugin multivue doit encore etre Inactif.":
        "Open the right panel, then the Plugins tab. The multi-view plugin must still be disabled.",
    "Clique Actif sur Demo 2 vues. Sans CSV depose, aucun point ni lien n'est dessine.":
        "Enable Demo 2 views. Without a dropped CSV, no point or link is drawn.",
    "Deplie la carte pour afficher l'entree pairs_csv et les actions.":
        "Expand the card to display the pairs_csv input and actions.",
    "Glisse CSV correspondances multivues dans pairs_csv. Les appariements pre-calcules couvrent les dix couples RGB/IR.":
        "Drag the multi-view matches CSV onto pairs_csv. The precomputed matches cover all ten RGB/IR pairs.",
    "Clique Ouvrir le panneau (dock). Il distingue les 600 lignes fixes du CSV du nombre de correspondances visibles sur la frame courante.":
        "Click Open panel (dock). It distinguishes the CSV's 600 fixed rows from the number of matches visible on the current frame.",
    "Clique dans une vue puis deplace sa mini-timeline. Le compteur affiche sur la vue haute et dans le dock s'adapte a chaque paire de frames.":
        "Click a view, then move its mini-timeline. The counter shown on the upper view and in the dock adapts to each frame pair.",
    "La taille de base vaut 11 px. Taille proportionnelle et Coolwarm_r utilisent la meme normalisation : adaptative a la frame, ou absolue sur tout le CSV. Un score faible est rouge, un score fort bleu.":
        "The base size is 11 px. Proportional size and Coolwarm_r use the same normalization: adaptive per frame or absolute over the full CSV. A low score is red and a high score blue.",
    "Tu as charge deux vues, enregistre des clics, annote, decoupe, lie des offsets et alimente deux plugins par drag-and-drop. Convertir exporte la composition; l'Aide reste la reference detaillee.":
        "You loaded two views, recorded clicks, added annotations, extracted segments, linked offsets, and fed two plugins by drag and drop. Convert exports the composition; Help remains the detailed reference.",

    # Tooltips and descriptive labels on the initial application surface.
    "Ouvre/ferme le panneau : calques SIDECAR/.ver + histogramme,\njournal des conversions, paramètres (3 onglets)":
        "Open/close the panel: SIDECAR/.ver layers + histogram,\nconversion log, settings (3 tabs)",
    "Rotation 90° horaire  (Ctrl+R)": "Rotate 90° clockwise (Ctrl+R)",
    "Écart temporel N : la vue gauche affiche la frame i-N":
        "Temporal offset N: the left view displays frame i-N",
    "Résolution de lecture (toutes les vues)\nPleine = qualité max ; ½/¼/⅛ = plus rapide (réseau/SSH lent)":
        "Playback resolution (all views)\nFull = maximum quality; ½/¼/⅛ = faster (slow network/SSH)",
    "Lier les vues : zoom, déplacement et frame synchronisés entre\ntoutes les vues (comparaison côte à côte)":
        "Link views: synchronize zoom, pan, and frame across\nall views (side-by-side comparison)",
    "Ouvrir le dossier de configuration utilisateur\n(settings.ini + plugins perso, dans AppData\\FrameViewer)":
        "Open the user configuration folder\n(settings.ini + custom plugins, in AppData\\FrameViewer)",
    "Vue plein cadre / normale  (F11 / Échap)": "Full view / normal view (F11 / Esc)",
    "Capturer la frame courante (Ctrl+E)": "Capture the current frame (Ctrl+E)",
    "Activer / desactiver le son (MP4 uniquement)": "Enable / disable audio (MP4 only)",
    "Appareil photo : le format et les options s'ouvrent dans une fenêtre, puis choix du dossier d'enregistrement.":
        "Camera: format and options open in a dialog, followed by the output folder selection.",
    "Rogner l'export sur une sous-zone.\nActiver puis tracer un rectangle vert dans la vue :\nseule cette zone est exportée (clip IN→OUT et morceaux).":
        "Crop export to a sub-area.\nEnable it, then draw a green rectangle in the view:\nonly this area is exported (IN→OUT clip and segments).",
    "Marquer le début du clip à la frame courante": "Set clip start to the current frame",
    "Marquer la fin du clip à la frame courante": "Set clip end to the current frame",
    "Zone sélectionnée pour l'extraction": "Range selected for extraction",
    "Extraire le clip sélectionné (IN → OUT)": "Extract the selected clip (IN → OUT)",
    "Ajoute la plage IN→OUT courante à la liste des morceaux,\npuis réinitialise IN/OUT pour définir le morceau suivant.":
        "Add the current IN→OUT range to the segment list,\nthen reset IN/OUT to define the next segment.",
    "Morceaux définis (surlignés en vert sur la timeline).":
        "Defined segments (highlighted in green on the timeline).",
    "Extrait tous les morceaux en une fois (nom commun {nom}_{IN}_{OUT}).":
        "Extract all segments at once (common name {name}_{IN}_{OUT}).",
    "Trace une croix au centre de l'image.\nUtile pour l'alignement optique.":
        "Draw a cross at the image center.\nUseful for optical alignment.",
    "0 = désactivé.\n4–8 : réseau rapide / LAN.\n12–32 : SSH lent (plus de RAM utilisée).":
        "0 = disabled.\n4–8: fast network / LAN.\n12–32: slow SSH (uses more RAM).",
    "Recharge tous les plugins depuis le disque (recompile le code, sans cache).":
        "Reload all plugins from disk (recompiles code without cache).",
    "Journal (logs, prints, erreurs) + test sur la frame courante + diagnostic env + export debug VS Code.":
        "Log (messages, print output, errors) + current-frame test + environment diagnostics + VS Code debug export.",
    "Aide : tous les hooks et objets api, par theme (le requis en vert), avec exemples.":
        "Help: all hooks and API objects by topic (required items in green), with examples.",
    "Diminuer le point noir": "Lower black point",
    "Augmenter le point noir": "Raise black point",
    "Diminuer le point blanc": "Lower white point",
    "Augmenter le point blanc": "Raise white point",
    "Echelle log": "Log scale",
    "Cocher (afficher) tous les calques": "Enable (show) all layers",
    "Décocher (masquer) tous les calques": "Disable (hide) all layers",
    "Numero de la frame + libelle(s) des overlays affiches sur l'image":
        "Frame number + label(s) of overlays displayed on the image",
    "Active l'outil 'Selection / ROI' puis\ntrace un rectangle sur l'image.":
        "Enable the 'Selection / ROI' tool, then\ndraw a rectangle on the image.",
    "Transformée de Fourier 2D du crop ROI courant.\nAffiche le spectre de magnitude (log) — analyse de texture / périodicité.":
        "2D Fourier transform of the current ROI crop.\nDisplays the log magnitude spectrum for texture / periodicity analysis.",
    "Double-clic : rouvrir la source\nGlisser vers un bloc du split : l'y charger\nSélection (Maj/Ctrl) + Suppr : retirer de l'historique":
        "Double-click: reopen the source\nDrag onto a split slot: load it there\nSelect (Shift/Ctrl) + Delete: remove from history",
    "Clic : sélectionner cette vue (outils TI + histogramme)\nDouble-clic : plein écran / retour\nClic droit glissé : échanger avec une autre vue":
        "Click: select this view (image tools + histogram)\nDouble-click: full screen / return\nRight-drag: swap with another view",
    "Convertir cette vue vers un autre format\n(avec calques + contraste gravés si coché)":
        "Convert this view to another format\n(with layers + contrast burned in when enabled)",
    "Tape un numero de frame puis Entree": "Type a frame number, then press Enter",
    "FPS reel mesure en cours de lecture": "Measured real FPS during playback",
    "Convertir la sequence courante vers un autre format":
        "Convert the current sequence to another format",
    "Lecture en boucle": "Loop playback",
    "Historique des sources": "Source history",
    "Supprime TOUS les calques listés, immédiatement (pas besoin de\nles sélectionner). Pour retirer un seul calque/.ver : sélectionne-le\ndans la liste puis touche Suppr (multi-sélection possible).":
        "Remove ALL listed layers immediately (no need to\nselect them). To remove one layer/.ver, select it\nin the list, then press Delete (multi-selection supported).",
    "Calques de la vue sélectionnée.\n• Calque SIDECAR : une seule ligne.\n• .ver : groupé par fichier ; la case du groupe coche/décoche tous\n  ses tracks d'un coup — déplie (▸) pour choisir track par track.\nSélection + touche Suppr = retirer ce(s) calque(s) (un .ver entier\nsi le groupe est sélectionné, ou juste le track choisi).":
        "Layers of the selected view.\n• SIDECAR layer: one row.\n• .ver: grouped by file; the group checkbox toggles all\n  tracks at once — expand (▸) to choose individual tracks.\nSelect + Delete removes the selected layer(s), either an entire .ver\nwhen its group is selected or only the selected track.",
    "Choisir une plage de frames puis réorganiser l'export des tracks\n.ver : éclater un .ver multi-tracks en plusieurs fichiers mono-track,\nou fusionner plusieurs .ver en un seul fichier multi-tracks.":
        "Choose a frame range, then reorganize exported .ver tracks:\nsplit a multi-track .ver into several single-track files,\nor merge several .ver files into one multi-track file.",
    "Exporter les calques .ver de cette vue en dataset YOLO (labels +\nimages, split train/val/test), ou importer un dossier YOLO\n(un .txt par frame) en .ver.":
        "Export this view's .ver layers as a YOLO dataset (labels +\nimages, train/val/test split), or import a YOLO folder\n(one .txt per frame) as .ver.",

    # Help page. Keys and descriptions are translated before the HTML is
    # assembled, so opening Help in English never exposes French copy.
    "FrameViewer — Aide complète": "FrameViewer — Complete help",
    "Souris, clavier, outils, calques, multivue et export — tout ce que fait l'application.":
        "Mouse, keyboard, tools, layers, multi-view, and export — the complete application reference.",
    "Souris — vue / canvas": "Mouse — view / canvas",
    "Clic gauche (vue inactive)": "Left-click (inactive view)",
    "Active cette vue (outils TI + histogramme s'y rattachent).":
        "Activate this view (image tools and histogram attach to it).",
    "Clic gauche + glisser (outil ROI)": "Left-click + drag (ROI tool)",
    "Trace un rectangle de sélection (ROI).": "Draw a selection rectangle (ROI).",
    "Clic gauche simple (outil ROI)": "Left-click (ROI tool)",
    "Sur une boîte .ver : démarre le <b>suivi</b> automatique de cette boîte. Sur une autre boîte : change de suivi. Dans le vide : annule le suivi.":
        "On a .ver box: start automatic <b>tracking</b> of that box. On another box: switch tracking. On empty space: cancel tracking.",
    "Clic gauche simple (REC clics actif)": "Left-click (click recording enabled)",
    "Enregistre un clic (frame, x, y) dans le fichier de clics.":
        "Record a click (frame, x, y) in the click file.",
    "Clic gauche + glisser (outil ligne)": "Left-click + drag (line tool)",
    "Trace un profil de ligne.": "Draw a line profile.",
    "Clic gauche (outil règle)": "Left-click (ruler tool)",
    "Ajoute un point à la polyligne de mesure.": "Add a point to the measurement polyline.",
    "Clic droit (outil règle)": "Right-click (ruler tool)",
    "Réinitialise la polyligne de mesure.": "Reset the measurement polyline.",
    "Clic droit + glisser (bandeau [n])": "Right-click + drag ([n] header)",
    "Échange le contenu de deux vues.": "Swap the contents of two views.",
    "Double-clic (vue)": "Double-click (view)",
    "Plein écran de cette vue / retour à la disposition normale.":
        "Full-screen this view / return to the normal layout.",
    "Molette": "Mouse wheel",
    "Zoom avant/arrière, centré sur le curseur.": "Zoom in/out around the cursor.",
    "Clic molette + glisser": "Middle-click + drag",
    "Panoramique dans une vue zoomée.": "Pan a zoomed view.",
    "Clic molette (sans glisser)": "Middle-click (without dragging)",
    "Réajuste la vue (fit, zoom 1×).": "Fit the view (1× zoom).",
    "Glisser un fichier / dossier sur une vue": "Drag a file / folder onto a view",
    "Ouvre cette séquence dans cette vue (un dossier de liens symboliques vers des images est accepté, même sans extension).":
        "Open that sequence in the view (a folder of symbolic links to images is accepted, even without extensions).",
    "Glisser un .sidecar / .ver sur une vue": "Drag an .sidecar / .ver file onto a view",
    "Ajoute ce calque à cette vue (l'active si besoin).": "Add that layer to the view (activating it if needed).",
    "Glisser le curseur de frame d'une vue inactive": "Drag the frame slider of an inactive view",
    "Cette vue devient active dès le relâchement.": "That view becomes active on release.",
    "Outils TI (panneau accordéon)": "Image tools (accordion panel)",
    "Spectre de Fourier 2D (magnitude, log) de la ROI courante — texture/périodicité.":
        "2D Fourier spectrum (log magnitude) of the current ROI — texture/periodicity.",
    "Trace le profil d'intensité le long d'une ligne ; export CSV.":
        "Plot the intensity profile along a line; export to CSV.",
    "Distance et angle entre points cliqués (polyligne).": "Distance and angle between clicked points (polyline).",
    "Affiche |frame N − frame N−k| pour révéler mouvements/variations.":
        "Display |frame N − frame N−k| to reveal movement/variations.",
    "Calques — SIDECAR / .ver": "Layers — SIDECAR / .ver",
    "Case à cocher d'un calque": "Layer checkbox",
    "Affiche / masque ce calque (overlay SIDECAR ou boîtes .ver d'un track).":
        "Show / hide this layer (SIDECAR overlay or .ver boxes for one track).",
    "Tout / Aucun": "All / None",
    "Coche / décoche tous les calques de la vue active d'un coup.":
        "Enable / disable all layers of the active view at once.",
    "Supprime les calques sélectionnés dans la liste (multi-sélection, touche Suppr).":
        "Remove the selected layers from the list (multi-selection, Delete key).",
    "Vider calques": "Clear layers",
    "Supprime TOUS les calques (SIDECAR + .ver) de la vue active, en un clic.":
        "Remove ALL layers (SIDECAR + .ver) from the active view in one click.",
    "« Afficher les overlays »": "“Show overlays”",
    "Interrupteur général : masque tout, même si des calques sont cochés.":
        "Master switch: hide everything even when layers are enabled.",
    "Multivue / Fusion": "Multi-view / Blend",
    "Bandeau [1] [2] …": "[1] [2] … header",
    "Sélectionne/active la vue correspondante.": "Select/activate the corresponding view.",
    "⇄ (haut-droit de chaque vue)": "⇄ (top-right of each view)",
    "Convertit cette vue (menu de formats, calques/contraste inclus).":
        "Convert this view (format menu, including layers/contrast).",
    "🔗 Lier": "🔗 Link",
    "Ouvre le réglage des frames de départ puis synchronise frame, zoom et pan. Les écarts sont conservés au scrub, au pas-à-pas et en lecture. La plage commune empêche une vue d'arriver silencieusement en fin de source.":
        "Open starting-frame settings, then synchronize frame, zoom, and pan. Offsets are preserved during scrubbing, stepping, and playback. The common range prevents a view from silently reaching the end of its source.",
    "Mode Fusion": "Blend mode",
    "Mode Temporel (i-N | i)": "Temporal mode (i-N | i)",
    "Inter-connexion inter-vues": "Cross-view connections",
    "Automatique dès qu'un plugin la fournit (aucun bouton) : ronds + traits colorés reliant des points appariés entre deux vues (ex. keypoints i-N ↔ i, ou matching SIFT seq1 ↔ seq2). Voir les plugins <i>demo_2_multivue_kpts</i> / <i>demo_4_multivue_kpts</i>.":
        "Automatic when supplied by a plugin (no button): circles + colored lines connect matched points between views (for example i-N ↔ i keypoints or SIFT matching between sequences). See <i>demo_2_multivue_kpts</i> / <i>demo_4_multivue_kpts</i>.",
    "Convertir (toutes les vues)": "Convert (all views)",
    "🎬 Convertir (toutes les vues)": "🎬 Convert (all views)",
    "Assemble un MP4 de la composition multi-vues actuelle\n(disposition, calques et contraste inclus).":
        "Assemble an MP4 of the current multi-view composition\n(including layout, layers, and contrast).",
    "En multivue : assemble un MP4 de la disposition actuelle (grille ou fusion), calques et contraste inclus.":
        "In multi-view: assemble an MP4 of the current layout (grid or blend), including layers and contrast.",
    "Formats média détectés": "Detected media formats",
    "Disponibles dans cette installation": "Available in this installation",
    "Dossier d'images": "Image folder",
    "Les fichiers sont triés dans l'ordre naturel, sans parcours récursif.":
        "Files are sorted in natural order, without recursive traversal.",
    "Formats optionnels": "Optional formats",
    "; formats optionnels actifs : ": "; active optional formats: ",
    "Ils n'apparaissent ici que si leur adaptateur backend est réellement chargé.":
        "They appear here only when their backend adapter is actually loaded.",
    "Annotations YOLO / .ver": "YOLO / .ver annotations",
    "Association prioritaire": "Primary association",
    "Repli": "Fallback",
    "Si aucun nom ne correspond, association par ordre naturel. Les labels sans image correspondante sont signalés dans la barre de statut.":
        "When no names match, associate by natural order. Labels without a matching image are reported in the status bar.",
    "Optionnel, adjacent au dossier de labels, une classe par ligne. Dans la démonstration, la classe 0 se nomme <b>vehicle</b>.":
        "Optional, beside the labels folder, one class per line. In the demo, class 0 is named <b>vehicle</b>.",
    "Dossier YOLO": "YOLO folder",
    "YOLO fusionné, frames 0-based": "Merged YOLO, 0-based frames",
    ".ver court, frames 1-based": "Short .ver, 1-based frames",
    ".ver long": "Long .ver",
    "Export / Conversion": "Export / Conversion",
    "Zoom du crop + histogramme + statistiques (min/max/moyenne/σ/médiane). Bouton <b>Convertir…</b> : exporte le crop fixe ou suivi sur toute la séquence dans les formats actifs, avec padding réglable et heatmap d'histogrammes en option.":
        "Crop zoom + histogram + statistics (min/max/mean/σ/median). <b>Convert…</b> exports the fixed or tracked crop across the full sequence using active formats, with adjustable padding and an optional histogram heatmap.",
    "Glisser une 2ᵉ source en haut-droite ; régler α ou le mode de mélange (alpha, addition pondérée, différence, damier) ; ⤓ Extraire récupère le rendu fusionné.":
        "Drop a second source in the upper-right; adjust α or the blend mode (alpha, weighted addition, difference, checkerboard); ⤓ Extract saves the blended result.",
    "Même flux affiché à deux instants : la frame <b>i-N</b> à gauche (vue figée « Past Frame », bandeau vert clair) et la frame courante <b>i</b> à droite (vue moteur, bord vert). Réglez <b>N</b> dans la barre d'outils. Idéal pour comparer une frame à son passé (mouvement, dérive).":
        "The same stream at two times: frame <b>i-N</b> on the left (frozen “Past Frame” view, light-green header) and current frame <b>i</b> on the right (engine view, green border). Set <b>N</b> in the toolbar. This is useful for comparing a frame with its past state (movement, drift).",
    "Même nom de base : <b>images/frame_05000.png</b> ↔ <b>labels/frame_05000.txt</b>, sans tenir compte de la casse.":
        "Same base name: <b>images/frame_05000.png</b> ↔ <b>labels/frame_05000.txt</b>, case-insensitive.",
    "Formats vidéo, dossiers d'images et formats optionnels actifs. Case « tel qu'affiché » : grave calques + contraste dans l'export.":
        "Video formats, image folders, and active optional formats. “As displayed” burns layers + contrast into the export.",
    "Convertir (bouton par vue / barre du bas)": "Convert (per-view button / bottom bar)",
    "Même export, sur toute la séquence de cette source.": "Same export across this source's full sequence.",
    "Capture la frame courante en PNG (avec ou sans calques, au choix).":
        "Capture the current frame as PNG, with or without layers.",
    "Échap": "Esc",
    "Clavier": "Keyboard",
    "Frame précédente / suivante.": "Previous / next frame.",
    "Lecture / pause.": "Play / pause.",
    "Première / dernière frame.": "First / last frame.",
    "Chiffres puis Entrée": "Digits, then Enter",
    "Aller directement à la frame N.": "Go directly to frame N.",
    "Zoom avant / arrière.": "Zoom in / out.",
    "Ouvrir un fichier.": "Open a file.",
    "Ouvrir un dossier.": "Open a folder.",
    "Sauvegarder les clics enregistrés.": "Save recorded clicks.",
    "Annuler le dernier clic.": "Undo the last click.",
    "Effacer tous les clics enregistrés.": "Clear all recorded clicks.",
    "Rotation 90° horaire.": "Rotate 90° clockwise.",
    "Capturer la frame courante en PNG.": "Capture the current frame as PNG.",
    "Copier la frame dans le presse-papiers.": "Copy the frame to the clipboard.",
    "Vue plein cadre (masque toute l'interface).": "Full view (hide the whole interface).",
    "Quitter le plein cadre.": "Exit full view.",

    # Conversion, annotation, and host plugin dialogs.
    "Cible:": "Target:",
    "Sortie:": "Output:",
    "Echantillonnage:": "Sampling:",
    "Cadence MP4:": "MP4 frame rate:",
    "Fichier de sortie": "Output file",
    "Dossier de sortie": "Output folder",
    "Enregistrer le clip...": "Save clip...",
    "Dossier de destination...": "Destination folder...",
    "Dossier de destination pour les PNG...": "Destination folder for PNG files...",
    "Dossier de destination pour les TIFF...": "Destination folder for TIFF files...",
    "Dossier de destination pour les PNG 16 bits...": "Destination folder for 16-bit PNG files...",
    "Dossier de destination des morceaux...": "Segment destination folder...",
    "Exporter le crop ROI...": "Export ROI crop...",
    "Dossier de destination pour les .ver...": "Destination folder for .ver files...",
    "Enregistrer la capture...": "Save capture...",
    "Enregistrer la lecture vidéo...": "Save video recording...",
    "Choisir le dossier du plugin à importer": "Choose the plugin folder to import",
    "Ouvrir un media": "Open media",
    "Ouvrir un dossier d'images": "Open an image folder",
    "Charger un LUT .cube": "Load a .cube LUT",
    "Exporter la composition multi-vues...": "Export multi-view composition...",
    "Enregistrer les clics sous...": "Save clicks as...",
    "Choisir un CSV pour ce graphe": "Choose a CSV for this graph",
    "Exporter profil CSV": "Export profile CSV",
    "Dossier de sortie du dataset YOLO...": "YOLO dataset output folder...",
    "Dossier YOLO source (un .txt par frame)...": "Source YOLO folder (one .txt per frame)...",
    "...ou fichier .txt fusionné": "...or a merged .txt file",
    "Enregistrer le .ver sous...": "Save .ver as...",
    "Extraire le clip": "Extract clip",
    "Extraire les morceaux": "Extract segments",
    "Re-encodage MPEG-4 par cv2. Légère perte acceptable.":
        "MPEG-4 re-encoding with cv2. Slight quality loss is acceptable.",
    "Exporter aussi les calques SIDECAR + annotations (IN→OUT)":
        "Also export SIDECAR layers + annotations (IN→OUT)",
    "Exporter la vue telle qu'affichée (calques + contraste gravés)":
        "Export the view as displayed (layers + contrast burned in)",
    "Créer des liens au lieu de copier (symlink, sans duplication)":
        "Create links instead of copying (symlink, no duplication)",
    "Exporter tel qu'affiché (calques + contraste gravés)":
        "Export as displayed (layers + contrast burned in)",
    "Exporter aussi les calques (SIDECAR, .ver) et données plugin par morceau":
        "Also export layers (SIDECAR, .ver) and plugin data per segment",
    "Chaque morceau est nommé {nom}_{frameIN}_{frameOUT}.":
        "Each segment is named {name}_{frameIN}_{frameOUT}.",
    "ROI fixe": "Fixed ROI",
    "(même zone exportée à chaque frame)": "(same area exported on every frame)",
    "Choisis la plage de frames à exporter, les tracks .ver à inclure, puis comment répartir ces tracks entre les fichiers de sortie.":
        "Choose the frame range to export, the .ver tracks to include, and how to distribute them among output files.",
    "Classes (dans l'ordre) :": "Classes (in order):",
    "Aucun calque .ver chargé sur cette vue -- dépose d'abord un/des .ver.":
        "No .ver layer loaded on this view -- drop one or more .ver files first.",
    "Exporter aussi les images (PNG) — nécessaire pour entraîner":
        "Also export images (PNG) — required for training",
    "Inclure les frames sans annotation (labels vides, fond/négatif)":
        "Include frames without annotations (empty labels, background/negative)",
    "Exporter le dataset YOLO...": "Export YOLO dataset...",
    "Pré-rempli depuis la vue ouverte.": "Pre-filled from the open view.",
    "Somme des 3 pourcentages = 0 -- rien ne serait exporté.":
        "The three percentages total 0 -- nothing would be exported.",
    "Source pré-remplie depuis le calque YOLO chargé sur cette vue.":
        "Source pre-filled from the YOLO layer loaded on this view.",
    "Plusieurs calques YOLO d'origines différentes sont chargés -- choisis le dossier/fichier voulu via Parcourir...":
        "Several YOLO layers from different origins are loaded -- choose the required folder/file with Browse...",
    "Chemin d'origine du calque YOLO introuvable -- choisis-le via Parcourir...":
        "The YOLO layer's original path was not found -- select it with Browse...",
    "Coche au moins un calque .ver à exporter.": "Select at least one .ver layer to export.",
    "Aucune boîte dans les calques .ver cochés -- rien à exporter.":
        "No box in the selected .ver layers -- nothing to export.",
    "Choisis un dossier de sortie.": "Choose an output folder.",
    "Aucune séquence ouverte sur cette vue.": "No sequence is open on this view.",
    "Le split ne peut pas être 0/0/0.": "The split cannot be 0/0/0.",
    "Choisis un dossier ou fichier YOLO source valide.": "Choose a valid source YOLO folder or file.",
    "Choisis un chemin de sortie .ver.": "Choose a .ver output path.",
    "Importer -> écrire le .ver...": "Import -> write .ver...",
    "Nom du plugin :": "Plugin name:",
    "Entrée frame courante :": "Current-frame input:",
    "+ Ajouter une entrée": "+ Add input",
    "Nouveau plugin code": "New code plugin",
    "Console plugins": "Plugin console",
    "Tester sur la frame courante": "Test on current frame",
    "Diagnostic environnement": "Environment diagnostics",
    "Exporter frame + commande VS Code": "Export frame + VS Code command",
    "Défilement auto": "Auto-scroll",
    "Aide plugin : hooks et api": "Plugin help: hooks and API",
    "Éditeur de plugin": "Plugin editor",
    "(aucun plugin sélectionné)": "(no plugin selected)",
    "Aucun plugin sélectionné.": "No plugin selected.",
    "Enregistré.": "Saved.",
    "Enregistré + plugins rechargés.": "Saved + plugins reloaded.",
    "Ajouter un bloc": "Add a block",
    "Charger un CSV...": "Load a CSV...",
    "Colonne frame :": "Frame column:",
    "Tester (frame courante)": "Test (current frame)",
    "Enregistrer + recharger": "Save + reload",
    "Éditeur de graphe -- mode d'emploi": "Graph editor -- user guide",
    "Insérer l'exemple fonctionnel (keypoints)": "Insert working example (keypoints)",
    "Exemple keypoints inséré. Vérifie la « Colonne frame », puis « Tester (frame courante) » ou « Enregistrer + recharger ».":
        "Keypoint example inserted. Check “Frame column”, then use “Test (current frame)” or “Save + reload”.",
    "Colonne du CSV qui identifie la frame (regroupe les lignes par frame avant d'evaluer le graphe). Laisse vide = 1re colonne.":
        "CSV column that identifies the frame (rows are grouped by frame before graph evaluation). Leave empty to use the first column.",
    "Comment ça marche : le principe, un exemple fonctionnel à insérer, les conditions requises.":
        "How it works: the principle, a working example to insert, and required conditions.",
    "Enregistre le graphe puis recharge les plugins et redessine la vue -- le resultat s'affiche tout de suite si le plugin est actif.":
        "Save the graph, reload plugins, and redraw the view. The result appears immediately when the plugin is enabled.",
    "Clic droit (ou double-clic) sur le fond = palette de blocs (tape pour chercher, Entrée pour ajouter). Clique une sortie (bleue) puis une entrée (orange) pour relier. Molette = zoom, bouton du milieu = déplacer la vue. Suppr = retirer.":
        "Right-click (or double-click) the background to open the block palette; type to search and press Enter to add. Click an output (blue), then an input (orange) to connect them. Mouse wheel zooms, middle-button drag pans, and Delete removes items.",
    "<b>Plugins</b> (dossier plugins/)": "<b>Plugins</b> (plugins/ folder)",
    "Nouveau plugin...": "New plugin...",
    "Enregistrer + recharger les plugins": "Save + reload plugins",
    "<b>Objets et actions disponibles</b><br>(double-clic pour insérer)":
        "<b>Available objects and actions</b><br>(double-click to insert)",
    "« Actions/compétences » (colonne du haut) : méthodes que TON plugin peut définir (get_overlays, render_patch, ...).\n« Objets » (colonne du bas) : méthodes de l'app que TON code peut appeler via api.xxx(...) pour lire des données ou agir sur l'interface.":
        "“Actions/capabilities” (upper section): methods YOUR plugin can define (get_overlays, render_patch, ...).\n“Objects” (lower section): application methods YOUR code can call through api.xxx(...) to read data or act on the interface.",
    "Journal des plugins : api.log(), print(), erreurs et tests. Pour un vrai debug pas-a-pas (breakpoints), exporte la frame et lance le runner sous VS Code (bouton ci-dessous).":
        "Plugin log: api.log(), print output, errors, and tests. For step-by-step debugging with breakpoints, export the frame and run the runner in VS Code (button below).",
    "Execute les hooks de rendu du plugin selectionne sur la frame courante et affiche le resultat/erreurs.":
        "Run the selected plugin's rendering hooks on the current frame and display results/errors.",
    "Montre d'ou numpy/pandas/cv2 sont reellement charges (bundle vs env externe) et les site-packages actifs.":
        "Show where numpy/pandas/cv2 are actually loaded from (bundle vs external environment) and active site-packages.",
    "Ecrit la frame courante sur disque et donne la commande prete a coller pour debugger le plugin dans VS Code.":
        "Write the current frame to disk and provide a ready-to-run command for debugging the plugin in VS Code.",
    "Comprendre les bibliothèques des plugins et les méthodes de debug dans FrameViewer ou VS Code.":
        "Understand plugin libraries and debugging methods in FrameViewer or VS Code.",
    "Vert = <b>REQUIS</b>. En dessous, les hooks a definir et les objets api.xxx a appeler, par theme. Chaque entree : a quoi ca sert + un exemple ultra simple. (Pour inserer du code, utilise l'editeur.)":
        "Green = <b>REQUIRED</b>. Below are hooks to define and api.xxx objects to call, grouped by topic. Each entry explains its purpose and gives a minimal example. Use the editor to insert code.",
    "Entrées fichiers -- une variable par fichier à glisser-déposer.\nLe nom doit être un identifiant Python (ex. csv_plots) : il devient self.<nom> dans le code.":
        "File inputs -- one variable per dropped file.\nThe name must be a Python identifier (for example csv_plots); it becomes self.<name> in code.",
    "Écrit à côté du clip, ré-indexés sur la plage IN→OUT :\n• les calques SIDECAR (overlays.sidecar)\n• les annotations .ver / YOLO":
        "Write beside the clip, re-indexed to the IN→OUT range:\n• SIDECAR layers (overlays.sidecar)\n• .ver / YOLO annotations",
    "Pour une séquence d'images extraite dans son format d'origine :\ncrée un lien vers chaque fichier source au lieu de le copier.\nEssaie symlink, puis lien physique, puis copie en dernier recours.":
        "For an image sequence extracted in its original format:\ncreate a link to each source file instead of copying it.\nTry a symlink, then a hard link, and copy only as a final fallback.",
    "Symlink possible uniquement avec « Copie des fichiers originaux » (séquence extraite telle quelle, entrée = sortie). Les autres formats ré-encodent chaque image : il n'existe plus de fichier source unique à lier, donc aucun lien n'est possible.":
        "Symlinks are available only with “Copy original files” (the extracted sequence is unchanged, input equals output). Other formats re-encode every image, so there is no single source file to link.",
    "Écrit à côté de CHAQUE morceau, ré-indexés sur sa plage :\n• les calques SIDECAR (overlays.sidecar)\n• les annotations .ver / YOLO\n• les CSV/TSV déposés sur les plugins actifs (si colonne de frame)":
        "Write beside EACH segment, re-indexed to its range:\n• SIDECAR layers (overlays.sidecar)\n• .ver / YOLO annotations\n• CSV/TSV files dropped onto enabled plugins (when they have a frame column)",
    "Noms de classe, dans l'ordre de leur index (0, 1, 2...).\nUtilisé dans les deux sens : index -> nom (export .ver) et\nnom -> index (les classes .ver inconnues de cette liste sont\nignorées à l'export YOLO).":
        "Class names in index order (0, 1, 2...).\nUsed both ways: index -> name (.ver export) and\nname -> index (.ver classes absent from this list are\nignored during YOLO export).",
    "Source : calques .ver chargés sur cette vue (décoche ceux à exclure). « Label id » = id de classe YOLO écrit pour ce track à l'export -- un .ver a des sous-classes (ex. drone/quadcoptère/mavic) que YOLO n'a pas : plusieurs tracks peuvent donc partager le même id (0 par défaut partout).":
        "Source: .ver layers loaded on this view (clear those to exclude). “Label id” is the YOLO class ID written for this track during export. A .ver file can have subclasses that YOLO does not, so several tracks may share the same ID (0 by default).",
    "⚠ YOLO ne fournit aucun identifiant d'objet entre frames. Le track_id écrit dans le .ver est assigné par position dans chaque frame — ce n'est PAS un vrai suivi temporel.":
        "⚠ YOLO provides no object identity across frames. The track_id written to .ver is assigned by position within each frame — it is NOT true temporal tracking.",
}


PHRASE_ENGLISH = (
    ("Active REC clics puis clique dans l'image. Le point est ajoute immediatement dans :", "Enable click recording, then click the image. The point is added immediately to:"),
    ("<dossier de la source>/<nom_source>_clicks.txt", "<source folder>/<source_name>_clicks.txt"),
    ("Le fichier contient frame,x,y. Annuler, Effacer et Ctrl+S completent le flux.", "The file contains frame,x,y. Undo, Clear, and Ctrl+S complete the workflow."),
    ("Le point est enregistre dans", "The point is recorded in"),
    ("le fichier de clics", "the click file"),
    ("Desactive maintenant REC clics pour retrouver un clic normal sur le canvas.", "Disable click recording now to restore normal canvas clicks."),
    ("Fichier pour «", "File for “"),
    (" »", "”"),
    ("le clip", "the clip"),
    ("le crop ROI", "the ROI crop"),
    ("aucune source à convertir", "no source to convert"),
    ("aucune source a convertir", "no source to convert"),
    ("réouverture impossible", "could not reopen"),
    ("reouverture impossible", "could not reopen"),
    ("Enregistrement", "Recording"),
    ("Vidéo enregistrée", "Video saved"),
    ("Video enregistree", "Video saved"),
    ("Erreur annot", "Annotation error"),
    ("Erreur données plugin", "Plugin data error"),
    ("Extrait (tel qu'affiché, calques inclus)", "Extracted (as displayed, layers included)"),
    ("Extrait (re-encodé)", "Extracted (re-encoded)"),
    ("Extrait vidéo", "Video extracted"),
    ("Extrait image", "Image extracted"),
    ("Fichiers copiés", "Files copied"),
    ("PNG exportés", "PNG exported"),
    ("Crop ROI exporté", "ROI crop exported"),
    ("Heatmap histogrammes", "Histogram heatmap"),
    ("Liens créés", "Links created"),
    ("lien physique", "hard link"),
    ("copie ", "copy "),
    ("Somme =", "Total ="),
    ("sera normalisée automatiquement", "will be normalized automatically"),
    ("sera normalisee automatiquement", "will be normalized automatically"),
    ("Erreur export", "Export error"),
    ("Erreur import", "Import error"),
    ("Dataset YOLO écrit", "YOLO dataset written"),
    ("Dataset YOLO ecrit", "YOLO dataset written"),
    (".ver écrit", ".ver written"),
    (".ver ecrit", ".ver written"),
    ("Créé --", "Created --"),
    ("Cree --", "Created --"),
    ("Colonnes CSV", "CSV columns"),
    ("colonne(s) proposée(s)", "suggested column(s)"),
    ("colonne(s) proposee(s)", "suggested column(s)"),
    ("aperçu du dossier", "folder preview"),
    ("apercu du dossier", "folder preview"),
    ("Relié", "Connected"),
    ("Relie", "Connected"),
    ("Aucune boîte trouvée dans", "No box found in"),
    ("Aucune boite trouvee dans", "No box found in"),
    ("non écrit (ou vide)", "not written (or empty)"),
    ("non ecrit (ou vide)", "not written (or empty)"),
    ("boîte(s) sur", "box(es) across"),
    ("boite(s) sur", "box(es) across"),
    ("Frames valides", "Valid frames"),
    ("Associe une colonne pour", "Map a column for"),
    ("Extraire les morceaux", "Extract segments"),
    ("morceau(x)", "segment(s)"),
    ("plage(s)", "range(s)"),
    ("au total", "total"),
    ("ROI fixe", "Fixed ROI"),
    ("même zone exportée à chaque frame", "same area exported on every frame"),
    ("meme zone exportee a chaque frame", "same area exported on every frame"),
    ("fixe ou suivi .ver", "fixed or .ver-tracked"),
    ("px de fond en plus tout autour", "extra background pixels around it"),
    ("Image X = frame, Y = niveau de gris (0-255), couleur = nombre de pixels", "Image X = frame, Y = grayscale level (0-255), color = pixel count"),
    ("Grave directement dans chaque frame exportée", "Burn directly into every exported frame"),
    ("contraste/LUT/filtres courants", "current contrast/LUT/filters"),
    ("boîtes .ver visibles", "visible .ver boxes"),
    ("boites .ver visibles", "visible .ver boxes"),
    ("calque SIDECAR (si coché)", "SIDECAR layer (when enabled)"),
    ("S'applique aux formats", "Applies to formats"),
    ("re-encodé", "re-encoded"),
    ("Préchargement", "Preloading"),
    ("sequences images", "image sequences"),
    ("séquences images", "image sequences"),
    ("Charge N frames en avance dans un thread de fond.", "Load N frames ahead in a background thread."),
    ("Réduit les saccades en lecture réseau SSH.", "Reduces stutter during SSH network playback."),
    ("Désactivé pour les vidéos MP4", "Disabled for MP4 videos"),
    ("Entrées", "Inputs"),
    ("Entrees", "Inputs"),
    ("Jeux actifs", "Active sets"),
    ("Statut", "Status"),
    ("Dernière exécution", "Last run"),
    ("Derniere execution", "Last run"),
    ("Inactif", "Disabled"),
    ("actif(s)", "enabled"),
    ("Exporte cette ROI", "Export this ROI"),
    ("fixe, ou suivant une boîte .ver si un suivi est actif", "fixed, or following a .ver box when tracking is active"),
    ("fixe, ou suivant une boîte .ver si un suivi\nest actif", "fixed, or following a .ver box when tracking is active"),
    ("dossier PNG", "PNG folder"),
    ("sur toute la séquence", "across the full sequence"),
    ("sur toute la sequence", "across the full sequence"),
    ("avec option heatmap des histogrammes", "with an optional histogram heatmap"),
    ("Outils TI", "Image tools"),
    ("Historique des sources", "Source history"),
    ("Lecture en boucle", "Loop playback"),
    ("Séquence d'images", "Image sequence"),
    ("Sequence d'images", "Image sequence"),
    ("calques/contraste possibles", "layers/contrast supported"),
    ("Copie des fichiers originaux", "Copy original files"),
    ("sans dégradation", "lossless"),
    ("sans degradation", "lossless"),
    ("frames rendues 8 bits", "rendered 8-bit frames"),
    ("données brutes", "raw data"),
    ("donnees brutes", "raw data"),
    ("16 bits conservés", "16-bit preserved"),
    ("16 bits conserves", "16-bit preserved"),
    ("Fichier ", "File "),
    ("Marge ajoutée de chaque côté du rectangle", "Margin added on each side of the rectangle"),
    ("Marge ajoutee de chaque cote du rectangle", "Margin added on each side of the rectangle"),
    ("couleur = nombre de pixels", "color = pixel count"),
    ("histogramme de la ROI empilé", "ROI histograms stacked"),
    ("histogramme de la ROI empile", "ROI histograms stacked"),
    ("Ne correspond pas", "Does not match"),
    ("fichier =", "file ="),
    ("reste", "remainder"),
    ("Ajustez largeur/hauteur/format jusqu'a", "Adjust width/height/format until"),
    ("Visite interactive complete de FrameViewer", "Complete interactive FrameViewer tour"),
    ("Ouvre/ferme le panneau", "Open/close the panel"),
    ("Disposition des vues", "View layout"),
    ("Vue unique", "Single view"),
    ("Côte à côte", "Side by side"),
    ("Cote a cote", "Side by side"),
    ("Empilé", "Stacked"),
    ("Empile", "Stacked"),
    ("Grille", "Grid"),
    ("Trois colonnes", "Three columns"),
    ("Vues liées", "Linked views"),
    ("Vues liees", "Linked views"),
    ("Même flux", "Same stream"),
    ("Meme flux", "Same stream"),
    ("Liaison active", "Link enabled"),
    ("frames de départ", "starting frames"),
    ("Frames de départ", "Starting frames"),
    ("frame de départ", "starting frame"),
    ("Frame de départ", "Starting frame"),
    ("Aucune source chargée", "No source loaded"),
    ("Aucune source chargee", "No source loaded"),
    ("Aucun fichier", "No file"),
    ("Aucun calque", "No layer"),
    ("Aucune frame", "No frame"),
    ("Dossier d'images", "Image folder"),
    ("dossier d'images", "image folder"),
    ("Fichier image", "Image file"),
    ("fichier image", "image file"),
    ("Fichier vidéo", "Video file"),
    ("fichier vidéo", "video file"),
    ("Cadence MP4", "MP4 frame rate"),
    ("Format de sortie", "Output format"),
    ("Plage de frames", "Frame range"),
    ("Choisir un dossier", "Choose a folder"),
    ("Choisir un fichier", "Choose a file"),
    ("Parcourir...", "Browse..."),
    ("Enregistrer sous", "Save as"),
    ("Enregistrement actif", "Recording enabled"),
    ("Dernier clic annule", "Last click undone"),
    ("Clics effaces", "Clicks cleared"),
    ("Conversion terminee", "Conversion completed"),
    ("Conversion terminée", "Conversion completed"),
    ("Erreur", "Error"),
    ("Impossible de", "Unable to"),
    ("Exporter", "Export"),
    ("Extraire", "Extract"),
    ("Convertir", "Convert"),
    ("Convertit", "Convert"),
    ("Chargement", "Loading"),
    ("Ouvrir", "Open"),
    ("Fermer", "Close"),
    ("Annuler", "Cancel"),
    ("Appliquer", "Apply"),
    ("Réinitialiser", "Reset"),
    ("Reinitialiser", "Reset"),
    ("Supprimer", "Delete"),
    ("Enregistrer", "Save"),
    ("Paramètres", "Settings"),
    ("Parametres", "Settings"),
    ("Historique", "History"),
    ("Calques", "Layers"),
    ("Calque", "Layer"),
    ("Histogramme", "Histogram"),
    ("Outils", "Tools"),
    ("Annotations", "Annotations"),
    ("Opacité", "Opacity"),
    ("Opacite", "Opacity"),
    ("Remplissage", "Fill"),
    ("Épaisseur", "Thickness"),
    ("Epaisseur", "Thickness"),
    ("Confiance", "Confidence"),
    ("Taille", "Size"),
    ("Aperçu", "Preview"),
    ("Apercu", "Preview"),
    ("Précédent", "Previous"),
    ("Precedent", "Previous"),
    ("Suivant", "Next"),
    ("Terminer", "Finish"),
    ("Quitter", "Exit"),
    ("Activer", "Enable"),
    ("Désactiver", "Disable"),
    ("Desactiver", "Disable"),
    ("Affichage", "Display"),
    ("Navigation", "Navigation"),
    ("Source", "Source"),
    ("Fichier", "File"),
    ("Dossier", "Folder"),
    ("Image", "Image"),
    ("Vidéo", "Video"),
    ("Video", "Video"),
    ("Réglages", "Settings"),
    ("Reglages", "Settings"),
    ("A faire", "To do"),
    ("Cliquer", "Click"),
    ("cliquer", "click"),
    ("Glisse", "Drag"),
    ("glisser", "drag"),
    ("Déposer", "Drop"),
    ("Deposer", "Drop"),
    ("deposer", "drop"),
    ("sélectionnée", "selected"),
    ("selectionnee", "selected"),
    ("courante", "current"),
    ("courant", "current"),
    ("Toutes les vues", "All views"),
    ("toutes les vues", "all views"),
)


def normalize_language(value: object) -> str:
    language = str(value or "fr").strip().lower()
    return language if language in SUPPORTED_LANGUAGES else "fr"


@lru_cache(maxsize=4096)
def translate_text(text: str, language: str) -> str:
    """Translate an application-owned label while preserving unknown content."""
    if language != "en" or not text:
        return text
    translated = EXACT_ENGLISH.get(text)
    if translated is not None:
        return translated
    # Buttons often add one leading space before an icon. Catalog entries stay
    # readable while the visual spacing is preserved exactly.
    stripped = text.strip()
    translated = EXACT_ENGLISH.get(stripped)
    if translated is not None:
        start = len(text) - len(text.lstrip())
        end = len(text) - len(text.rstrip())
        return text[:start] + translated + (text[len(text) - end:] if end else "")
    translated = text
    for source, target in PHRASE_ENGLISH:
        translated = translated.replace(source, target)
    translated = re.sub(r"\bframe\(s\)\b", "frame(s)", translated)
    return translated


def ui_text(widget: QtWidgets.QWidget, text: str) -> str:
    """Translate text using the language selected by the containing window."""
    window = widget.window()
    language = getattr(window, "_ui_language", "fr")
    return translate_text(text, language)


def set_ui_text(widget: QtWidgets.QWidget, text: str) -> None:
    """Set a dynamic application label in the selected language."""
    target = ui_text(widget, text)
    widget.setProperty("_fv_i18n_source_text", text)
    widget.setProperty("_fv_i18n_translated_text", target)
    widget.setText(target)


def set_ui_pair(widget: QtWidgets.QWidget, french: str, english: str) -> None:
    """Set already-formatted dynamic FR/EN text with no translation work."""
    language = getattr(widget.window(), "_ui_language", "fr")
    target = english if language == "en" else french
    widget.setProperty("_fv_i18n_source_text", french)
    widget.setProperty("_fv_i18n_translated_text", english)
    widget.setText(target)


def set_ui_tooltip(widget: QtWidgets.QWidget, text: str) -> None:
    """Set a dynamic tooltip while retaining its untranslated source."""
    target = ui_text(widget, text)
    widget.setProperty("_fv_i18n_source_tooltip", text)
    widget.setProperty("_fv_i18n_translated_tooltip", target)
    widget.setToolTip(target)


class LocalizedStatusBar(QtWidgets.QStatusBar):
    """Translate status messages only when the application sets a message."""

    def __init__(self, window: QtWidgets.QMainWindow):
        super().__init__(window)
        self._window = window
        self._source_message = ""
        self._source_timeout = 0

    def showMessage(self, message: str, timeout: int = 0) -> None:
        self._source_message = message
        self._source_timeout = timeout
        language = getattr(self._window, "_ui_language", "fr")
        super().showMessage(translate_text(message, language), timeout)

    def retranslate(self) -> None:
        if self._source_message:
            # A language switch should not restart a transient timeout.
            language = getattr(self._window, "_ui_language", "fr")
            super().showMessage(translate_text(self._source_message, language))


class UiTranslationController(QtCore.QObject):
    """Apply runtime translations to application-owned Qt widgets."""

    _SOURCE_PREFIX = "_fv_i18n_source_"
    _TRANSLATED_PREFIX = "_fv_i18n_translated_"
    _APPLIED_LANGUAGE = "_fv_i18n_applied_language"

    def __init__(self, window: QtWidgets.QMainWindow, language: str = "fr"):
        super().__init__(window)
        self.window = window
        self.language = normalize_language(language)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        if self.language == "en":
            self.refresh()

    def set_language(self, language: str) -> None:
        self.language = normalize_language(language)
        self.refresh()

    def eventFilter(self, watched, event):
        if (self.language == "en" and event.type() == QtCore.QEvent.Show
                and isinstance(watched, QtWidgets.QWidget)
                and self._belongs_to_window(watched)
                and not self._plugin_owned(watched)
                and watched.property(self._APPLIED_LANGUAGE) != self.language):
            self._translate_widget(watched)
        return False

    def _belongs_to_window(self, obj: QtCore.QObject) -> bool:
        current = obj
        while current is not None:
            if current is self.window:
                return True
            current = current.parent()
        return False

    @staticmethod
    def _plugin_owned(obj: QtCore.QObject) -> bool:
        current = obj
        while current is not None:
            if bool(current.property("frameviewer_plugin_owned")):
                return True
            current = current.parent()
        return False

    def _property_text(self, obj, key: str, getter, setter) -> None:
        current = getter()
        if not isinstance(current, str):
            return
        source_key = self._SOURCE_PREFIX + key
        translated_key = self._TRANSLATED_PREFIX + key
        source = obj.property(source_key)
        previous = obj.property(translated_key)
        if source is None or (self.language == "en" and previous is not None
                              and current not in (source, previous)):
            source = current
            obj.setProperty(source_key, source)
        if self.language == "fr":
            target = str(source)
        else:
            target = translate_text(str(source), "en")
        obj.setProperty(translated_key, target)
        if current != target:
            setter(target)

    def _translate_action(self, action: QtGui.QAction) -> None:
        if self._plugin_owned(action):
            return
        self._property_text(action, "text", action.text, action.setText)
        self._property_text(action, "tooltip", action.toolTip, action.setToolTip)
        self._property_text(action, "status", action.statusTip, action.setStatusTip)

    def _translate_combo(self, combo: QtWidgets.QComboBox) -> None:
        key = "_fv_i18n_combo_sources"
        sources = combo.property(key)
        current = [combo.itemText(index) for index in range(combo.count())]
        previous = combo.property("_fv_i18n_combo_translated")
        if sources is None or (self.language == "en" and previous is not None
                               and current != list(previous)):
            sources = current
            combo.setProperty(key, sources)
        sources = list(sources)
        if len(sources) != combo.count():
            sources = current
            combo.setProperty(key, sources)
        targets = sources if self.language == "fr" else [
            translate_text(str(item), "en") for item in sources
        ]
        for index, target in enumerate(targets):
            if combo.itemText(index) != target:
                combo.setItemText(index, target)
        combo.setProperty("_fv_i18n_combo_translated", targets)

    def _translate_tabs(self, tabs: QtWidgets.QTabWidget) -> None:
        key = "_fv_i18n_tab_sources"
        sources = tabs.property(key)
        current = [tabs.tabText(index) for index in range(tabs.count())]
        previous = tabs.property("_fv_i18n_tab_translated")
        if sources is None or (self.language == "en" and previous is not None
                               and current != list(previous)):
            sources = current
            tabs.setProperty(key, sources)
        sources = list(sources)
        if len(sources) != tabs.count():
            sources = current
            tabs.setProperty(key, sources)
        targets = sources if self.language == "fr" else [
            translate_text(str(item), "en") for item in sources
        ]
        for index, target in enumerate(targets):
            if tabs.tabText(index) != target:
                tabs.setTabText(index, target)
        tabs.setProperty("_fv_i18n_tab_translated", targets)

    def _translate_widget(self, widget: QtWidgets.QWidget) -> None:
        if self._plugin_owned(widget):
            return
        self._property_text(widget, "tooltip", widget.toolTip, widget.setToolTip)
        self._property_text(widget, "status", widget.statusTip, widget.setStatusTip)
        self._property_text(widget, "whats_this", widget.whatsThis, widget.setWhatsThis)
        if widget.isWindow():
            self._property_text(
                widget, "window_title", widget.windowTitle, widget.setWindowTitle
            )
        if isinstance(widget, QtWidgets.QLabel):
            self._property_text(widget, "text", widget.text, widget.setText)
        elif isinstance(widget, QtWidgets.QAbstractButton):
            self._property_text(widget, "text", widget.text, widget.setText)
        elif isinstance(widget, QtWidgets.QGroupBox):
            self._property_text(widget, "title", widget.title, widget.setTitle)
        elif isinstance(widget, QtWidgets.QLineEdit):
            self._property_text(
                widget, "placeholder", widget.placeholderText, widget.setPlaceholderText
            )
        if isinstance(widget, QtWidgets.QComboBox):
            self._translate_combo(widget)
        if isinstance(widget, QtWidgets.QTabWidget):
            self._translate_tabs(widget)
        for action in widget.actions():
            self._translate_action(action)
        for action in widget.findChildren(QtGui.QAction):
            self._translate_action(action)
        for child in widget.findChildren(QtWidgets.QWidget):
            if child is not widget and not self._plugin_owned(child):
                self._translate_widget_shallow(child)
        widget.setProperty(self._APPLIED_LANGUAGE, self.language)

    def _translate_widget_shallow(self, widget: QtWidgets.QWidget) -> None:
        self._property_text(widget, "tooltip", widget.toolTip, widget.setToolTip)
        self._property_text(widget, "status", widget.statusTip, widget.setStatusTip)
        self._property_text(widget, "whats_this", widget.whatsThis, widget.setWhatsThis)
        if widget.isWindow():
            self._property_text(
                widget, "window_title", widget.windowTitle, widget.setWindowTitle
            )
        if isinstance(widget, QtWidgets.QLabel):
            self._property_text(widget, "text", widget.text, widget.setText)
        elif isinstance(widget, QtWidgets.QAbstractButton):
            self._property_text(widget, "text", widget.text, widget.setText)
        elif isinstance(widget, QtWidgets.QGroupBox):
            self._property_text(widget, "title", widget.title, widget.setTitle)
        elif isinstance(widget, QtWidgets.QLineEdit):
            self._property_text(
                widget, "placeholder", widget.placeholderText, widget.setPlaceholderText
            )
        if isinstance(widget, QtWidgets.QComboBox):
            self._translate_combo(widget)
        if isinstance(widget, QtWidgets.QTabWidget):
            self._translate_tabs(widget)
        if isinstance(widget, QtWidgets.QDialogButtonBox):
            labels = {
                QtWidgets.QDialogButtonBox.Ok: ("OK", "OK"),
                QtWidgets.QDialogButtonBox.Cancel: ("Annuler", "Cancel"),
                QtWidgets.QDialogButtonBox.Close: ("Fermer", "Close"),
                QtWidgets.QDialogButtonBox.Save: ("Enregistrer", "Save"),
                QtWidgets.QDialogButtonBox.Apply: ("Appliquer", "Apply"),
                QtWidgets.QDialogButtonBox.Reset: ("Réinitialiser", "Reset"),
                QtWidgets.QDialogButtonBox.Yes: ("Oui", "Yes"),
                QtWidgets.QDialogButtonBox.No: ("Non", "No"),
            }
            for standard, (french, english) in labels.items():
                button = widget.button(standard)
                if button is not None:
                    button.setText(english if self.language == "en" else french)
        for action in widget.actions():
            self._translate_action(action)
        widget.setProperty(self._APPLIED_LANGUAGE, self.language)

    def refresh(self) -> None:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        roots = [self.window]
        roots.extend(
            widget for widget in app.topLevelWidgets()
            if widget is not self.window and self._belongs_to_window(widget)
        )
        for root in roots:
            if not self._plugin_owned(root):
                self._translate_widget(root)
