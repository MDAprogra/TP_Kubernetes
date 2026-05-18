# TP 4 — Cluster managé Scaleway Kapsule

> Master 1 Informatique — Module Orchestration de conteneurs
> Prérequis : TP1 + TP2 validés. TP3 utile mais pas indispensable.
> Compte Scaleway actif (carte bancaire obligatoire à la création)

## Thématique fil rouge

**« Du cluster artisanal au cluster managé : ce que le cloud fait pour vous »**

À la fin du TP, le livre d'or des TP précédents tourne sur un cluster Kubernetes managé Scaleway Kapsule réparti sur deux zones de disponibilité, exposé en HTTPS avec un certificat Let's Encrypt valide, les images servies depuis le Container Registry Scaleway, avec autoscaling des nœuds et stockage persistant Block Storage. L'objectif principal est de **comparer concrètement** un cluster auto-géré (k3s du TP1) et un cluster managé (Kapsule).

## Objectifs spécifiques

1. Provisionner un cluster Kubernetes managé multi-AZ via la CLI Scaleway
2. Identifier les composants managés par le cloud provider vs ceux gérés par l'utilisateur
3. Utiliser les services cloud intégrés : Container Registry, Load Balancer, Block Storage
4. Mettre en place un vrai TLS public (cert-manager + Let's Encrypt)
5. Démontrer l'autoscaling automatique des nœuds sous charge
6. Évaluer le coût, les responsabilités et les compromis d'un service managé
7. Détruire proprement toutes les ressources pour éviter toute facturation

## Plan de la séance

1. Briefing : qu'est-ce qu'un cluster managé
2. Bloc 1 — Setup Scaleway
3. Bloc 2 — Création du cluster Kapsule multi-AZ
4. Bloc 3 — Premier contact, exploration
5. Bloc 4 — Container Registry Scaleway
6. Bloc 5 — Migration du livre d'or
7. Bloc 6 — LoadBalancer + Ingress + cert-manager + Let's Encrypt
8. Bloc 7 — Stockage persistant Block Storage
9. Bloc 8 — Cluster autoscaler en action
10. Bloc 9 — Comparatif multi-AZ vs multi-région (théorique)
11. Bloc 10 — Questionnaire de comparaison
12. Bloc 11 — Destruction obligatoire

> ⚠️ **Lecture obligatoire avant de commencer** : les ressources créées sur Scaleway sont facturées à la durée. Un cluster oublié coûte de l'argent réel. Le Bloc 11 n'est pas optionnel.

---

## Briefing initial

Au tableau, comparaison à dessiner :

| Couche | k3s (TP1) | Kapsule (TP4) |
|---|---|---|
| Machines | VMs du labo | Instances Scaleway |
| Installation kubelet | `curl get.k3s.io \| sh` manuel | Automatique |
| Control plane | Tourne sur `k3s-server` (vous) | Géré par Scaleway, invisible |
| etcd / SQLite | Sur le server (à sauvegarder) | Redondé et sauvegardé par Scaleway |
| CNI | Flannel + kube-router | Cilium (au choix : Calico, Weave, Flannel) |
| LoadBalancer Service | Bricolé (ServiceLB / NodePort) | Vrai Load Balancer Scaleway facturé |
| StorageClass | `local-path` (disque local) | `scw-bssd` (Block Storage distant) |
| Coût | Coût des VMs | Compute + LB + Block Storage |

Idée centrale : on délègue la complexité du control plane en échange d'un coût récurrent et d'une certaine perte de contrôle.

---

## Bloc 1 — Setup Scaleway

### Étape 1.1 — Compte et projet

Sur https://console.scaleway.com :
1. Créer un compte (carte bancaire requise).
2. `Organization → Projects` : créer `tp4-k8s-<binôme>`.
3. `IAM → API Keys` : créer une clé API liée au projet. Noter Access Key et Secret Key (affichée une seule fois).

### Étape 1.2 — CLI

```bash
# macOS
brew install scw
# Linux
curl -fsSL https://raw.githubusercontent.com/scaleway/scaleway-cli/master/scripts/get.sh | sh

scw init
# Région : fr-par, Zone : fr-par-1, clés API, project ID
scw info
```

**Checkpoint 1** ✅ `scw info` affiche l'organisation et le projet attendus.

---

## Bloc 2 — Création du cluster Kapsule multi-AZ

### Étape 2.1 — Lister les options

```bash
scw k8s version list region=fr-par
scw instance server-type list zone=fr-par-1 | grep -E "DEV1|GP1"
```

`DEV1-M` (3 vCPU, 4 Go RAM, ~0.02€/h) est l'instance la moins chère éligible.

### Étape 2.2 — Création multi-AZ

Un seul cluster (un seul control plane), deux pools dans deux AZ différentes pour la résilience datacenter :

```bash
scw k8s cluster create \
  name=tp4-cluster \
  type=kapsule \
  version=1.32.3 \
  cni=cilium \
  pools.0.name=pool-paris-1 \
  pools.0.node-type=DEV1-M \
  pools.0.size=2 \
  pools.0.zone=fr-par-1 \
  pools.0.autohealing=true \
  pools.1.name=pool-paris-2 \
  pools.1.node-type=DEV1-M \
  pools.1.size=1 \
  pools.1.zone=fr-par-2 \
  pools.1.autohealing=true \
  region=fr-par

export CLUSTER_ID=<id-retourné>
scw k8s cluster wait $CLUSTER_ID region=fr-par
```

### Étape 2.3 — Kubeconfig

```bash
scw k8s kubeconfig install $CLUSTER_ID region=fr-par
kubectl config use-context <le-contexte-kapsule>
kubectl get nodes -o wide --show-labels | grep topology.kubernetes.io/zone
```

Constater le label `topology.kubernetes.io/zone=fr-par-1` ou `fr-par-2` sur chaque nœud.

**Checkpoint 2** ✅ 3 nœuds Ready, répartis sur 2 AZ.

### Pièges bloc 2

- **`UnauthorizedException`** : clé API hors projet ou Secret Key tronquée.
- **`creating` long** : capacité épuisée, essayer `pools.1.zone=fr-par-3`.

---

## Bloc 3 — Premier contact

Exploration rapide pour répondre aux questions du Bloc 10.

```bash
kubectl get nodes -o wide
kubectl get pods -A
kubectl get sc
kubectl cluster-info
kubectl get pods -A | grep -E "apiserver|etcd|scheduler"
```

Noter dans `notes-exploration.md` :
- Quels pods système sont là sur Kapsule mais pas sur k3s (et vice versa) ?
- Y a-t-il un IngressController pré-installé ?
- Combien de StorageClasses ?
- Que voit-on (ou ne voit-on pas) du control plane ?

Côté console Scaleway → Kubernetes → tp4-cluster, explorer les onglets Pools, Nodes, Easy Deploy.

**Checkpoint 3** ✅ Au moins 3 différences observables identifiées.

---

## Bloc 4 — Container Registry Scaleway

On bascule de Docker Hub vers le registry intégré à Scaleway.

### Étape 4.1 — Namespace SCR

```bash
scw registry namespace create \
  name=tp4-<binôme> \
  region=fr-par \
  is-public=false

export SCR_ENDPOINT=rg.fr-par.scw.cloud/tp4-<binôme>
```

### Étape 4.2 — Push des images

```bash
echo "$SCW_SECRET_KEY" | docker login rg.fr-par.scw.cloud -u nologin --password-stdin

docker tag docker.io/<login>/webapp-backend:v2.0 $SCR_ENDPOINT/webapp-backend:v2.0
docker push $SCR_ENDPOINT/webapp-backend:v2.0

docker tag docker.io/<login>/webapp-frontend:v1.0 $SCR_ENDPOINT/webapp-frontend:v1.0
docker push $SCR_ENDPOINT/webapp-frontend:v1.0
```

Vérifier dans la console Scaleway → Container Registry.

### Étape 4.3 — imagePullSecret

```bash
kubectl create namespace guestbook
kubectl config set-context --current --namespace=guestbook

kubectl create secret docker-registry scw-registry-credentials \
  --docker-server=rg.fr-par.scw.cloud \
  --docker-username=nologin \
  --docker-password=$SCW_SECRET_KEY
```

**Checkpoint 4** ✅ Images visibles dans la console SCR, secret créé.

---

## Bloc 5 — Migration du livre d'or

Récupérer les manifests TP2 dans `tp4/manifests/` et appliquer ces trois changements :

**1.** Remplacer les images : `docker.io/<login>/...` → `rg.fr-par.scw.cloud/tp4-<binôme>/...`

**2.** Ajouter dans chaque Deployment spec.template.spec :
```yaml
imagePullSecrets:
- name: scw-registry-credentials
```

**3.** Dans le StatefulSet Postgres, changer la `storageClassName` :
```yaml
volumeClaimTemplates:
- metadata:
    name: data
  spec:
    accessModes: ["ReadWriteOnce"]
    storageClassName: scw-bssd
    resources:
      requests:
        storage: 5Gi
```

Appliquer :

```bash
kubectl apply -f tp4/manifests/
kubectl get pods,pvc,svc
```

**Checkpoint 5** ✅ Tous les pods Running, PVC `data-postgres-0` Bound sur `scw-bssd`.

### Pièges bloc 5

- **`ImagePullBackOff`** : `imagePullSecrets` oublié, ou secret dans le mauvais namespace.
- **PVC pending** : Block Storage met 30s-2min à provisionner. Patience.

---

## Bloc 6 — LoadBalancer + Ingress + cert-manager + Let's Encrypt

C'est le moment fort : un vrai LB cloud + une IP publique stable = certificat Let's Encrypt **valide** (impossible en TP2 sur k3s).

### Étape 6.1 — ingress-nginx via Helm

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace \
  --set controller.service.type=LoadBalancer

kubectl get svc -n ingress-nginx ingress-nginx-controller -w
# Attendre l'EXTERNAL-IP (1-2 min)
```

```bash
export LB_IP=$(kubectl get svc -n ingress-nginx ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "Load Balancer IP : $LB_IP"
```

Vérifier dans la console Scaleway → Load Balancers qu'une instance LB a été créée.

### Étape 6.2 — DNS

Pointer un domaine vers `$LB_IP`. Trois options :
- **Recommandée** : domaine personnel (OVH, Gandi, Cloudflare…), enregistrement A `tp4.votredomaine.fr → $LB_IP`
- **Alternative gratuite** : https://www.duckdns.org
- **Sans domaine** : sauter le TLS Let's Encrypt, rester sur `/etc/hosts` + auto-signé comme en TP2

Pour la suite, on suppose `tp4.example.com` pointe vers `$LB_IP`.

```bash
dig +short tp4.example.com   # doit retourner $LB_IP
```

### Étape 6.3 — cert-manager

```bash
helm repo add jetstack https://charts.jetstack.io
helm repo update

helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --version v1.16.1 \
  --set crds.enabled=true
```

### Étape 6.4 — ClusterIssuer + Ingress avec TLS

**`60-clusterissuer.yaml`** (utiliser `staging` d'abord pour éviter les rate-limits) :

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: <votre-email@example.com>
    privateKeySecretRef:
      name: letsencrypt-prod-key
    solvers:
    - http01:
        ingress:
          class: nginx
```

**`61-ingress.yaml`** :

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: webapp-ingress
  namespace: guestbook
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - tp4.example.com
    secretName: webapp-tls
  rules:
  - host: tp4.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: frontend-svc
            port:
              number: 80
```

```bash
kubectl apply -f 60-clusterissuer.yaml
kubectl apply -f 61-ingress.yaml
kubectl get certificate -n guestbook -w
```

Observer le `Status` du Certificate : `Issuing` → `Ready` (peut prendre 1-3 min).

```bash
curl https://tp4.example.com/
# Cadenas vert dans le navigateur, sans warning
```

**Checkpoint 6** ✅ Livre d'or accessible en HTTPS avec certificat valide.

### Pièges bloc 6

- **Certificate stuck en Issuing** : DNS mal propagé, ou Let's Encrypt n'arrive pas à atteindre le cluster. `kubectl describe challenge`.
- **Rate limit Let's Encrypt** : tester avec `staging` d'abord (`https://acme-staging-v02.api.letsencrypt.org/directory`).

---

## Bloc 7 — Stockage persistant Block Storage

### Étape 7.1 — Inspecter

```bash
kubectl get sc
kubectl describe sc scw-bssd
```

Comparaison clé avec `local-path` (TP2) :

| Aspect | `local-path` (k3s) | `scw-bssd` (Kapsule) |
|---|---|---|
| Type | Fichier local sur le nœud | Volume distant réseau |
| Si nœud disparaît | Données perdues | Volume rattachable à un autre nœud |
| Multi-AZ | Coincé sur le nœud | Coincé sur l'AZ du volume |
| Snapshots | Non | Oui |
| Coût | 0 € | Au Go-mois |

### Étape 7.2 — Démontrer la portabilité

```bash
kubectl get pod postgres-0 -o wide
kubectl delete pod postgres-0 --force --grace-period=0
kubectl get pod postgres-0 -w
```

Si le pod est replanifié sur un autre nœud **de la même AZ**, Scaleway détache et rattache le Block Storage. C'est exactement ce que `local-path` ne sait pas faire.

> Limite : un Block Storage est lié à une AZ. Si tous les nœuds de `fr-par-1` tombent, le pod postgres ne pourra pas être replanifié dans `fr-par-2`. Solutions : réplication applicative (Patroni, Postgres logical replication).

**Checkpoint 7** ✅ Volume qui suit le pod sur un autre nœud démontré.

---

## Bloc 8 — Cluster autoscaler en action

### Étape 8.1 — Activer l'autoscaling

```bash
scw k8s pool list cluster-id=$CLUSTER_ID region=fr-par
export POOL_ID=<id-du-pool-paris-1>

scw k8s pool update $POOL_ID region=fr-par \
  autoscaling=true \
  min-size=2 \
  max-size=5
```

### Étape 8.2 — Charge gourmande

**`80-greedy.yaml`** :

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: greedy
  namespace: guestbook
spec:
  replicas: 1
  selector:
    matchLabels: { app: greedy }
  template:
    metadata:
      labels: { app: greedy }
    spec:
      containers:
      - name: stress
        image: progrium/stress
        args: ["--cpu", "2", "--timeout", "600s"]
        resources:
          requests: { cpu: "1500m", memory: "256Mi" }
          limits:   { cpu: "2000m", memory: "512Mi" }
```

```bash
kubectl apply -f 80-greedy.yaml
kubectl scale deploy/greedy --replicas=10
```

### Étape 8.3 — Observer le scale-up

Trois terminaux :

```bash
# Pods en attente
watch -n2 'kubectl get pods -n guestbook -l app=greedy'

# Nœuds qui apparaissent
watch -n5 'kubectl get nodes'

# Events autoscaler
kubectl get events -n kube-system --field-selector source=cluster-autoscaler -w
```

Attendu en 2-4 min :
1. Pods en `Pending` avec `FailedScheduling`
2. Autoscaler déclenche création de nœud
3. Nouveau serveur DEV1-M provisionné côté Scaleway
4. Nœud `Ready`, pods schedulés
5. Jusqu'à `max-size=5`

### Étape 8.4 — Scale-down

```bash
kubectl delete -f 80-greedy.yaml
```

L'autoscaler attend ~10 min de sous-utilisation puis supprime les nœuds excédentaires. Long, mais à constater au moins partiellement.

**Checkpoint 8** ✅ Scale-up observé, nouveaux nœuds visibles dans la console.

### Pièges bloc 8

- **Pas de scale-up** : `requests.cpu` trop basse.
- **Coût qui dérive** : oublier de retirer la charge → cluster qui grossit et facture. Surveiller.

---

## Bloc 9 — Multi-AZ vs multi-région (théorique)

> Pas de manipulation dans ce bloc. Lecture et discussion uniquement.

Votre cluster est multi-AZ (`fr-par-1` + `fr-par-2`). Vous résistez à la panne d'une AZ Paris. Mais que faire si **tout Paris** est down (catastrophe naturelle, panne réseau régionale) ?

### Limite : Kapsule est mono-région

Un cluster Kapsule est **lié à une seule région**. Vous ne pouvez pas avoir un seul cluster avec des nœuds Paris ET Amsterdam. Pour couvrir deux régions, deux options :

1. **Deux clusters Kapsule indépendants**, un par région
2. **Kosmos** (offre cousine de Scaleway), avec un control plane unique qui accepte des nœuds de plusieurs régions et même d'autres fournisseurs cloud (AWS, GCP, Azure, on-premise)

### Tableau comparatif

| Aspect | Multi-AZ (votre cluster) | Multi-région (théorique, à 2 clusters Kapsule) |
|---|---|---|
| Nombre de clusters | 1 | 2 |
| Nombre de control planes | 1 | 2 |
| Coût | x1 | x2 environ |
| Failover en cas de panne | Automatique (scheduler k8s) | Manuel ou via outils externes |
| Réplication de la base de données | Locale au cluster | À configurer explicitement (Postgres logical replication, etc.) |
| Cohérence des manifests | Un `kubectl apply` | Deux contextes kubectl, ou GitOps multi-cluster |
| Latence pour l'utilisateur | Région unique | Possibilité de router au plus proche |
| Cas d'usage typique | Résilience datacenter | Souveraineté, RGPD, proximité utilisateurs |

### Outils permettant le multi-cluster

À mentionner culturellement :

- **Argo CD multi-cluster** : un Argo CD pilote N clusters depuis un dépôt Git unique
- **Cilium ClusterMesh** : maille les Services et Pods entre clusters, routing transparent
- **DNS failover** : Cloudflare ou Route53 avec health checks pour basculer le trafic
- **Service mesh multi-cluster** : Istio, Linkerd
- **Scaleway Kosmos** : la réponse Scaleway au besoin multi-région avec un seul control plane

### Discussion guidée

Pendant 10-15 min, par binôme, réfléchir à :

- Si Paris tombe entièrement, quel est l'impact sur votre livre d'or actuel ?
- Pour passer en multi-région, quels changements minimaux dans votre déploiement ?
- Que coûte en plus une approche multi-région (en argent et en complexité) ?

Les réponses argumentées vont dans les questions du Bloc 10.

**Checkpoint 9** ✅ Compréhension claire de ce que multi-AZ couvre, et de ce qu'il ne couvre pas.

---

## Bloc 10 — Questionnaire de comparaison

À traiter en binôme, à rendre dans `tp4/comparaison.md`. Chaque réponse doit être étayée par une commande, un screenshot, ou un extrait de manifest.

### A. Bootstrap et administration

**Q1.** Combien de commandes shell ont été nécessaires pour avoir un cluster prêt sur k3s (TP1) ? Sur Kapsule (TP4) ? Détaillez.

**Q2.** Sur k3s, où tournent api-server, scheduler, controller-manager, etcd ? Sur Kapsule, où tournent-ils ? Quelle commande permet de constater leur absence ?

**Q3.** Que se passe-t-il si vous faites `sudo systemctl stop k3s` sur le server du TP1 ? Que se passerait-il si Scaleway redémarrait votre control plane Kapsule ? Vous le sauriez ?

### B. Networking et exposition

**Q4.** Quel est l'IngressController par défaut sur k3s ? Sur Kapsule ? Décrivez tout ce qui a été créé par le `helm install ingress-nginx` (dans le cluster et hors du cluster).

**Q5.** Pourquoi avez-vous pu obtenir un certificat Let's Encrypt **valide** au Bloc 6 alors que c'était impossible (ou très complexe) sur k3s en TP2 ? Quels prérequis Let's Encrypt impose-t-il qu'un cluster managé satisfait nativement ?

### C. Stockage

**Q6.** Comparez le comportement d'un PVC `local-path` (k3s) vs `scw-bssd` (Kapsule) dans le scénario : un nœud tombe, ses pods sont replanifiés sur un autre nœud. Que devient la donnée ?

**Q7.** Combien coûte 1 Go de Block Storage par mois sur Kapsule (https://www.scaleway.com/en/pricing/) ? Combien coûte 1 Go de `local-path` sur k3s ?

### D. Lifecycle et autoscaling

**Q8.** Pour ajouter un nœud à k3s : étapes ? Pour ajouter un nœud à Kapsule (CLI ou console) : étapes ? Comparez en termes de rapidité et de reproductibilité.

**Q9.** Décrivez ce que vous avez observé au Bloc 8 (autoscaling). En combien de temps le scale-up s'est-il déclenché ? Comment construire l'équivalent sur k3s, et est-ce réaliste ?

**Q10.** Mise à jour de Kubernetes 1.32 → 1.33 : qui s'en charge sur k3s ? Sur Kapsule ? Quelles précautions prendre dans chaque cas ?

### E. Sécurité et coût

**Q11.** Comment récupère-t-on le kubeconfig sur k3s ? Sur Kapsule ? Comparez la nature de l'authentification. Lequel est plus facile à révoquer en cas de fuite ?

**Q12.** Estimez le coût mensuel de votre configuration TP4 (control plane shared + 3 × DEV1-M en autoscaling vers 5 + 1 Load Balancer S + 5 Go de Block Storage + Container Registry). Référence : https://www.scaleway.com/en/pricing/

**Q13.** En cas d'incident à 3h du matin (un nœud tombe, le LB ne route plus, l'API server ne répond plus), qui doit intervenir sur k3s ? Sur Kapsule ? Comment cette responsabilité s'est-elle déplacée ?

### F. Géographie et résilience (Bloc 9)

**Q14.** Votre cluster TP4 est multi-AZ. Listez précisément ce contre quoi il vous protège, et ce contre quoi il ne vous protège pas.

**Q15.** Pour chacun des scénarios suivants, mono-région (multi-AZ) suffit-il, ou faut-il viser multi-région ? Justifiez :
- App B2B française dont les utilisateurs sont tous en France
- App SaaS européenne avec utilisateurs Berlin + Madrid + Stockholm
- Service public soumis à HDS (Hébergement de Données de Santé)

### Synthèse libre

**Q16.** Vous montez une startup avec 3 développeurs et zéro SRE. Vous devez héberger une app stateful en production. k3s self-hosted ou Kapsule ? Justifiez en 5-10 lignes.

---

## Bloc 11 — Destruction des ressources (OBLIGATOIRE)

### Étape 11.1 — Supprimer les Services LoadBalancer

```bash
kubectl delete svc -n ingress-nginx ingress-nginx-controller
sleep 30   # laisser le CCM nettoyer le LB côté Scaleway
```

### Étape 11.2 — Supprimer les namespaces

```bash
kubectl delete namespace guestbook
kubectl delete namespace ingress-nginx
kubectl delete namespace cert-manager
sleep 30
```

### Étape 11.3 — Vérifier les ressources externes

```bash
scw lb lb list region=fr-par
scw block volume list zone=fr-par-1
scw block volume list zone=fr-par-2
```

Si quelque chose subsiste :

```bash
scw lb lb delete <lb-id> region=fr-par
scw block volume delete <volume-id> zone=fr-par-1
```

### Étape 11.4 — Supprimer le cluster

```bash
scw k8s cluster delete $CLUSTER_ID region=fr-par with-additional-resources=true
```

### Étape 11.5 — Supprimer le Container Registry

```bash
scw registry namespace list region=fr-par
scw registry namespace delete <namespace-id> region=fr-par
```

### Étape 11.6 — Vérification finale

```bash
scw k8s cluster list region=fr-par
scw lb lb list region=fr-par
scw block volume list zone=fr-par-1
scw block volume list zone=fr-par-2
scw instance ip list zone=fr-par-1
scw registry namespace list region=fr-par
```

Toutes ces listes doivent être **vides**.

Dans `Billing → Cost analysis`, vérifier le matin suivant qu'aucune ressource n'a continué à facturer. Configurer une alerte dans `Billing → Alerts` pour être averti dès 5 € dépassés.

**Checkpoint 11** ✅ Toutes les listes vides, capture du projet vide à joindre au livrable.

### Pièges bloc 11

- **Service LoadBalancer non supprimé avant le cluster** : LB orphelin facturé.
- **PV en `Released`** : à supprimer manuellement.
- **Reserved IPs oubliées** : facturées même si non utilisées.

---

## Pièges fréquents — synthèse TP4

| Symptôme | Cause probable | Diagnostic |
|---|---|---|
| `scw` : `UnauthorizedException` | Clé API hors projet, Secret Key tronquée | `scw config dump` |
| `EXTERNAL-IP` reste `<pending>` | Quota LB, CCM en panne | `kubectl logs -n kube-system -l app=scaleway-cloud-controller-manager` |
| PVC `Pending` longtemps | Provision lent (jusqu'à 2 min) | `kubectl describe pvc` |
| `403 Forbidden` sur SCR | imagePullSecret manquant ou Secret Key invalide | Recréer le secret |
| Certificate stuck en Issuing | DNS qui ne pointe pas, rate limit Let's Encrypt | `kubectl describe challenge`, staging |
| Autoscaler ne déclenche pas | `requests.cpu` trop basse | Augmenter, vérifier `kubectl describe pod` |
| Facture inattendue | LB ou volume orphelin | `scw lb lb list`, `scw block volume list` |

---

## Ressources documentaires

- Documentation Kapsule : https://www.scaleway.com/en/docs/kubernetes/
- CLI Scaleway : https://github.com/scaleway/scaleway-cli/blob/main/docs/commands/k8s.md
- Annotations Load Balancer : https://github.com/scaleway/scaleway-cloud-controller-manager/blob/master/docs/loadbalancer-annotations.md
- cert-manager : https://cert-manager.io/docs/
- Let's Encrypt rate limits : https://letsencrypt.org/docs/rate-limits/
- Tarifs : https://www.scaleway.com/en/pricing/
- Scaleway Kosmos : https://www.scaleway.com/en/docs/kubernetes/concepts/#kosmos

---

## Grille d'évaluation TP4 (sur 20)

| Critère | Points |
|---|---|
| Cluster Kapsule multi-AZ provisionné, kubeconfig fonctionnel | 2 |
| Container Registry Scaleway opérationnel, images poussées et tirées | 2 |
| Livre d'or migré et fonctionnel sur Kapsule | 2 |
| Ingress + cert-manager + certificat Let's Encrypt valide | 3 |
| StatefulSet Postgres avec PVC `scw-bssd`, persistance démontrée | 2 |
| Autoscaling démontré (capture du scale-up) | 2 |
| Questionnaire de comparaison (Q1 à Q16) complet et étayé | 4 |
| Destruction propre vérifiée (captures de listes vides) | 3 |

> Note importante : un livrable sans **preuve de destruction** est plafonné à 12/20.

---

## Livrables attendus

Sur le dépôt Git, sous `tp4/` :

- `manifests/` : les YAML adaptés depuis le TP2 (diffs commentés)
- `manifests/60-clusterissuer.yaml`, `61-ingress.yaml`, `80-greedy.yaml`
- `comparaison.md` : réponses aux 16 questions du Bloc 10
- `notes-exploration.md` : observations du Bloc 3
- `cleanup-proof/` : 4 captures d'écran montrant
  - Aucun cluster (`scw k8s cluster list region=fr-par` vide)
  - Aucun Load Balancer
  - Aucun Block Storage
  - Le projet vide dans la console
- `README.md` synthétique
