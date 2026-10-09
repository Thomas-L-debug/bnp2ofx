# BNP2OFX

Petit logiciel fait **pour ma famille**.

BNP Paribas ne propose un vrai export (OFX / QIF) que sur les **~90 derniers jours** (« Télécharger mes opérations »).  
Les relevés plus anciens, dans les e-Documents, ne sont disponibles **qu’en PDF**.  
Les sites en ligne du type « PDF vers OFX » marchent, mais ils envoient le relevé — nom, IBAN, historique — sur un serveur inconnu. Ce n’est pas une option.

Ce programme tourne **uniquement sur l’ordinateur**. On lui donne un PDF (ou tout un dossier de PDF), il sort des fichiers **OFX** importables dans un logiciel de compta. Rien ne part sur Internet.

- Un relevé, ou **tout un dossier** de relevés d’un coup
- Aucun envoi en ligne
- Contrôle basique des soldes (pour vérifier que rien n’a été oublié)

## Pour quelqu’un de non expérimenté (recommandé)

### 1. Installer

1. Double-cliquez sur **`BNP2OFX-Setup.exe`**.
2. Cliquez sur **Oui** / **Suivant** jusqu’à la fin.
3. Un raccourci **BNP2OFX** apparaît sur le Bureau.

Si Windows affiche « Windows a protégé votre PC » :

1. Cliquez sur **Informations complémentaires**
2. Puis **Exécuter quand même**

(L’avertissement est normal : le logiciel n’a pas de certificat payant.)

Variante sans installation : double-cliquez directement sur **`BNP2OFX.exe`**.

### 2. Convertir un dossier de relevés

1. Mettez tous vos PDF de relevés dans un même dossier (des sous-dossiers sont acceptés).
2. Ouvrez **BNP2OFX**.
3. Cliquez sur **Choisir un dossier de relevés…** et sélectionnez ce dossier.
4. Cliquez sur **Convertir**.
5. Les fichiers `.ofx` sont créés dans un sous-dossier **OFX**.
6. Cliquez sur **Ouvrir le dossier des OFX** si besoin.

Vous pouvez aussi convertir **un seul** relevé avec **Choisir un relevé…**.

### 3. Importer

Ouvrez le fichier `.ofx` dans votre logiciel comptable (Sage, Cegid, GnuCash, Actual Budget, etc.).

Le fichier `exemple_releve_bnp.txt` sert de test sans PDF réel.

### 4. Désinstaller

L'installeur habituel copie le programme et les raccourcis, sans l'inscrire dans Windows. **BNP2OFX** n'apparaît donc pas dans Paramètres → Applications et fonctionnalités. Pour le retirer, supprimez le programme et ses raccourcis à la main.

1. Fermez **BNP2OFX** s'il est ouvert.
2. Supprimez le raccourci **BNP2OFX** sur le Bureau, s'il est présent.
3. Appuyez sur **Windows + R**, collez `%LOCALAPPDATA%\BNP2OFX`, puis Entrée.
4. Supprimez ce dossier. Il contient `BNP2OFX.exe`.
5. Pour le raccourci du menu Démarrer : **Windows + R**, collez `%APPDATA%\Microsoft\Windows\Start Menu\Programs`, puis Entrée.
6. Supprimez le fichier `BNP2OFX.lnk`.

Les fichiers `.ofx` déjà produits à côté des relevés restent en place.

Variante sans installation (`BNP2OFX.exe` lancé directement) : supprimez ce fichier. Rien d'autre n'a été copié sur l'ordinateur.

Si l'installeur a été construit avec Inno Setup, le logiciel figure dans Applications et fonctionnalités : utilisez **Désinstaller**. Le dossier d'installation contient alors `unins000.exe`.

## Limites

- Relevés **PDF** téléchargés depuis l’espace client BNP (on peut y sélectionner le texte). Pas les photos ni les scans papier.
- Si BNP change la mise en page, le parseur peut se tromper : lisez l’avertissement « soldes ».
- Compte chèque classique. Les relevés carte / titres / Multi-Choix peuvent différer.

## Ligne de commande (facultatif)

```bat
python bnp2ofx.py
python bnp2ofx.py mon_releve.pdf mon_releve.ofx
python bnp2ofx.py dossier_de_releves
python bnp2ofx.py dossier_de_releves dossier_ofx
```

## Reconstruire l’installeur (développeur)

Sur un PC Windows, dans ce dossier :

```bat
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Sorties dans `dist\` :

| Fichier | Rôle |
|---|---|
| `BNP2OFX.exe` | Logiciel portable (double-clic) |
| `BNP2OFX-Setup.exe` | Installeur (raccourci Bureau + menu Démarrer) |

Si [Inno Setup](https://jrsoftware.org/isinfo.php) est installé, l’installeur officiel est utilisé. Sinon, un installeur de secours est généré automatiquement.

À savoir : l’antivirus peut scanner l’exe au premier lancement. C’est banal pour un exe « maison ».

## Licence

MIT. Le logiciel est gratuit : utilisation, copie et modification sont libres. Le texte complet est dans le fichier `LICENSE`.
