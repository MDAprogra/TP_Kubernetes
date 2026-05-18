# Comparaison k3s vs Kapsule — TP4
**Binôme : Corentin GODON & Matthias DAUVEL**
**Module : Arthur BARADEL — KUBERNETES**
**Date : 18 Mai 2026**

---

## A. Bootstrap et administration

### Q1. Combien de commandes shell ont été nécessaires pour avoir un cluster prêt ?

**k3s (TP1)** — 3 étapes principales, ~5-6 commandes au total :
1. Sur le server : `curl -sfL https://get.k3s.io | sh -`
2. Récupérer le token : `sudo cat /var/lib/rancher/k3s/server/node-token`
3. Sur chaque agent : `curl -sfL https://get.k3s.io | K3S_URL=... K3S_TOKEN=... sh -`

Soit environ 5-6 commandes pour un cluster 3 nœuds opérationnel, sans compter la configuration manuelle du kubeconfig, la correction de l'IP, et l'installation des composants supplémentaires (Traefik, metrics-server...).

**Kapsule (TP4)** — 2 commandes :
1. `scw k8s cluster create name=tp4-cluster-dauvel type=kapsule version=1.32.13 cni=cilium pools.0... pools.1... region=fr-par`
2. `scw k8s kubeconfig install $CLUSTER_ID region=fr-par`

Le cluster Kapsule est livré avec CNI (Cilium), metrics-server, StorageClasses Block Storage et autohealing déjà configurés. Sur k3s, chaque composant supplémentaire nécessite une intervention manuelle.

---

### Q2. Où tournent api-server, scheduler, controller-manager, etcd ?

**k3s** : tous ces composants tournent sur le nœud `GODON-k3s-server`. On peut le constater avec :
```bash
kubectl get pods -n kube-system
```
On y voyait : coredns, traefik, metrics-server, local-path-provisioner — et les composants du control plane intégrés dans le binaire k3s. Ils sont sous la responsabilité de l'utilisateur.

**Kapsule** : le control plane est invisible, géré par Scaleway sur une infrastructure dédiée. La commande :
```bash
kubectl get pods -A | grep -E "apiserver|etcd|scheduler"
```
Ne retourne aucun résultat. Le control plane est accessible uniquement via l'URL `https://cd95bb1a-b06e-43e0-b3f4-6903137b6bc3.api.k8s.fr-par.scw.cloud:6443`.

---

### Q3. Que se passe-t-il si le control plane tombe ?

**k3s** : si on exécute `sudo systemctl stop k3s` sur `GODON-k3s-server`, l'API server n'est plus accessible. Les pods existants continuent de tourner sur les agents mais ne peuvent plus être gérés (aucun scheduling, apply, get possible). On le saurait immédiatement car `kubectl` ne répond plus. Il faut intervenir manuellement en SSH pour redémarrer k3s. Pas de SLA.

**Kapsule** : Scaleway garantit la disponibilité du control plane via son SLA. En cas de redémarrage, les workloads continuent de tourner et Scaleway relance le control plane automatiquement. L'utilisateur ne le saurait probablement pas — c'est transparent. Aucune intervention requise de sa part.

---

## B. Networking et exposition

### Q4. IngressController par défaut et ressources créées par helm install ingress-nginx

**k3s (TP1/TP2)** : Traefik est installé par défaut :
```bash
kubectl get pods -n kube-system | grep traefik
# traefik-9bcdbbd9-jzpk6   1/1   Running
kubectl get svc -n kube-system | grep traefik
# traefik   LoadBalancer   10.43.129.63   ...   80:30832/TCP,443:30945/TCP
```
Traefik utilise un ServiceLB (klipper-lb) — pas un vrai Load Balancer cloud, juste un mécanisme NodePort déguisé.

**Kapsule (TP4)** : aucun IngressController pré-installé. Le `helm install ingress-nginx` a créé :
- Dans le cluster : un Deployment `ingress-nginx-controller`, un Service de type `LoadBalancer`, des RBAC, ConfigMaps, IngressClass `nginx`
- Hors du cluster : un **vrai Load Balancer Scaleway** provisionné automatiquement par le Cloud Controller Manager, avec une IP publique stable (`51.158.58.244`) et des règles de routage TCP vers les nœuds sur les ports 80 et 443, visible dans la console Scaleway → Load Balancers

---

### Q5. Pourquoi Let's Encrypt valide est possible sur Kapsule mais pas sur k3s ?

Let's Encrypt impose que le serveur ACME puisse atteindre le cluster sur le port 80 depuis Internet pour valider le challenge HTTP-01.

**Sur k3s (TP2)** :
- Certificat auto-signé (OpenSSL) car nos VMs n'avaient pas de nom de domaine public
- Le port 80 était accessible via NodePort 30832 — non standard, Let's Encrypt ne peut pas valider sur un port non-standard
- Pas d'IP publique stable associée à un DNS valide

**Sur Kapsule (TP4)** :
- ingress-nginx crée un vrai Load Balancer avec IP publique stable (`51.158.58.244`)
- On peut associer un vrai domaine DNS à cette IP (`tp4.dauvel.mediaschool-rouen.fr`)
- Let's Encrypt peut faire sa validation HTTP-01 sur le port 80 standard ✅
- Certificat obtenu en 26 secondes via cert-manager

---

## C. Stockage

### Q6. Comportement PVC local-path vs scw-bssd quand un nœud tombe

**local-path (k3s, TP2)** :
- Le volume est stocké localement sur le nœud (`/var/lib/rancher/k3s/storage/`)
- Si le nœud `godon-k3s-agent-2` tombe, les données sur ce nœud sont inaccessibles
- Le pod Postgres serait replanifié sur un autre nœud mais sans ses données
- On l'a vu dans le TP2 : `postgres-0` tournait sur `godon-k3s-agent-2` (IP `10.42.2.36`) — si ce nœud disparaît, les données sont perdues

**scw-bssd (Kapsule, TP4)** :
- Le volume est un Block Storage réseau distant, indépendant du nœud
- Si un nœud tombe, Scaleway détache le volume et le rattache au nouveau nœud où le pod est replanifié
- Les données sont préservées ✅
- Démontré en TP : après `kubectl delete pod postgres-0 --force`, le pod a redémarré en 4 secondes avec ses données intactes

Limite : le Block Storage est lié à une AZ. Un volume `fr-par-2` ne peut pas être rattaché à un nœud `fr-par-1`.

---

### Q7. Coût du stockage

**scw-bssd (Kapsule)** : environ **0,10 €/Go/mois** selon la grille Scaleway. Pour notre PVC de 5 Go → ~0,50 €/mois.

**local-path (k3s)** : 0 € supplémentaire — c'est le disque local de la VM déjà payée. Mais avec le risque de perte de données si le nœud tombe définitivement.

---

## D. Lifecycle et autoscaling

### Q8. Ajouter un nœud : k3s vs Kapsule

**k3s** :
1. Provisionner une VM manuellement sur Scaleway
2. SSH sur la VM
3. `curl -sfL https://get.k3s.io | K3S_URL=... K3S_TOKEN=... sh -`
4. Vérifier avec `kubectl get nodes`

C'est manuel, peu reproductible, prend ~5-10 minutes.

**Kapsule** :
```bash
scw k8s pool update <pool-id> size=4 region=fr-par
```
Une seule commande, automatique, ~2-3 minutes. Ou automatiquement via le Cluster Autoscaler sans aucune intervention. Entièrement reproductible et scriptable.

---

### Q9. Autoscaling observé au Bloc 8

**Sur k3s** : le HPA (Horizontal Pod Autoscaler) scale les pods mais pas les nœuds. On n'a pas configuré de Cluster Autoscaler sur k3s — c'est complexe et nécessite un provider cloud compatible. Construire l'équivalent nécessiterait d'intégrer l'API Scaleway manuellement. Pas réaliste sans effort significatif.

**Sur Kapsule** : après `kubectl scale deploy/greedy --replicas=10` avec des pods demandant 1500m CPU chacun, le Cluster Autoscaler a déclenché le scale-up en moins de 30 secondes. 3 nouveaux nœuds DEV1-M ont été provisionnés dans `pool-paris-1` (fr-par-1), passant de 2 à 5 nœuds (max configuré). Les pods `Pending` ont été schedulés dès que les nœuds sont passés en `Ready`. Tout s'est fait sans aucune intervention manuelle.

---

### Q10. Mise à jour Kubernetes 1.32 → 1.33

**k3s** :
```bash
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.33.x sh -
```
C'est manuel — il faut le faire sur chaque nœud, gérer le drain, vérifier la compatibilité des APIs dépréciées, et prévoir une fenêtre de maintenance. Sans automatisation, c'est risqué.

**Kapsule** :
```bash
scw k8s cluster upgrade $CLUSTER_ID version=1.33.x region=fr-par
```
Scaleway gère la mise à jour du control plane en premier, puis les nœuds via un rolling upgrade automatique avec drain. Précautions communes : vérifier les APIs dépréciées, tester en staging d'abord, planifier hors heures de pointe.

---

## E. Sécurité et coût

### Q11. Récupération du kubeconfig et révocation

**k3s (TP1)** :
```bash
sudo cat /etc/rancher/k3s/k3s.yaml
```
C'est un fichier avec un certificat client admin. Si ce fichier fuite, n'importe qui a les droits admin sur le cluster. La révocation est complexe — il faut recréer les certificats du cluster ou modifier les RBAC manuellement.

**Kapsule** :
```bash
scw k8s kubeconfig install $CLUSTER_ID region=fr-par
```
Basé sur les clés API Scaleway IAM. Pour révoquer l'accès, il suffit de supprimer la clé API dans la console IAM — immédiat et sans impact sur le cluster. Beaucoup plus simple et sécurisé en cas de fuite.

---

### Q12. Estimation du coût mensuel de la configuration TP4

| Ressource | Détail | Coût estimé/mois |
|---|---|---|
| 3 × DEV1-M (base) | 0,0198 €/h × 3 × 730h | ~43 € |
| 1 Load Balancer S | ~0,012 €/h × 730h | ~9 € |
| 5 Go Block Storage (scw-bssd) | ~0,10 €/Go × 5 | ~0,50 € |
| Container Registry | Gratuit jusqu'à 75 Go | 0 € |
| **Total estimé** | | **~57 €/mois** |

À comparer avec k3s : notre cluster du TP1 avec 3 × BASIC3-X2C-8G coûtait environ ~25 €/mois, mais sans LB ni stockage managé ni autohealing.

---

### Q13. Incident à 3h du matin

**k3s** : l'utilisateur doit intervenir manuellement. Si `GODON-k3s-server` tombe, l'API server est mort. Il faut se connecter en SSH, diagnostiquer (logs k3s, état etcd, réseau), redémarrer les services, potentiellement restaurer depuis une sauvegarde. Sans monitoring en place, l'incident peut passer inaperçu pendant des heures. Pas de SLA.

**Kapsule** : la responsabilité est partagée. Si c'est le control plane qui est KO → Scaleway intervient (SLA). Si c'est un nœud worker → l'autohealing (`autohealing=true` activé sur nos pools) recrée automatiquement le nœud. L'utilisateur reste responsable de ses workloads (pods, configurations applicatives), mais est déchargé de toute l'infrastructure sous-jacente. C'est le cœur du modèle "managed".

---

## F. Géographie et résilience

### Q14. Ce contre quoi le cluster multi-AZ protège (et ne protège pas)

**Protège contre :**
- Panne d'un datacenter Scaleway Paris (fr-par-1 ou fr-par-2 isolément)
- Maintenance planifiée d'une AZ (pods replanifiés dans l'autre AZ)
- Panne réseau locale à une AZ
- Panne d'un nœud individuel (autohealing + rescheduling)

**Ne protège pas contre :**
- Panne de toute la région fr-par (catastrophe naturelle, panne réseau régionale Paris)
- Attaque DDoS ciblant la région ou l'IP du Load Balancer
- Erreur humaine (suppression accidentelle du cluster ou des données)
- Incident Scaleway global
- Corruption des données Postgres (pas de réplication multi-AZ du volume Block Storage — un volume `fr-par-2` ne peut pas être monté depuis `fr-par-1`)

---

### Q15. Mono-région (multi-AZ) suffit-il ?

**App B2B française, utilisateurs tous en France** : mono-région (multi-AZ) **suffit**. Les utilisateurs sont géographiquement proches, la latence est faible, la résilience datacenter couvre les scénarios les plus probables, et le RGPD est géré nativement.

**App SaaS européenne, utilisateurs Berlin + Madrid + Stockholm** : mono-région **ne suffit pas**. Il faut au minimum deux régions (fr-par + nl-ams par exemple) pour réduire la latence pour les utilisateurs éloignés et garantir la disponibilité en cas de panne régionale.

**Service soumis à HDS (Hébergement de Données de Santé)** : mono-région **ne suffit pas**. HDS impose des exigences strictes sur la localisation des données, la redondance géographique, les sauvegardes et les PRA (Plans de Reprise d'Activité). Il faut une architecture multi-sites certifiée HDS avec réplication des données et procédures de failover documentées.

---

## Synthèse libre

### Q16. Startup 3 devs, zéro SRE — k3s self-hosted ou Kapsule ?

**Kapsule, sans hésiter.**

Avec 3 développeurs et zéro SRE, personne n'a le temps de :
- Gérer les mises à jour Kubernetes manuellement nœud par nœud
- Surveiller la santé du control plane et d'etcd
- Intervenir à 3h du matin si un nœud tombe
- Gérer les certificats et la rotation des secrets

On l'a vu concrètement dans nos TP : k3s demande beaucoup d'interventions manuelles — clés SSH, problèmes de CNI, NetworkPolicies non supportées par Flannel, architecture hétérogène ARM64/amd64 sur les agents qui causait des `ErrImagePull`... Autant de problèmes qui n'existent pas sur Kapsule.

Kapsule absorbe toute cette complexité opérationnelle. Le surcoût (~30 €/mois vs k3s self-hosted) est largement justifié par le temps économisé et la fiabilité gagnée. Une startup doit se concentrer sur son produit, pas sur son infrastructure.

La seule raison de choisir k3s serait un besoin de contrôle total (air-gap, conformité très spécifique, contrainte budgétaire extrême) ou un contexte edge/IoT où le cluster managé n'est pas disponible.