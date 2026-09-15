# BNP2OFX — cahier pour Grok Build

Mini logiciel **local** : convertir un relevé bancaire **BNP Paribas** (PDF texte ou TXT) en fichier **OFX**, utilisable par quelqu’un de non technique (fenêtre, 2 boutons).

Aucune donnée ne doit quitter l’ordinateur.

---

## Objectif produit

| Entrée | Sortie | Utilisateur |
|---|---|---|
| PDF relevé BNP (espace client) ou TXT issu de `pdftotext -layout` | `.ofx` importable (Sage, Cegid, GnuCash, Actual Budget, Pennylane si OFX accepté) | Double-clic → choisir fichier → enregistrer OFX |

Hors scope v1 :

- OCR / PDF scanné (photo)
- Autres banques (CA, SG, Boursorama…)
- Compte titres, relevé carte seul, Relevé Multi-Choix
- Envoi cloud, compte utilisateur, télémétrie
- Signature Authenticode / installateur MSI

---

## Contexte (pourquoi ce projet)

- BNP donne un **OFX natif** seulement sur ~90 jours (`Télécharger mes opérations`).
- Les relevés officiels archivés (e-Documents, jusqu’à 10 ans) sont des **PDF** (`RCHQ_101_<compte>_<date>.pdf`).
- Un convertisseur générique type PDFtoOFX GitHub est calé sur **ANZ Plus (Australie)**, inutilisable tel quel.
- Les services en ligne (RapidOFX, OFXpress) marchent mais envoient le relevé sur un serveur.
- Un parseur « une banque + un layout » est faisable en quelques soirs ; le dur n’est pas l’OFX, c’est le tableau PDF.

Layout typique BNP compte chèque :

```
Date | Valeur | Nature des opérations | Débit | Crédit
JJ/MM/AAAA
montants 1 234,56
libellés sur 2–3 lignes
ancien solde / nouveau solde (pas de solde à chaque ligne)
```

Un changement de colonnes (BNP l’a déjà fait 2012–2018) casse les regex. D’où le **contrôle des soldes** obligatoire.

---

## Fichiers déjà écrits (à copier dans le dossier Grok Build)

```
bnp2ofx/
├── CAHIER.md                 ← ce fichier
├── README.md                 ← usage utilisateur
├── requirements.txt          ← pdfplumber, pyinstaller
├── bnp2ofx.py                ← parseur + OFX + GUI tkinter
└── exemple_releve_bnp.txt    ← relevé fictif de test
```

### Rôles

- `bnp2ofx.py` : tout-en-un
  - extraction texte (`pdfplumber` ou lecture `.txt`)
  - parseur heuristique BNP
  - génération OFX 1.02 SGML
  - GUI tkinter (pas de dépendance UI)
  - CLI : `python bnp2ofx.py entree.pdf sortie.ofx`
- `exemple_releve_bnp.txt` : 6 opérations, soldes qui tombent juste  
  `1250,40 + 2480,00 − 923,79 = 2806,61`

---

## Comportement attendu

1. Lancer sans argument → fenêtre.
2. Bouton « Choisir le relevé » → PDF ou TXT.
3. Extraire + parser.
4. Afficher : n° compte si trouvé, période, nb d’opérations, soldes, aperçu, **avertissement si soldes faux**.
5. Bouton « Convertir » → dialogue Enregistrer sous `.ofx`.
6. Si 0 opération → erreur, pas d’OFX.

Règle de vérité :

```
ancien_solde + somme(montants signés) ≈ nouveau_solde   (± 0,05 €)
```

Montant OFX : **négatif = débit**, positif = crédit.

---

## Règles de parsing à conserver / améliorer

- Une opération **commence** par une date `JJ/MM/AAAA` (éventuellement Date + Valeur).
- Les lignes suivantes **sans date** = suite du libellé ; le montant peut être sur la 2e ligne.
- Deux colonnes Débit / Crédit : une seule remplie en général.
- Un seul montant + libellé « VIREMENT EN VOTRE FAVEUR » / SALAIRE / REMBOURSEMENT → crédit.
- Libellé PRELEVEMENT / PAIEMENT PAR CARTE / VIREMENT SEPA EMIS / FRAIS → débit.
- Ignorer en-têtes, « Page n », mentions légales, pub.
- IBAN `FRxx` → `BANKID` = code banque (30004), `ACCTID` = compte.

Pièges connus à tester plus tard :

- débit différé carte + détail en fin de relevé (doublons)
- nouvelle colonne « Réf. »
- montant seul sur la ligne suivante
- PDF multi-pages

---

## OFX cible

- En-tête OFX 1.02 SGML (large compatibilité FR)
- `CURDEF` EUR, `LANGUAGE` FRA, `ACCTTYPE` CHECKING
- `FITID` stable (hash date + montant + libellé + index) pour éviter les doublons à l’import
- `NAME` 32 car., `MEMO` libellé complet
- `LEDGERBAL` = nouveau solde si connu

---

## Packaging Windows (plus tard, sur un PC Windows)

```bat
pip install pdfplumber pyinstaller
pyinstaller --onefile --windowed --name BNP2OFX bnp2ofx.py
```

Sortie : `dist\BNP2OFX.exe`.

SmartScreen dira « éditeur inconnu » (exe non signé). Documenter « Informations complémentaires → Exécuter quand même ».

---

## Backlog Grok Build suggéré

Priorité 1

- [ ] Garder le flux GUI + CLI actuel
- [ ] Ajouter des tests unitaires sur `exemple_releve_bnp.txt`
- [ ] Afficher clairement l’écart de soldes
- [ ] Drag & drop du PDF sur la fenêtre

Priorité 2

- [ ] Parser par positions X (`pdfplumber.extract_words`) plutôt que seulement le texte
- [ ] Option « forcer crédit / débit » sur une ligne dans l’aperçu
- [ ] Export CSV en plus de l’OFX
- [x] Lot : un dossier de PDF → un OFX par fichier

Priorité 3

- [x] Icone + installeur Windows (Inno Setup ou secours PyInstaller)
- [ ] Détection « ce n’est pas un relevé BNP »
- [ ] OCR optionnel (hors v1)

---

## Contraintes Grok Build

- Python 3.11+
- Dépendance runtime : `pdfplumber` uniquement
- GUI : `tkinter` (stdlib)
- Pas de réseau dans le convertisseur
- Code et UI en **français**
- Ne pas logger IBAN / nom / montants dans un fichier persistant

---

## Prompt de reprise (à coller dans Grok Build)

> Voici le dossier `bnp2ofx` (cahier + `bnp2ofx.py` + exemple TXT).  
> Améliore le mini-logiciel local PDF/TXT BNP Paribas → OFX.  
> Ne change pas l’esprit : hors ligne, 2 boutons, contrôle des soldes.  
> Ajoute tests sur `exemple_releve_bnp.txt`, drag-and-drop, et un aperçu éditable si une ligne est mal signée.  
> Ne pas ajouter d’appel réseau ni d’OCR en v1.

---

## Rappel sécurité

Un relevé contient IBAN et historique.  
Travailler uniquement en local. Ne pas coller un vrai relevé dans un chat cloud sans masquer nom / IBAN.
