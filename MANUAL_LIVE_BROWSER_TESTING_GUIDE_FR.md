# Guide de test manuel E2E — Cartes de réduction personnalisées

Guide pour tester dans le navigateur le clone **Odoo 17 `odoo17_e2e`**, sans modifier le code. Les parcours peuvent créer des cartes, attributions, promotions, devis et commandes de test dans cette base.

> **Clone uniquement.** Ne pas utiliser ces identifiants ni ces opérations sur la production. Les mots de passe Silver et Caissier ont été réinitialisés dans le clone avec autorisation. Les adresses `@example.test` sont des adresses de test. Une livraison effective des e-mails n'a pas été vérifiée.

## 0. URL, comptes et données de départ

### URL de connexion

- Connexion Odoo : [http://192.168.108.129:8075/web/login?db=odoo17_e2e](http://192.168.108.129:8075/web/login?db=odoo17_e2e)
- POS : [http://192.168.108.129:8075/pos/ui?config_id=1](http://192.168.108.129:8075/pos/ui?config_id=1)
- Portail cartes : [http://192.168.108.129:8075/my/cartes](http://192.168.108.129:8075/my/cartes)
- Boutique : [http://192.168.108.129:8075/shop](http://192.168.108.129:8075/shop)
- Produit de test : [http://192.168.108.129:8075/shop/furn-0269-office-chair-black-17](http://192.168.108.129:8075/shop/furn-0269-office-chair-black-17)
- Panier : [http://192.168.108.129:8075/shop/cart](http://192.168.108.129:8075/shop/cart)

### Comptes du clone

| Rôle / usage | Identifiant | Mot de passe |
|---|---|---|
| Administrateur et responsable Ventes | `admin` | `E2E-Admin-Access-2026` |
| Client portail Gold | `e2e_vip_client` | `E2E-Portal-Verify-2026` |
| Client portail Silver | `e2e_silver_client` | `E2E-Silver-Portal-2026` |
| Caissier POS | `e2e_cashier` | `E2E-Cashier-POS-2026` |

Il n'existe pas de compte séparé de représentant commercial avec les droits nécessaires : utiliser le compte administrateur pour le scénario Ventes.

### Enregistrements déjà présents

Les éléments ci-dessous permettent de commencer par vérifier les données existantes, sans créer de doublons :

| Enregistrement | Valeurs actuelles dans le clone |
|---|---|
| `E2E Bronze` | 5 %, actif, début 20/10/2026, fin 19/11/2027, seuil de dépense 200 USD, programme de fidélité à 15 % |
| `E2E Silver` | 11 %, actif, mêmes dates, conditions de dépense 200 USD et de points 100, sans programme de fidélité lié |
| `E2E Gold` | 20 %, actif et VIP, mêmes dates, seuil de dépense 1 000 USD, programme « 15% on next order » |
| Client Gold | `E2E VIP Assignment Test`, attribution validée, historique POS de 2 610,42 USD |
| Client Silver | `E2E Silver Portal Test`, attribution validée |
| Promotion en cours | `E2E Bronze customer offer`, remise 12 %, liée en réalité à Gold, au programme 15 % et à la liste de prix Benelux |
| Promotion planifiée | `E2E Gold planned promotion`, remise 10 %, du 20/11/2026 au 31/12/2026, liée à Gold, au programme 15 % et à Benelux |
| Session POS | `POS/00005`, configuration `Shop`, ouverte par `e2e_cashier` |
| Règles d'association | 8 règles existent déjà ; une associe « Office Chair Black » à « Desk Stand with Screen » |
| Devis | Le devis brouillon `S00046` existe déjà pour le client Gold |

La devise de la base est USD. Certains noms/descriptions de conditions contiennent encore « DT » : comparer le montant numérique et la devise affichée, et noter cette incohérence.

## 1. Scénario Administrateur et Marketing — Back-office

### 1.1 Connexion et préparation

1. Ouvrir l'URL de connexion et se connecter avec `admin`.
2. Vérifier que le menu **Cartes de Réduction** est visible.
3. Pour éviter de modifier les données utilisées par les scénarios portail et POS, ne pas éditer directement les fiches `E2E ...`. Pour les essais de création/modification, créer des enregistrements temporaires nommés `MANUEL - ...`.

### 1.2 Consulter et tester les tiers de cartes

1. Ouvrir **Cartes de Réduction > Gestion des Cartes > Lister les cartes disponibles**.
2. Ouvrir successivement `E2E Bronze`, `E2E Silver` et `E2E Gold`.
3. Vérifier le taux, les dates de validité, le statut actif, le champ VIP sur Gold et le programme de fidélité lié.
4. Pour tester la création, ouvrir **Cartes de Réduction > Gestion des Cartes > Créer une nouvelle carte**, puis créer par exemple `MANUEL - Gold` :
   - taux de remise et dates de début/expiration ;
   - cocher VIP uniquement si le test doit déclencher l'attribution VIP ;
   - renseigner le programme de fidélité si nécessaire.
5. Enregistrer, rouvrir la fiche et vérifier que les valeurs sont conservées.
6. Modifier le taux ou une date sur la fiche temporaire, enregistrer, puis vérifier la modification dans la liste. Une modification de dates de la carte ne réécrit pas les dates des attributions déjà créées.

### 1.3 Conditions d'éligibilité numériques

1. Ouvrir **Cartes de Réduction > Gestion des Cartes > Conditions d'obtention**.
2. Examiner les conditions liées à chaque carte E2E.
3. Cliquer sur **Créer** pour ajouter une condition temporaire, la relier à `MANUEL - Gold` et saisir `min_amount` et/ou `min_points`.
4. Enregistrer, rouvrir la condition et vérifier les valeurs et la carte liée.
5. Pour vérifier la logique, utiliser deux essais distincts : une condition « dépense minimale » et une condition « points minimum ». Dans une même condition, les seuils renseignés doivent tous deux être atteints ; plusieurs conditions actives d'une carte fonctionnent comme des possibilités alternatives.
6. Les seuils de dépense sont comparés aux achats enregistrés ; un devis brouillon ne constitue pas un achat payé/confirmé.
7. Le formulaire de condition ne propose pas de période mensuelle dans les champs numériques vérifiés. Ne qualifier le seuil de « mensuel » que si une période est effectivement affichée/configurée dans l'interface.

### 1.4 Attribution d'une carte et notification VIP

1. Ouvrir **Cartes de Réduction > Gestion des Cartes > Attribuer une carte à un client**.
2. Créer une attribution temporaire pour un client de test. Choisir la carte, le client et vérifier les dates proposées automatiquement à partir de la carte.
3. Enregistrer, puis cliquer sur **Attribuer** si le formulaire ne l'a pas déjà fait. L'attribution doit passer à l'état validé ; l'état technique correspondant est `validated`.
4. Pour tester la notification VIP sans toucher à l'attribution existante du client Gold, relier une carte temporaire marquée VIP à un client temporaire.
5. Ouvrir **Cartes de Réduction > Notifications et Alertes > Alerte sur les cartes inactives**. Rechercher la nouvelle notification dont le nom commence par « Carte VIP » et vérifier le client et la date.
6. Le menu **Notifications clients** est filtré sur les notifications d'e-mail ; la notification VIP peut donc être plus facile à retrouver dans **Alerte sur les cartes inactives**. La présence d'un enregistrement ne garantit pas la livraison d'un e-mail.
7. Depuis la fiche de la carte, tester la case d'activation/désactivation. Vérifier que la carte est marquée inactive puis active, sans supprimer l'attribution.
8. Une notification VIP de l'attribution Gold E2E existe déjà. Pour tester un nouvel envoi, utiliser une carte VIP temporaire et un client temporaire afin de distinguer le nouveau résultat.

### 1.5 Expiration et assistant de renouvellement

Il n'existe actuellement aucune attribution expirée de test. Ne pas modifier l'attribution Gold ou Silver, utilisée dans les autres scénarios.

1. Créer une attribution pour un client/carte temporaires comme ci-dessus.
2. Régler la date d'attribution au **20/10/2026** et la date d'expiration au **18/11/2026** (la veille de la date de test, 19/11/2026). La date d'expiration doit rester postérieure à la date d'attribution.
3. Vérifier que l'attribution est validée.
4. Ouvrir **Paramètres > Technique > Automatisation > Actions planifiées**. Rechercher l'action de vérification des cartes expirées, l'ouvrir et cliquer **Exécuter manuellement**.
5. Revenir à **Cartes de Réduction > Gestion des Cartes** et ouvrir la liste des cartes/attributions expirées. L'attribution temporaire doit apparaître expirée. Vérifier aussi la notification dans **Notifications et Alertes > Alerte sur les cartes inactives**.
6. Depuis la ligne expirée, cliquer **Renouveler** pour ouvrir l'assistant de renouvellement. Vérifier le client, la carte et la date ; saisir une nouvelle expiration future (par exemple 19/11/2027), puis cliquer **Confirmer**.
7. **Point de contrôle important :** l'assistant peut ouvrir avec le client ou la carte vide, même si l'action a été lancée depuis une attribution. Si un champ requis reste vide ou empêche la confirmation, noter l'étape et le message comme échec ; ne pas contourner en modifiant la base directement.
8. Si le renouvellement aboutit, vérifier que l'attribution n'est plus dans la liste expirée et qu'elle a l'état `renouvelé`/`renouvelé(e)` affiché par l'interface.

### 1.6 Promotions, programme et liste de prix

1. Ouvrir **Cartes de Réduction > Gestion des Promo > Gérer les promotions personnalisées**.
2. Ouvrir `E2E Bronze customer offer` et vérifier la carte, le taux de 12 %, le programme, la liste Benelux et les dates. Son nom est trompeur : elle cible actuellement Gold, pas Bronze.
3. Ouvrir `E2E Gold planned promotion` et vérifier qu'elle est planifiée à partir du 20/11/2026.
4. Pour un nouvel essai, cliquer **Créer** et renseigner le nom, le taux, la carte, le programme de fidélité, la liste de prix et les dates ; enregistrer.
5. Vérifier les filtres dédiés :
   - **Cartes de Réduction > Gestion des Promo > Voir les promotions en cours** ;
   - **Cartes de Réduction > Gestion des Promo > Planifier des promotions futures**.
6. La valeur de l'état est déduite des dates. Le lien vers un programme ou une liste de prix est enregistrable et consultable, mais ne pas supposer que modifier le taux de la promotion met automatiquement à jour les règles de prix : ce comportement n'est pas garanti par le formulaire de promotion.
7. **Cartes de Réduction > Gestion des Promo > Ajouter une promotion** ouvre les programmes de fidélité standards (`loyalty.program`), et non le formulaire de promotion personnalisée.
8. Pour inspecter les règles de prix, aller à **Ventes > Produits > Listes de prix**, ouvrir **Benelux** et examiner ses règles. Le checkout Gold applique actuellement le programme lié à 15 %, alors que le taux du tier Gold est 20 % : vérifier et consigner ces deux valeurs séparément.

### 1.7 Règles d'association et analyses client

#### Générer les règles d'association

1. Ouvrir **Cartes de Réduction > Fidélité Clients > generer les regles**.
2. Cliquer **Générer les règles** et noter le message/compte de règles.
3. Vérifier les résultats dans **Cartes de Réduction > Gestion des Promo > Associations de Produits**. Le jeu actuel contient 8 règles, dont Office Chair Black → Desk Stand with Screen.
4. Le générateur utilise l'historique des ventes POS payées des 365 derniers jours. Il peut remplacer les anciennes règles ; ne le lancer que sur le clone.

#### Comportement et CLV

1. Ouvrir **Cartes de Réduction > Notifications et Alertes > comportement d'achat client**.
2. Ouvrir le client **E2E VIP Assignment Test** puis l'onglet **Analyse Comportementale**.
3. Cliquer **Recalculer**. Vérifier les mesures basées sur l'historique POS et les valeurs de comportement/CLV. Les valeurs actuelles de référence pour Gold sont environ 2 610,42 USD de ventes, dernier achat le 19/11/2026, fréquence de 30 jours et CLV prédit de 6 526,05 USD.
4. Le bouton **Tester la prédiction** peut lancer l'entraînement et écrire un fichier dans le répertoire du module monté en lecture seule dans ce clone. Ne pas l'utiliser comme test standard ; s'il échoue, noter le message et ne pas modifier les permissions ni le code.
5. Ouvrir **Cartes de Réduction > Fidélité Clients > Identifier les clients VIP** pour consulter la liste des clients marqués VIP et leurs données.

### 1.8 Rapports et tableaux de bord

1. **Cartes de Réduction > Rapports et Analyses > Statistique des cartes** : examiner les statistiques d'utilisation des cartes et les vues graphiques disponibles.
2. Ouvrir **Tableau de Bord Ventes > Top Produits** puis **Top Produits (Global)**.
3. Ouvrir **Tableau de Bord Ventes > Quantité des Produits par Client** et vérifier les résultats par client.
4. Revenir à **Cartes de Réduction > Notifications et Alertes > comportement d'achat client** pour la vue comportement/CLV.
5. Pour le rapport PDF des cartes, ouvrir une fiche carte puis **Imprimer > Rapport des Cartes**.
6. Le menu **Rapports et Analyses > Exporter les données** est actuellement relié au tableau des ventes globales, pas à une action d'export. Le modèle `rapports.usage` ne fournit pas de rapport opérationnel à vérifier.

## 2. Scénario Caissier — Point de Vente

### 2.1 Ouvrir la session existante

1. Se connecter avec `e2e_cashier` sur l'URL de connexion.
2. Ouvrir [le POS Shop](http://192.168.108.129:8075/pos/ui?config_id=1).
3. La session `POS/00005` est déjà ouverte pour ce caissier. La réutiliser ; ne pas ouvrir une deuxième session si Odoo indique qu'une session existe déjà.
4. Vérifier que l'écran des produits s'ouvre et qu'une nouvelle commande vide est sélectionnée.

### 2.2 Sélectionner le client et vérifier la carte

1. Cliquer **Review** (sur une commande vide, l'écran indique `Review 0 items`).
2. Dans l'écran de commande, cliquer **Customer**.
3. Dans **Search Customers...**, saisir `E2E VIP Assignment Test`, puis sélectionner cette ligne.
4. Revenir à l'écran des produits avec la flèche **Go Back**.
5. Ajouter **Office Chair Black**. Si le produit n'est pas immédiatement visible, utiliser la recherche de produits ou la catégorie Furniture.
6. À l'ajout du produit, une suggestion de produit peut s'afficher : **Desk Stand with Screen**. Accepter pour l'ajouter au panier, ou refuser pour poursuivre sans cet article.
7. Cliquer le bouton POS **cartes reduction**. Vérifier le panneau **Meilleure carte pour cette commande**, puis tester **Voir toutes les cartes** et **Fermer**.
8. Le programme Gold lié applique actuellement une récompense de 15 %, tandis que le tier affiche 20 %. Il n'y a pas de sélecteur manuel distinct de programme : l'application est automatique. Noter le taux affiché dans le détail de carte et le taux effectivement appliqué à la ligne/au total.
9. Pour comparer le client Silver, répéter avec `E2E Silver Portal Test`. Silver n'a pas de programme de fidélité lié ; ne pas s'attendre automatiquement au programme Gold de 15 %.

### 2.3 Valider une commande de test

1. Vérifier le client, les lignes, la suggestion acceptée/refusée, le rabais et le total.
2. Cliquer **Pay** / **Payment**.
3. Choisir le mode de paiement **Cash**.
4. Saisir le montant exact si l'écran le demande, puis cliquer **Validate**.
5. Vérifier le reçu et la référence de commande. Cette validation crée une vraie commande POS dans le clone ; ne pas effectuer le paiement pour un test qui doit rester sans écriture.

**Limite d'affichage :** le panneau POS peut afficher `DTN` comme devise alors que les montants de la base et des autres écrans sont en USD. Consigner ce défaut d'étiquette séparément du calcul.

## 3. Scénario Représentant Commercial — Ventes

Utiliser le compte `admin` (il n'existe pas de compte représentant séparé avec les droits Ventes).

1. Ouvrir **Ventes > Commandes > Devis**.
2. Cliquer **Nouveau**.
3. Dans **Client**, rechercher et sélectionner `E2E VIP Assignment Test`.
4. Dans les lignes, cliquer **Ajouter un produit**, rechercher **Office Chair Black** et l'ajouter.
5. Enregistrer le devis et vérifier la ligne. Le devis E2E existant `S00046` fournit une valeur de référence : prix avant remise 120,50 USD, remise de programme 15 %, sous-total hors taxe 102,43 USD, taxe 15,36 USD, total 117,79 USD.
6. La remise attendue est celle du programme lié (15 %), pas nécessairement les 20 % affichés sur le tier Gold.
7. Pour tester les recommandations, cliquer **Confirmer** sur le devis. La suggestion associée à Office Chair Black peut ouvrir un assistant avec **Ajouter** et **Ignorer** :
   - **Ajouter** ajoute Desk Stand with Screen au devis ;
   - **Ignorer** ferme la suggestion sans ajouter l'article.
8. Le premier clic sur **Confirmer** peut être intercepté par l'assistant ; le devis reste alors un devis. Après avoir traité la suggestion, vérifier les lignes et les montants. Cliquer une seconde fois sur **Confirmer** uniquement si l'objectif est de transformer le devis en commande de vente.
9. La recommandation intervient à la confirmation, pas au moment où le produit est ajouté. Ne pas envoyer le devis au client pendant ce test.

## 4. Scénario Client — Portail et boutique

### 4.1 Client Gold : portail et promotions

1. Se déconnecter du compte précédent avec le menu utilisateur, puis se connecter avec `e2e_vip_client`.
2. Ouvrir [Mes cartes](http://192.168.108.129:8075/my/cartes).
3. Vérifier la carte active **E2E Gold**, le taux du tier de 20 % et l'expiration au 19/11/2027.
4. Vérifier les promotions présentées. La promotion active peut porter le nom `E2E Bronze customer offer` alors qu'elle cible Gold ; c'est l'enregistrement existant.
5. Gold étant le tier le plus élevé actuellement configuré, noter si une condition de tier supérieur est absente.

### 4.2 Client Silver : portail et condition de progression

1. Se déconnecter puis se connecter avec `e2e_silver_client`.
2. Ouvrir [Mes cartes](http://192.168.108.129:8075/my/cartes).
3. Vérifier **E2E Silver**, le taux de 11 % et la date d'expiration au 19/11/2027.
4. Vérifier les conditions requises pour le tier suivant, notamment la condition Gold de 1 000 USD si elle est présentée.
5. Le portail présente les seuils requis ; il ne faut pas interpréter cet affichage comme une progression en temps réel du montant déjà dépensé.

### 4.3 Boutique web et panier

Effectuer d'abord le test Gold, puis vider le panier avant de recommencer avec Silver.

1. En étant connecté au client choisi, ouvrir [la boutique](http://192.168.108.129:8075/shop).
2. Ouvrir la fiche [Office Chair Black](http://192.168.108.129:8075/shop/furn-0269-office-chair-black-17).
3. Cliquer **Add to cart** puis ouvrir [le panier](http://192.168.108.129:8075/shop/cart).
4. Vérifier les lignes, le rabais et le total. Pour Gold, le test connu applique la récompense du programme liée de 15 % : sur une ligne à 120,50 USD, le sous-total remisé de référence est 102,43 USD, avant taxe.
5. Une invite de recommandation peut proposer **Desk Stand with Screen**. Accepter pour ajouter l'article ou annuler pour le refuser. Sur la boutique, l'invite peut ne présenter que le produit suggéré si la règle n'est pas liée à une récompense.
6. Vider le panier avant le test Silver. Comme Silver n'a pas de programme lié, comparer le résultat réellement affiché sans supposer que le rabais Gold de 15 % s'applique.
7. Ne pas finaliser le paiement du panier : cela créerait une commande e-commerce réelle dans la base de test.

## 5. Résultat à consigner

Pour chaque vérification, noter **Réussi**, **Échoué** ou **Bloqué**, le nom de l'utilisateur, l'enregistrement concerné et le message/écran observé. Capturer l'écran des échecs, notamment :

- assistant de renouvellement vide ou impossible à confirmer ;
- différence entre taux de carte et taux réellement appliqué ;
- règle de prix Benelux non mise à jour par une promotion ;
- notification enregistrée mais e-mail non livré ;
- étiquette `DTN` dans le POS au lieu de USD ;
- bouton d'export ouvrant un tableau de ventes ;
- erreur du bouton **Tester la prédiction** ;
- écart entre les données du portail et les conditions du tier suivant.

Après avoir terminé les scénarios avec les données E2E d'origine, une vérification complémentaire de propagation peut être faite avec un tier et une condition `MANUEL - ...` : attribuer ce tier au client Silver, actualiser `/my/cartes`, modifier le taux/le seuil dans le back-office puis actualiser le portail. Vérifier si les nouvelles valeurs apparaissent. Désactiver ensuite le tier temporaire pour ne pas le confondre avec les données E2E.

## Références techniques consultées

Les menus et formulaires correspondent aux vues du module, notamment [views/base_menu.xml](./views/base_menu.xml), [views/cartes_view.xml](./views/cartes_view.xml), [views/promotions.xml](./views/promotions.xml), [views/portal_templates.xml](./views/portal_templates.xml), les vues du tableau de bord sous `views/`, et les routes portail/boutique de [controllers/portal.py](./controllers/portal.py) et [controllers/website_sale.py](./controllers/website_sale.py). Le parcours POS est fourni par les composants du module sous `static/src/`.
