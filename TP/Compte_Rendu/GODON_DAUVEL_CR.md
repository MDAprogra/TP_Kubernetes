# Compte Rendu — TP1 Kubernetes
**Binôme : Corentin GODON & Matthias DAUVEL**  
**Module : Arthur BARADEL — KUBERNETES**  
**Date : 04 Mai 2026**

---

## Pré-TP — Construction des images Docker du fil rouge

### Objectif
Construire et publier les images Docker du frontend (nginx) et du backend (Flask) sur Docker Hub, afin qu'elles soient accessibles depuis le cluster k3s sans `imagePullSecrets`.

### Architecture cible
- `webapp-backend` : API Flask exposant `/api/messages` (GET/POST) et `/api/health`
- `webapp-frontend` : nginx servant une page HTML statique qui appelle l'API via un proxy `/api/`

### Ce que nous avons fait

Nous avons créé la structure de fichiers suivante :

```text
webapp/
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf
│   └── html/
│       ├── index.html
│       └── app.js
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    └── app.py
```

Le `nginx.conf` configure un proxy crucial vers le backend — le nom `backend-svc` est résolu par CoreDNS en TP1, et sera remplacé par un Ingress en TP2 :

```nginx
location /api/ {
  proxy_pass http://backend-svc:5000/api/;
  proxy_set_header Host $host;
}
```

### Build et publication

```powershell
$env:DOCKERHUB_USER = "mdprogra"
$env:TAG = "v1.0"

docker build --platform linux/amd64 -t docker.io/$env:DOCKERHUB_USER/webapp-backend:$env:TAG ./backend
docker push docker.io/$env:DOCKERHUB_USER/webapp-backend:$env:TAG

docker build --platform linux/amd64 -t docker.io/$env:DOCKERHUB_USER/webapp-frontend:$env:TAG ./frontend
docker push docker.io/$env:DOCKERHUB_USER/webapp-frontend:$env:TAG
```

### Problème rencontré — Architecture AMD64

Lors du premier build, les images ont été buildées sans préciser la plateforme cible. Les VMs Scaleway étant en `linux/amd64`, les pods tombaient en `ImagePullBackOff` avec l'erreur :

```
no match for platform in manifest: not found
```

**Solution** : rebuild en forçant explicitement la plateforme AMD64 avec `docker buildx` :

```powershell
docker buildx create --use --name mybuilder2
docker buildx inspect --bootstrap
docker buildx build --platform linux/amd64 `
  -t mdprogra/webapp-frontend:v1.2 `
  ./frontend --push --no-cache --progress=plain
docker buildx build --platform linux/amd64 `
  -t mdprogra/webapp-backend:v1.2 `
  ./backend --push --no-cache --progress=plain
```

Les images sont publiées en **public** sur Docker Hub : `https://hub.docker.com/u/mdprogra`

![Images publiées sur Docker Hub](../../Image/TP1/PreTP/00_docker_hub.png)

---

## Bloc 1 — Installation du cluster k3s

### Objectif
Installer un cluster k3s multi-nœuds (1 server + 2 agents) et vérifier que tous les nœuds et les pods système sont opérationnels.

### Infrastructure Scaleway

| VM | Hostname | IP publique | Rôle |
|---|---|---|---|
| VM 1 | `GODON-k3s-server` | `212.47.230.56` | Control plane |
| VM 2 | `GODON-k3s-agent-1` | `163.172.161.25` | Worker 1 |
| VM 3 | `GODON-k3s-agent-2` | `212.47.246.29` | Worker 2 |

Toutes les VMs sont sous Ubuntu 26.04, zone PAR-1. Ports `6443/TCP` et `8472/UDP` ouverts entre les VMs.

### Étape 1.1 — Installation du server

Sur **`GODON-k3s-server`** :

```bash
curl -sfL https://get.k3s.io | sh -
sudo systemctl status k3s
sudo kubectl get nodes
```

Récupération du token de jonction :
```bash
sudo cat /var/lib/rancher/k3s/server/node-token
```

### Étape 1.2 — Jonction des agents

Sur **chaque agent** (remplacer `<TOKEN>`) :
```bash
curl -sfL https://get.k3s.io | \
  K3S_URL=https://212.47.230.56:6443 \
  K3S_TOKEN=<TOKEN> \
  sh -
sudo systemctl status k3s-agent
```

### Étape 1.3 — Configuration de kubectl

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
chmod 600 ~/.kube/config
echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc
source ~/.bashrc
```

### Étape 1.4 — Vérification du cluster

```bash
kubectl get nodes -o wide
kubectl get pods -A
```

Résultat :

```
NAME                STATUS   ROLES           AGE     VERSION
godon-k3s-agent-1   Ready    <none>          4m15s   v1.35.4+k3s1
godon-k3s-agent-2   Ready    <none>          3m26s   v1.35.4+k3s1
godon-k3s-server    Ready    control-plane   6m34s   v1.35.4+k3s1
```

![VMs Scaleway — infrastructure du cluster](../../Image/TP1/Bloc1/01_vms_scaleway.png)

![kubectl get nodes — 3 nœuds Ready](../../Image/TP1/Bloc1/02_nodes_ready.png)

![kubectl get pods -A — pods système Running](../../Image/TP1/Bloc1/03_pods_systeme.png)

✅ **Checkpoint 1.A** : 3 nœuds en `Ready`. Pods système (`coredns`, `traefik`, `local-path-provisioner`, `metrics-server`) en `Running`.

---

## Bloc 2 — Pods, ReplicaSets, Deployments

### Objectif
Comprendre et utiliser les primitives Pod, ReplicaSet, Deployment. Observer l'auto-réparation, le scaling déclaratif et le rolling update.

### Étape 2.1 — Pod impératif & déclaratif

**1. Pod impératif :**  
Test de création rapide d'un pod en ligne de commande :

![Création d'un pod en impératif](../../Image/TP1/Bloc2/07_pod_imperatif.png)

**2. Pod déclaratif (`01-pod.yaml`) :**  
Création d'un pod nginx basique pour comprendre la structure d'un manifest YAML et observer le comportement sans Deployment :

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: nginx-pod
  labels:
    app: demo
spec:
  containers:
  - name: nginx
    image: nginx:1.27-alpine
    ports:
    - containerPort: 80
    resources:
      requests: { cpu: "50m", memory: "64Mi" }
      limits:   { cpu: "200m", memory: "128Mi" }
```

```bash
kubectl apply -f 01-pod.yaml
kubectl get pods -o wide
kubectl describe pod nginx-pod
kubectl exec -it nginx-pod -- sh
kubectl delete -f 01-pod.yaml
```

> Un Pod seul supprimé n'est **pas recréé** — il n'existe pas de contrôleur pour le surveiller. C'est le rôle du Deployment/ReplicaSet.

![Pod déclaratif — kubectl get pods](../../Image/TP1/Bloc2/01_pod_declaratif.png)

### Étape 2.2 — Deployment frontend (`02-frontend-deploy.yaml`)

Un Deployment gère un ReplicaSet qui maintient le nombre souhaité de pods en vie. Les `readinessProbe` et `livenessProbe` permettent à Kubernetes de vérifier la santé des pods :

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
  labels: { app: webapp, tier: front }
spec:
  replicas: 3
  selector:
    matchLabels: { app: webapp, tier: front }
  template:
    metadata:
      labels: { app: webapp, tier: front }
    spec:
      containers:
      - name: frontend
        image: docker.io/mdprogra/webapp-frontend:v1.2
        ports:
        - containerPort: 80
        readinessProbe:
          httpGet: { path: /, port: 80 }
          initialDelaySeconds: 2
        livenessProbe:
          httpGet: { path: /, port: 80 }
          initialDelaySeconds: 5
          periodSeconds: 10
```

```bash
kubectl apply -f 02-frontend-deploy.yaml
kubectl get deploy,rs,pod
kubectl rollout status deploy/frontend
```

![Deployment frontend — 3 réplicas en Running](../../Image/TP1/Bloc2/02_frontend_deployment.png)

### Étape 2.3 — Auto-réparation

Kubernetes surveille en permanence l'état du cluster via le ReplicaSet controller. Si un pod est supprimé manuellement, il est immédiatement recréé :

```bash
kubectl delete pod frontend-5f6fd4c584-8xbs5
kubectl get pods -l tier=front -w
```

On observe la création du pod de remplacement (`hf7pv`) quasi-instantanément.

![Auto-réparation — recréation immédiate du pod supprimé](../../Image/TP1/Bloc2/03_auto_reparation.png)

### Étape 2.4 — Scaling

```bash
# Scaling impératif
kubectl scale deploy/frontend --replicas=5
kubectl get pods -l tier=front

kubectl scale deploy/frontend --replicas=2
```

Le scaling est **déclaratif** : on déclare l'état souhaité et Kubernetes s'assure de le respecter. La même opération peut être faite en modifiant `replicas` dans le YAML puis `kubectl apply`.

![Scaling — variation du nombre de réplicas (3 → 5 → 2)](../../Image/TP1/Bloc2/04_scaling.png)

### Étape 2.5 — Rolling update & Rollback

```bash
# Mise à jour de l'image vers v1.1
kubectl set image deploy/frontend frontend=docker.io/mdprogra/webapp-frontend:v1.1
kubectl rollout status deploy/frontend
kubectl rollout history deploy/frontend

# Rollback à la version précédente
kubectl rollout undo deploy/frontend
```

Le rolling update remplace les pods progressivement **sans interruption de service**. Le rollback revient à la révision précédente en utilisant l'historique des ReplicaSets.

![Rolling update — déploiement progressif](../../Image/TP1/Bloc2/05_rolling_update.png)

![Rollback — retour à la révision précédente](../../Image/TP1/Bloc2/06_rollback.png)

✅ **Checkpoint 2** : 3 puis 5 puis 2 réplicas observés. Suppression manuelle d'un pod → recréation automatique. Rollback fonctionnel.

---

## Bloc 3 — Services et exposition

### Objectif
Exposer les pods via des Services pour permettre la communication interne (ClusterIP) et l'accès depuis l'extérieur du cluster (NodePort).

### Étape 3.1 — Service ClusterIP (`03-frontend-svc.yaml`)

Un Service ClusterIP expose les pods **uniquement à l'intérieur du cluster**, avec une IP virtuelle stable indépendante du cycle de vie des pods. Le selector fait le lien avec les pods via leurs labels :

```yaml
apiVersion: v1
kind: Service
metadata:
  name: frontend-svc
spec:
  type: ClusterIP
  selector: { app: webapp, tier: front }
  ports:
  - port: 80
    targetPort: 80
```

```bash
kubectl apply -f 03-frontend-svc.yaml
kubectl get svc
kubectl describe svc frontend-svc

# Test depuis un pod éphémère
kubectl run curl-test --rm -it --image=curlimages/curl:latest -- sh
# curl http://frontend-svc/
```

![Service ClusterIP frontend — kubectl get svc](../../Image/TP1/Bloc3/01_clusterip_svc.png)

![Test ClusterIP depuis un pod éphémère curl-test](../../Image/TP1/Bloc3/03_curl_test.png)

### Étape 3.2 — Service NodePort (`04-frontend-nodeport.yaml`)

Un Service NodePort expose les pods **depuis l'extérieur du cluster** sur un port fixe (30080) de chaque nœud :

```yaml
apiVersion: v1
kind: Service
metadata:
  name: frontend-nodeport
spec:
  type: NodePort
  selector: { app: webapp, tier: front }
  ports:
  - port: 80
    targetPort: 80
    nodePort: 30080
```

Le frontend est accessible sur `http://212.47.230.56:30080`. À ce stade, le frontend s'affiche mais l'API est cassée — le backend n'est pas encore déployé.

![Service NodePort — frontend accessible sur :30080](../../Image/TP1/Bloc3/02_nodeport_svc.png)

✅ **Checkpoint 3** : Service `ClusterIP` joignable depuis un pod du cluster. Frontend accessible sur `:30080` depuis le réseau.

---

## Bloc 4 — Application multi-tier complète

### Objectif
Déployer le backend Flask, le connecter au frontend via la résolution DNS interne de Kubernetes (CoreDNS), et valider le fonctionnement de bout en bout.

### Étape 4.1 — Backend (`05-backend.yaml`)

Le fichier contient le Deployment et le Service dans un seul manifest (séparés par `---`).  
> ⚠️ Le nom du Service `backend-svc` doit correspondre exactement à celui codé dans `nginx.conf` (`proxy_pass http://backend-svc:5000`).

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
  labels: { app: webapp, tier: back }
spec:
  replicas: 2
  selector:
    matchLabels: { app: webapp, tier: back }
  template:
    metadata:
      labels: { app: webapp, tier: back }
    spec:
      containers:
      - name: backend
        image: docker.io/mdprogra/webapp-backend:v1.2
        ports:
        - containerPort: 5000
        env:
        - name: APP_ENV
          value: "tp1"
        readinessProbe:
          httpGet: { path: /api/health, port: 5000 }
          initialDelaySeconds: 3
---
apiVersion: v1
kind: Service
metadata:
  name: backend-svc
spec:
  selector: { app: webapp, tier: back }
  ports:
  - port: 5000
    targetPort: 5000
```

```bash
kubectl apply -f 05-backend.yaml
kubectl get pods,svc -l app=webapp
```

### Étape 4.2 — Résolution DNS interne (CoreDNS)

Le nom `backend-svc` dans `nginx.conf` est résolu automatiquement par CoreDNS. Chaque Service Kubernetes reçoit un nom DNS au format :  
`<nom-service>.<namespace>.svc.cluster.local`

```bash
kubectl exec -it <pod-frontend> -- sh
nslookup backend-svc
# → backend-svc.default.svc.cluster.local → 10.43.51.23
wget -qO- http://backend-svc:5000/api/health
```

![nslookup backend-svc depuis un pod frontend](../../Image/TP1/Bloc4/02_nslookup_dns.png)

### Étape 4.3 — Test end-to-end

L'application est accessible sur `http://212.47.230.56:30080`. Le livre d'or affiche le nom du pod backend qui répond (`served_by`), ce qui change selon le pod sélectionné par le load balancer — preuve du fonctionnement multi-tier.

```bash
kubectl logs -l tier=back -f
```

![Livre d'or fonctionnel de bout en bout](../../Image/TP1/Bloc4/01_guestbook_fonctionnel.png)

### Problème rencontré — ImagePullBackOff sur agent-2

Le nœud `godon-k3s-agent-2` présentait systématiquement des erreurs `ImagePullBackOff`. Diagnostic :

```bash
kubectl get events --sort-by=.lastTimestamp
kubectl describe pod <pod>
# → Events: Failed to pull image: no match for platform in manifest
kubectl get endpoints backend-svc
```

**Cause** : images buildées sans `--platform linux/amd64`.  
**Solution** : rebuild avec `docker buildx --platform linux/amd64` (cf. Pré-TP).

Le nœud `agent-2` continuait à avoir des problèmes après correction, probablement dû à un cache containerd corrompu. Les pods se sont répartis sur `agent-1` et `server` sans impact sur le fonctionnement global.

✅ **Checkpoint 4** : Application multi-tier fonctionnelle de bout en bout. Au moins une panne diagnostiquée et corrigée.

---

## Bloc 5 — Défis ouverts

### Défi A — Anti-affinité des pods backend

#### Objectif
Garantir que deux pods backend ne soient **jamais schedulés sur le même nœud**. Cela améliore la haute disponibilité : si un nœud tombe, au moins un pod backend reste disponible sur un autre nœud.

#### Concept
L'anti-affinité (`podAntiAffinity`) dit au scheduler Kubernetes : *"ne place pas ce pod sur un nœud où tourne déjà un pod avec ces labels"*.

Il existe deux modes :
- `requiredDuringSchedulingIgnoredDuringExecution` — **strict** : le pod reste en `Pending` si aucun nœud libre
- `preferredDuringSchedulingIgnoredDuringExecution` — **souple** : préférence, non bloquant

Nous utilisons le mode **strict** avec `topologyKey: kubernetes.io/hostname` pour séparer les pods par nœud physique.

#### YAML (`defis/defi-A.yaml`)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
  labels: { app: webapp, tier: back }
spec:
  replicas: 2
  selector:
    matchLabels: { app: webapp, tier: back }
  template:
    metadata:
      labels: { app: webapp, tier: back }
    spec:
      affinity:
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
          - labelSelector:
              matchLabels:
                app: webapp
                tier: back
            topologyKey: kubernetes.io/hostname
      containers:
      - name: backend
        image: docker.io/mdprogra/webapp-backend:v1.2
        ports:
        - containerPort: 5000
        env:
        - name: APP_ENV
          value: "tp1"
        readinessProbe:
          httpGet: { path: /api/health, port: 5000 }
          initialDelaySeconds: 3
---
apiVersion: v1
kind: Service
metadata:
  name: backend-svc
spec:
  selector: { app: webapp, tier: back }
  ports:
  - port: 5000
    targetPort: 5000
```

#### Vérification

```bash
kubectl get pods -o wide -l tier=back
```

Résultat obtenu :

```
NAME                       READY   STATUS    NODE
backend-58d789f58c-wjlgx   1/1     Running   godon-k3s-server
backend-64b7bdb4f9-hz2r8   1/1     Running   godon-k3s-agent-1
```

Les deux pods backend sont bien sur des **nœuds différents** ✅

![Défi A — Anti-affinité — pods backend sur nœuds distincts](../../Image/TP1/Bloc5/01_defi_A.png)

✅ **Défi A validé** : l'anti-affinité garantit la répartition des pods backend sur des nœuds distincts.

### Défi B — Init container

#### Objectif
Ajouter un `initContainer` au backend qui simule l'attente d'une base de données (`postgres-svc`) avant de démarrer l'application principale. Cela montre comment bloquer le démarrage d'un pod tant qu'une dépendance n'est pas prête.

#### Vérification et Logs

L'application backend reste en `Init:0/1` tant que l'initContainer tourne. Une fois terminé, le pod passe en `Running`.

![Déploiement avec Init Container](../../Image/TP1/Bloc5/02_defi_B.png)

![Logs de l'Init Container](../../Image/TP1/Bloc5/02_defi_B_logs.png)

✅ **Défi B validé** : l'initContainer s'exécute correctement et loggue son statut avant de laisser le conteneur principal démarrer.

### Défi C — Multi-conteneurs (Sidecar)

#### Objectif
Modifier le pod frontend pour ajouter un conteneur *sidecar* (`busybox`) qui lit en temps réel (`tail -f`) les logs de nginx via un volume partagé.

#### Vérification et Logs

Le pod frontend s'affiche désormais avec `2/2` conteneurs prêts (le serveur nginx + le sidecar).

![Pod frontend avec Sidecar (2/2 Ready)](../../Image/TP1/Bloc5/03_defi_C.png)

En interrogeant spécifiquement les logs du deuxième conteneur, on obtient bien les logs d'accès nginx :

![Logs du conteneur Sidecar](../../Image/TP1/Bloc5/03_defi_C_logs.png)

✅ **Défi C validé** : le conteneur sidecar tourne correctement dans le même pod que le frontend et accède aux logs partagés.

---

## Synthèse des commandes clés

| Commande | Description |
|---|---|
| `kubectl apply -f <fichier>` | Appliquer un manifest YAML |
| `kubectl get deploy,rs,pod` | Lister les ressources principales |
| `kubectl get pods -o wide -w` | Observer les pods en temps réel avec le nœud |
| `kubectl describe pod <pod>` | Détails et événements d'un pod |
| `kubectl logs <pod> --previous` | Logs du conteneur précédent (après crash) |
| `kubectl exec -it <pod> -- sh` | Shell dans un pod |
| `kubectl scale deploy/<nom> --replicas=N` | Scaling impératif |
| `kubectl rollout status deploy/<nom>` | Suivre un déploiement |
| `kubectl rollout undo deploy/<nom>` | Rollback |
| `kubectl rollout history deploy/<nom>` | Historique des révisions |
| `kubectl get events --sort-by=.lastTimestamp` | Événements triés par date |
| `kubectl get endpoints <svc>` | Vérifier les endpoints d'un Service |
| `kubectl get svc` | Lister les services |

---

## Pièges rencontrés

| Symptôme | Cause | Résolution |
|---|---|---|
| `ImagePullBackOff` | Images buildées sans `--platform linux/amd64` | Rebuild avec `docker buildx --platform linux/amd64` |
| Service sans endpoints | Selector ne correspond pas aux labels des pods | Vérifier `kubectl describe svc` et les labels |
| Frontend affiche mais API échoue | Backend non déployé ou `backend-svc` introuvable | Déployer le backend, vérifier le nom du Service |
| Pod en `Pending` | Ressources insuffisantes ou anti-affinité stricte | `kubectl describe pod` → Events |


---
---

# Compte Rendu — TP2 Kubernetes
**Binôme : Corentin GODON & Matthias DAUVEL**  
**Module : Arthur BARADEL — KUBERNETES**  
**Date : 04 Mai 2026**

---

## Pré-requis — Image backend v2.0

### Contexte

Le TP1 utilisait un backend Flask qui stockait les messages **en mémoire** (perdus à chaque redémarrage). Pour le TP2, le backend doit pouvoir se connecter à une base **PostgreSQL** via la variable d'environnement `DATABASE_URL`. Si celle-ci est absente, il retombe automatiquement en mode mémoire — ce qui assure la rétrocompatibilité avec le TP1.

### Modifications apportées

**`backend/requirements.txt`** — Ajout de `psycopg2-binary` :

```
flask==3.0.3
psycopg2-binary==2.9.9
```

**`backend/app.py`** — Ajout de la logique de connexion Postgres :
- Initialisation d'un pool de connexions via `psycopg2` si `DATABASE_URL` est définie
- Création automatique de la table `messages` au démarrage (`init_db()`)
- Le champ `backend_mode` du JSON retourne `"postgres"` ou `"memory"` selon le mode actif

### Build et push (multi-architecture)

Comme pour le TP1, on cible explicitement `linux/amd64` et `linux/arm64` pour couvrir les VMs Scaleway et les machines de développement (Mac M1/M2) :

```powershell
$env:DOCKERHUB_USER = "mdprogra"

docker buildx build --platform linux/amd64,linux/arm64 `
  -t docker.io/$env:DOCKERHUB_USER/webapp-backend:v2.0 `
  ./backend --push --no-cache
```

![Image webapp-backend:v2.0 publiée sur Docker Hub](../../Image/TP2/PreTP/00_docker_hub_v2.png)

✅ **Pré-requis validé** : l'image `mdprogra/webapp-backend:v2.0` est publiée en **public** sur Docker Hub, accessible pour les deux architectures.

---

## Bloc 1 — ConfigMaps & Secrets

### Objectif
Externaliser la configuration applicative et les credentials hors du code source. Comprendre la différence entre ConfigMap (données publiques) et Secret (données sensibles encodées en base64), et maîtriser leurs trois méthodes de création.

### Étape 1.1 — Namespace dédié

Pour isoler les ressources du TP2 du namespace `default` :

```bash
kubectl create namespace guestbook
kubectl config set-context --current --namespace=guestbook
kubectl config view --minify | grep namespace
```

Toutes les commandes suivantes opèrent dans ce namespace.

### Étape 1.2 — ConfigMap : trois méthodes de création

**Méthode 1 — impérative (littéraux)** :

```bash
kubectl create configmap demo-cm \
  --from-literal=APP_ENV=tp2 \
  --from-literal=LOG_LEVEL=info
kubectl get cm demo-cm -o yaml
kubectl delete cm demo-cm
```

![ConfigMap créé en impératif — kubectl get cm -o yaml](../../Image/TP2/Bloc1/01_configmap_imperatif.png)

**Méthode 2 — à partir d'un fichier** :

```bash
echo "welcome=Bienvenue sur le livre d'or persistant !" > app.properties
kubectl create configmap demo-cm --from-file=app.properties
kubectl get cm demo-cm -o yaml
kubectl delete cm demo-cm
```

![ConfigMap créé depuis un fichier app.properties](../../Image/TP2/Bloc1/02_configmap_fichier.png)

**Méthode 3 — déclarative (privilégiée)** via `10-configmap.yaml` :

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: webapp-config
data:
  APP_ENV: "tp2-prod"
  WELCOME_MESSAGE: "Bienvenue sur le livre d'or persistant !"
  LOG_LEVEL: "info"
```

```bash
kubectl apply -f 10-configmap.yaml
kubectl describe cm webapp-config
```

![ConfigMap déclaratif webapp-config — kubectl describe](../../Image/TP2/Bloc1/03_configmap_declaratif.png)

### Étape 1.3 — Secret : credentials Postgres

Le Secret est encodé en **base64**, pas chiffré — quiconque a accès à l'API Kubernetes peut décoder les valeurs. En production, il faudrait coupler avec `EncryptionConfiguration` côté kube-apiserver ou un gestionnaire externe (HashiCorp Vault, Sealed Secrets).

`11-secret.yaml` utilise `stringData` (Kubernetes encode lui-même) plutôt que `data` (qui exigerait du base64 manuel) :

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: postgres-credentials
type: Opaque
stringData:
  POSTGRES_USER: "guestbook"
  POSTGRES_PASSWORD: "ChangeMe_inTP2!"
  POSTGRES_DB: "guestbook"
```

```bash
kubectl apply -f 11-secret.yaml
kubectl get secret postgres-credentials -o yaml
echo "Z3Vlc3Rib29r" | base64 -d   # → guestbook
```

![Secret postgres-credentials — valeurs encodées en base64 visibles](../../Image/TP2/Bloc1/04_secret.png)

### Étape 1.4 — Injection de la config dans un pod test

Le pod `config-tester` (`12-test-config.yaml`) injecte le ConfigMap et le Secret comme variables d'environnement, et monte le ConfigMap en volume pour vérifier les deux modes d'accès :

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: config-tester
spec:
  containers:
  - name: app
    image: busybox:1.36
    command: ["sh", "-c", "env | sort && sleep 3600"]
    envFrom:
    - configMapRef:
        name: webapp-config
    - secretRef:
        name: postgres-credentials
    volumeMounts:
    - name: cfg-vol
      mountPath: /etc/cfg
  volumes:
  - name: cfg-vol
    configMap:
      name: webapp-config
```

```bash
kubectl apply -f 12-test-config.yaml
kubectl logs config-tester | grep -E "APP_ENV|WELCOME|POSTGRES"
kubectl exec config-tester -- ls /etc/cfg
kubectl exec config-tester -- cat /etc/cfg/WELCOME_MESSAGE
kubectl delete -f 12-test-config.yaml
```

![Pod config-tester — variables d'environnement et volume ConfigMap visibles](../../Image/TP2/Bloc1/05_pod_test_config.png)

![Checkpoint 1 — ConfigMap et Secret opérationnels dans le namespace guestbook](../../Image/TP2/Bloc1/06_checkpoint1.png)

> **Piège rencontré** : modifier un ConfigMap ne redémarre pas automatiquement les pods qui l'utilisent. Il faut déclencher manuellement un `kubectl rollout restart deploy/<nom>`.

✅ **Checkpoint 1** : ConfigMap `webapp-config` et Secret `postgres-credentials` créés dans le namespace `guestbook`. Le pod test affiche les variables attendues et accède au ConfigMap monté en volume.

---

## Bloc 2 — PV / PVC / StorageClass

### Objectif
Comprendre le modèle de stockage persistant de Kubernetes : PersistentVolume (PV), PersistentVolumeClaim (PVC) et StorageClass. Observer le provisionnement dynamique de k3s (`local-path`) et démontrer la survie des données après suppression d'un pod.

### Étape 2.1 — Inspection de ce qui existe en k3s

k3s embarque `local-path-provisioner` qui crée automatiquement des PV sur le disque du nœud hébergeant le pod :

```bash
kubectl get sc
kubectl describe sc local-path
kubectl get pv     # vide initialement
kubectl get pvc -A
```

![StorageClass local-path — ReclaimPolicy Delete, WaitForFirstConsumer](../../Image/TP2/Bloc2/07_storageclass.png)

Points clés observés :
- `Provisioner: rancher.io/local-path`
- `ReclaimPolicy: Delete` — le PV est supprimé avec le PVC
- `volumeBindingMode: WaitForFirstConsumer` — le PV n'est créé qu'au moment où un pod le réclame, pour garantir la colocalisation pod/volume sur le même nœud

### Étape 2.2 — Premier PVC et WaitForFirstConsumer

**`20-pvc-demo.yaml`** :

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: demo-pvc
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: local-path
  resources:
    requests:
      storage: 200Mi
```

```bash
kubectl apply -f 20-pvc-demo.yaml
kubectl get pvc    # → Pending : aucun pod ne l'utilise encore
kubectl get pv     # → toujours vide
```

![PVC demo-pvc en Pending — WaitForFirstConsumer en action](../../Image/TP2/Bloc2/08_pvc_pending.png)

Après attachement au pod `writer` (`21-pod-pvc.yaml`) :

```bash
kubectl apply -f 21-pod-pvc.yaml
kubectl get pv     # → un PV apparaît, lié au PVC
kubectl exec writer -- cat /data/log.txt
```

![PVC Bound après attachement — PV créé dynamiquement par local-path-provisioner](../../Image/TP2/Bloc2/09_pvc_bound.png)

### Étape 2.3 — Démonstration de la persistance

```bash
kubectl delete pod writer
kubectl apply -f 21-pod-pvc.yaml
kubectl exec writer -- cat /data/log.txt
# La ligne écrite lors du run précédent est toujours présente

kubectl delete -f 21-pod-pvc.yaml
kubectl delete -f 20-pvc-demo.yaml
```

> **Piège rencontré** : avec `accessModes: ReadWriteOnce`, le volume ne peut être monté que par **un seul pod à la fois** sur un seul nœud. Tenter de scaler un Deployment avec un PVC `local-path` en RWO provoque un blocage immédiat des pods supplémentaires.

✅ **Checkpoint 2** : PVC créé, PV provisionné dynamiquement à la première utilisation. Données persistantes après suppression du pod. Comportement `WaitForFirstConsumer` observé et compris.

---

## Bloc 3 — StatefulSet Postgres

### Objectif
Déployer PostgreSQL via un StatefulSet pour bénéficier d'une identité réseau stable, d'un volume dédié par réplica et d'un démarrage/arrêt ordonné — caractéristiques essentielles pour les bases de données.

### Étape 3.1 — Pourquoi un StatefulSet ?

Contrairement aux Deployments (pods interchangeables et anonymes), un StatefulSet garantit :
- **Identité stable** : `postgres-0`, `postgres-1`, … — le nom ne change pas entre redémarrages
- **Volume dédié par réplica** via `volumeClaimTemplates`
- **DNS prédictible** : `postgres-0.postgres-svc.<namespace>.svc.cluster.local`
- **Ordonnancement** : `postgres-0` démarre avant `postgres-1`

### Étape 3.2 — Headless Service + StatefulSet

**`30-postgres.yaml`** — Le service `clusterIP: None` (headless) permet la résolution DNS directe vers les pods nommés, sans passer par une VIP :

```yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres-svc
  labels: { app: postgres }
spec:
  clusterIP: None
  selector: { app: postgres }
  ports:
  - port: 5432
    targetPort: 5432
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
spec:
  serviceName: postgres-svc
  replicas: 1
  selector:
    matchLabels: { app: postgres }
  template:
    metadata:
      labels: { app: postgres }
    spec:
      containers:
      - name: postgres
        image: postgres:16-alpine
        envFrom:
        - secretRef:
            name: postgres-credentials
        ports:
        - containerPort: 5432
          name: pg
        volumeMounts:
        - name: data
          mountPath: /var/lib/postgresql/data
          subPath: pgdata
        readinessProbe:
          exec:
            command: ["pg_isready", "-U", "guestbook"]
          initialDelaySeconds: 5
          periodSeconds: 5
        resources:
          requests: { cpu: "100m", memory: "256Mi" }
          limits:   { cpu: "500m", memory: "512Mi" }
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: ["ReadWriteOnce"]
      storageClassName: local-path
      resources:
        requests:
          storage: 1Gi
```

> Le `subPath: pgdata` est indispensable : sans lui, `initdb` échoue car il détecte un répertoire `lost+found` dans le mountpoint et considère le répertoire non vide.

```bash
kubectl apply -f 30-postgres.yaml
kubectl get sts,pod,pvc,svc -l app=postgres
```

![StatefulSet postgres-0 en Running avec PVC data-postgres-0 lié](../../Image/TP2/Bloc3/10_postgres_running.png)

![PVC data-postgres-0 — 1Gi Bound sur le nœud](../../Image/TP2/Bloc3/11_postgres_pvc.png)

### Étape 3.3 — Vérification et résolution DNS

```bash
kubectl exec -it postgres-0 -- psql -U guestbook -d guestbook -c "\dt"
kubectl exec -it postgres-0 -- psql -U guestbook -d guestbook -c \
  "CREATE TABLE test(id INT); INSERT INTO test VALUES (1); SELECT * FROM test;"
```

Test DNS depuis un pod éphémère :

```bash
kubectl run dns-test --rm -it --image=busybox:1.36 -- sh
# nslookup postgres-svc
# nslookup postgres-0.postgres-svc
```

![Connexion psql à postgres-0 — DNS headless résolu correctement](../../Image/TP2/Bloc3/12_postgres_connect.png)

### Étape 3.4 — Test de persistance

```bash
kubectl exec postgres-0 -- psql -U guestbook -d guestbook -c "INSERT INTO test VALUES (42);"
kubectl delete pod postgres-0
kubectl get pod postgres-0 -w   # recréation automatique par le StatefulSet controller
kubectl exec postgres-0 -- psql -U guestbook -d guestbook -c "SELECT * FROM test;"
# → les valeurs 1 et 42 sont toujours présentes
```

> **Piège rencontré** : `kubectl delete sts postgres` ne supprime **pas** les PVC associés. Le volume `data-postgres-0` persiste et doit être supprimé manuellement avec `kubectl delete pvc data-postgres-0` pour repartir de zéro.

✅ **Checkpoint 3** : `postgres-0` en Running, PVC `data-postgres-0` lié (1Gi). DNS `postgres-0.postgres-svc` résolvable depuis un pod éphémère. Données persistantes après suppression et recréation du pod.

---

## Bloc 4 — Backend v2 connecté à Postgres

### Objectif
Déployer le backend v2.0 connecté à PostgreSQL via ConfigMap et Secret, puis démontrer que les messages du livre d'or survivent aux redémarrages du backend — contrairement au mode mémoire du TP1.

### Étape 4.1 — Deployment backend v2

**`40-backend-v2.yaml`** — L'ordre des variables `env` est critique : `DB_USER`, `DB_PASS` et `DB_NAME` doivent être définis **avant** `DATABASE_URL` pour que la substitution `$(...)` de Kubernetes fonctionne :

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
  labels: { app: webapp, tier: back }
spec:
  replicas: 2
  selector:
    matchLabels: { app: webapp, tier: back }
  template:
    metadata:
      labels: { app: webapp, tier: back }
    spec:
      containers:
      - name: backend
        image: docker.io/mdprogra/webapp-backend:v2.0
        ports:
        - containerPort: 5000
        envFrom:
        - configMapRef:
            name: webapp-config
        env:
        - name: DB_USER
          valueFrom:
            secretKeyRef: { name: postgres-credentials, key: POSTGRES_USER }
        - name: DB_PASS
          valueFrom:
            secretKeyRef: { name: postgres-credentials, key: POSTGRES_PASSWORD }
        - name: DB_NAME
          valueFrom:
            secretKeyRef: { name: postgres-credentials, key: POSTGRES_DB }
        - name: DATABASE_URL
          value: "postgresql://$(DB_USER):$(DB_PASS)@postgres-0.postgres-svc:5432/$(DB_NAME)"
        readinessProbe:
          httpGet: { path: /api/health, port: 5000 }
          initialDelaySeconds: 5
        resources:
          requests: { cpu: "50m", memory: "64Mi" }
          limits:   { cpu: "200m", memory: "256Mi" }
---
apiVersion: v1
kind: Service
metadata:
  name: backend-svc
spec:
  selector: { app: webapp, tier: back }
  ports:
  - port: 5000
    targetPort: 5000
```

```bash
kubectl apply -f 40-backend-v2.yaml
kubectl rollout status deploy/backend
kubectl logs -l tier=back
```

![Backend v2 — 2 pods Running, connectés à Postgres](../../Image/TP2/Bloc4/13_backend_v2_running.png)

### Étape 4.2 — Redéploiement du frontend

Le frontend ne change pas par rapport au TP1. On réapplique les manifests existants (le `nginx.conf` continue de proxier vers `backend-svc:5000`) :

```bash
kubectl apply -f 02-frontend-deploy.yaml
kubectl apply -f 03-frontend-svc.yaml
```

![Frontend redéployé dans le namespace guestbook](../../Image/TP2/Bloc4/15_frontend_running.png)

### Étape 4.3 — Test complet et démonstration de persistance

Accès temporaire via port-forward :

```bash
kubectl port-forward svc/frontend-svc 8080:80 --address 0.0.0.0
```

Après avoir posté 3 messages depuis le navigateur :

```bash
kubectl rollout restart deploy/backend
kubectl rollout status deploy/backend
```

![Livre d'or v2 — interface fonctionnelle avec backend_mode: postgres](../../Image/TP2/Bloc4/16_guestbook_v2.png)

Les messages sont **toujours présents** après le rollout restart — contrairement au TP1 où ils étaient perdus à chaque redémarrage. Vérification directe en SQL :

```bash
kubectl exec postgres-0 -- psql -U guestbook -d guestbook -c "SELECT * FROM messages;"
```

![Table messages PostgreSQL — données persistées en base](../../Image/TP2/Bloc4/14_postgres_table.png)

![Persistance vérifiée — messages survivent au rollout restart](../../Image/TP2/Bloc4/17_persistance.png)

> **Moment pédagogique clé** : le champ `backend_mode` de la réponse JSON renvoie `"postgres"` (et non `"memory"` comme en TP1), confirmant que le backend utilise bien la base de données.

> **Piège rencontré** : si `DATABASE_URL` n'est pas correctement interpolée (ordre des `env` incorrect), le backend démarre silencieusement en mode mémoire sans erreur visible — seul `backend_mode: "memory"` dans le JSON le trahit.

✅ **Checkpoint 4** : le livre d'or persiste à travers les redémarrages du backend. `backend_mode: "postgres"` confirmé. Messages visibles dans la table SQL `messages`.

---

## Bloc 5 — Ingress Traefik + TLS

### Objectif
Remplacer l'accès NodePort par un Ingress Traefik (niveau L7) permettant le routage HTTP par nom d'hôte, puis activer HTTPS avec un certificat TLS auto-signé.

### Étape 5.1 — Concept et inspection de Traefik

Au tableau : les Services opèrent en **L4** (TCP/UDP) — ils exposent un port mais ne connaissent pas le contenu HTTP. Un Ingress opère en **L7** (HTTP) et peut router selon le `Host` ou le chemin URL, ce qui permet de centraliser l'entrée du cluster sur un seul point.

k3s embarque Traefik comme IngressController, déployé dans `kube-system` avec un Service LoadBalancer qui écoute sur `:80` et `:443` de chaque nœud :

```bash
kubectl get pods -n kube-system | grep traefik
kubectl get svc -n kube-system traefik
```

### Étape 5.2 — Premier Ingress, routage par nom d'hôte

Ajout de la résolution DNS locale sur la machine de test :

```bash
# Linux/macOS : /etc/hosts — Windows : C:\Windows\System32\drivers\etc\hosts
212.47.230.56  guestbook.labo.local
```

**`50-ingress.yaml`** :

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: webapp-ingress
spec:
  rules:
  - host: guestbook.labo.local
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
kubectl apply -f 50-ingress.yaml
kubectl get ingress
kubectl describe ingress webapp-ingress
curl -H "Host: guestbook.labo.local" http://212.47.230.56/
```

![Ingress HTTP — kubectl get ingress et test curl](../../Image/TP2/Bloc5/18_ingress_http.png)

![Navigateur — http://guestbook.labo.local/ fonctionnel](../../Image/TP2/Bloc5/19_ingress_navigateur.png)

### Étape 5.3 — Activation de TLS avec certificat auto-signé

Génération du certificat et création du Secret TLS :

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout tls.key -out tls.crt \
  -subj "/CN=guestbook.labo.local/O=labo" \
  -addext "subjectAltName=DNS:guestbook.labo.local"

kubectl create secret tls guestbook-tls \
  --cert=tls.crt --key=tls.key
kubectl get secret guestbook-tls
```

![Secret TLS guestbook-tls créé dans le namespace guestbook](../../Image/TP2/Bloc5/20_tls_secret.png)

Mise à jour de l'Ingress via **`51-ingress-tls.yaml`** — l'annotation active l'écoute sur les deux entrypoints Traefik (`web` port 80 et `websecure` port 443) :

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: webapp-ingress
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web,websecure
spec:
  tls:
  - hosts:
    - guestbook.labo.local
    secretName: guestbook-tls
  rules:
  - host: guestbook.labo.local
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
kubectl apply -f 51-ingress-tls.yaml
curl -k https://guestbook.labo.local/
```

![Ingress TLS — kubectl describe avec section TLS active](../../Image/TP2/Bloc5/21_ingress_tls.png)

![Navigateur — https://guestbook.labo.local/ avec avertissement cert auto-signé](../../Image/TP2/Bloc5/22_https_navigateur.png)

L'avertissement du navigateur est attendu : le certificat est auto-signé et n'est pas reconnu par une CA de confiance. En production, on utiliserait `cert-manager` avec Let's Encrypt pour obtenir un certificat valide automatiquement.

> **Piège rencontré** : le Secret TLS doit être dans le **même namespace** que l'Ingress. Un Secret créé dans `default` pour un Ingress dans `guestbook` est silencieusement ignoré par Traefik, qui sert alors du HTTP nu sur le port 443.

✅ **Checkpoint 5** : le livre d'or est joignable via `http://guestbook.labo.local/`. HTTPS fonctionne avec l'avertissement de certificat auto-signé attendu.

---

## Bloc 6 — NetworkPolicies

### Objectif
Sécuriser le trafic intra-cluster avec des NetworkPolicies : appliquer un *default-deny* sur tout le namespace, puis rouvrir uniquement les flux légitimes (Traefik → frontend → backend → Postgres), et vérifier qu'un pod intrus ne peut plus joindre Postgres.

### Étape 6.1 — Vérifier que le CNI applique les policies

k3s utilise flannel + kube-router (depuis k3s v1.21) pour appliquer les NetworkPolicies :

```bash
kubectl get pods -n kube-system | grep -E "flannel|kube-router"
```

Si `kube-router` n'apparaît pas, vérifier que k3s n'a pas été démarré avec `--disable-network-policy`.

### Étape 6.2 — Tester l'absence d'isolation (avant policy)

Avant toute NetworkPolicy, n'importe quel pod peut joindre Postgres directement :

```bash
kubectl run pwn --rm -it --image=postgres:16-alpine -- sh
# psql -h postgres-0.postgres-svc -U guestbook -d guestbook
# (mot de passe : ChangeMe_inTP2!)
# SELECT * FROM messages;
# exit
```

![Avant NetworkPolicy — le pod intrus pwn peut accéder à Postgres](../../Image/TP2/Bloc6/23_avant_network_policy.png)

Constat : aucune isolation par défaut. Tout pod dans le namespace peut interroger la base de données — comportement dangereux en environnement multi-tenant.

### Étape 6.3 — Default-deny et rupture volontaire

**`60-default-deny.yaml`** — bloque tout le trafic entrant et sortant sur tous les pods du namespace :

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
```

```bash
kubectl apply -f 60-default-deny.yaml
curl -k https://guestbook.labo.local/   # → timeout ou 502
```

![Après default-deny — le livre d'or est cassé (502)](../../Image/TP2/Bloc6/24_default_deny.png)

Le livre d'or est intentionnellement cassé : c'est le moment pédagogique qui montre que sans règles d'autorisation, rien ne passe — y compris la résolution DNS (port 53/UDP).

### Étape 6.4 — Règles d'autorisation ciblées

**`61-allow-rules.yaml`** contient 6 NetworkPolicies distinctes :

| Policy | Effet |
|---|---|
| `allow-dns` | Autorise tout pod à atteindre kube-dns (UDP 53) — indispensable |
| `allow-ingress-to-frontend` | Traefik peut atteindre le frontend (port 80) |
| `allow-front-to-back` | Frontend peut atteindre le backend (port 5000) |
| `allow-back-to-postgres` | Backend peut atteindre Postgres (port 5432) |
| `allow-front-egress` | Frontend peut sortir vers le backend |
| `allow-back-egress` | Backend peut sortir vers Postgres |

```yaml
# Extrait — policy DNS (sans elle, tout reste cassé même après allow)
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress:
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
      podSelector:
        matchLabels:
          k8s-app: kube-dns
    ports:
    - protocol: UDP
      port: 53
```

```bash
kubectl apply -f 61-allow-rules.yaml
curl -k https://guestbook.labo.local/   # → à nouveau OK
```

![Après allow-rules — livre d'or fonctionnel avec NetworkPolicies actives](../../Image/TP2/Bloc6/25_allow_rules.png)

### Étape 6.5 — Vérification de l'isolation

```bash
kubectl run pwn --rm -it --image=postgres:16-alpine -- sh
# psql -h postgres-0.postgres-svc -U guestbook -d guestbook
# → connection timeout : la NetworkPolicy bloque le pod intrus
```

![Pod intrus pwn bloqué — timeout à la connexion Postgres](../../Image/TP2/Bloc6/26_pod_intrus_bloque.png)

Le pod `pwn` n'a pas le label `tier=back`, donc aucune policy ne l'autorise à joindre Postgres sur le port 5432. La protection est effective.

> **Piège critique** : la policy `allow-dns` est la première à appliquer après le *default-deny*. Sans elle, les pods ne résolvent plus aucun nom DNS et l'application reste cassée même quand toutes les autres rules sont correctes.

✅ **Checkpoint 6** : le livre d'or fonctionne avec les NetworkPolicies actives. Un pod sans label `tier=back` ne peut plus joindre Postgres.

---

## Bloc 7 — Défis ouverts

### Défi A — Init container et migration SQL

#### Objectif
Ajouter un `initContainer` au Deployment backend qui exécute une migration SQL (création d'un index sur `created_at`) avant le démarrage du conteneur principal. L'init container garantit que la migration est appliquée avant que l'application ne commence à recevoir des requêtes.

#### Concept

Un `initContainer` s'exécute à sa completion avant le démarrage des conteneurs du pod. S'il échoue, Kubernetes redémarre le pod — ce qui en fait un outil fiable pour les migrations de schéma.

#### YAML — extrait de `defis/defi-A.yaml`

```yaml
spec:
  initContainers:
  - name: migrate
    image: postgres:16-alpine
    env:
    - name: PGPASSWORD
      valueFrom:
        secretKeyRef: { name: postgres-credentials, key: POSTGRES_PASSWORD }
    command:
    - sh
    - -c
    - |
      until pg_isready -h postgres-0.postgres-svc -U guestbook; do
        echo "Waiting for postgres..."; sleep 2
      done
      psql -h postgres-0.postgres-svc -U guestbook -d guestbook -c \
        "CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);"
      echo "Migration done."
  containers:
  - name: backend
    image: docker.io/mdprogra/webapp-backend:v2.0
    # ... (identique à 40-backend-v2.yaml)
```

#### Vérification

```bash
kubectl apply -f defis/defi-A.yaml
kubectl get pods -l tier=back -w
# → Init:0/1 pendant l'exécution de la migration, puis Running
kubectl logs <pod-backend> -c migrate
```

![Pod backend en Init:0/1 — init container de migration en cours](../../Image/TP2/Bloc7/27_defi_A_init.png)

![Logs du migrate initContainer — migration SQL exécutée avec succès](../../Image/TP2/Bloc7/28_defi_A_migration_logs.png)

✅ **Défi A validé** : l'index `idx_messages_created_at` est créé avant le démarrage du backend. En cas d'échec de la migration (Postgres indisponible), le pod reste en `Init:CrashLoopBackOff` et le conteneur principal ne démarre pas — comportement de sécurité souhaité.

---

### Défi C — Ingress path-based multi-app

#### Objectif
Déployer une seconde application (`nginx` servant une page « admin ») et configurer l'Ingress pour router `/admin/` vers cette nouvelle app, tandis que `/` continue de pointer vers le frontend principal — le tout sur le même nom d'hôte `guestbook.labo.local`.

#### Déploiement de l'app admin

```yaml
# defis/defi-C-admin.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: admin-app
spec:
  replicas: 1
  selector:
    matchLabels: { app: admin }
  template:
    metadata:
      labels: { app: admin }
    spec:
      containers:
      - name: nginx
        image: nginx:1.27-alpine
        ports:
        - containerPort: 80
        volumeMounts:
        - name: html
          mountPath: /usr/share/nginx/html
      volumes:
      - name: html
        configMap:
          name: admin-html
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: admin-html
data:
  index.html: |
    <html><body><h1>Interface Admin</h1><p>Espace réservé.</p></body></html>
---
apiVersion: v1
kind: Service
metadata:
  name: admin-svc
spec:
  selector: { app: admin }
  ports:
  - port: 80
    targetPort: 80
```

#### Ingress path-based

**`defis/defi-C-ingress.yaml`** — deux `paths` sur le même `host` :

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: webapp-ingress
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web,websecure
spec:
  tls:
  - hosts: [guestbook.labo.local]
    secretName: guestbook-tls
  rules:
  - host: guestbook.labo.local
    http:
      paths:
      - path: /admin/
        pathType: Prefix
        backend:
          service:
            name: admin-svc
            port:
              number: 80
      - path: /
        pathType: Prefix
        backend:
          service:
            name: frontend-svc
            port:
              number: 80
```

> **Ordre des paths** : Traefik évalue les rules du plus spécifique au moins spécifique. `/admin/` doit apparaître **avant** `/` pour être correctement matché.

```bash
kubectl apply -f defis/defi-C-admin.yaml
kubectl apply -f defis/defi-C-ingress.yaml
curl -k https://guestbook.labo.local/admin/
curl -k https://guestbook.labo.local/
```

![Ingress path-based — deux backends sur le même host](../../Image/TP2/Bloc7/29_defi_C_ingress.png)

![Navigateur — /admin/ sert la page admin, / sert le livre d'or](../../Image/TP2/Bloc7/30_defi_C_navigateur.png)

✅ **Défi C validé** : `https://guestbook.labo.local/admin/` route vers l'app admin et `https://guestbook.labo.local/` continue de servir le livre d'or. Les deux coexistent sur le même Ingress et le même certificat TLS.

---

## Synthèse des commandes TP2

| Commande | Description |
|---|---|
| `kubectl create namespace <ns>` | Créer un namespace |
| `kubectl config set-context --current --namespace=<ns>` | Changer de namespace courant |
| `kubectl create configmap <nom> --from-literal=K=V` | ConfigMap impératif |
| `kubectl get cm,secret` | Lister ConfigMaps et Secrets |
| `kubectl get sc` | Lister les StorageClasses |
| `kubectl get pv,pvc` | Lister PersistentVolumes et Claims |
| `kubectl get sts` | Lister les StatefulSets |
| `kubectl exec -it postgres-0 -- psql -U guestbook -d guestbook` | Console psql dans postgres-0 |
| `kubectl rollout restart deploy/<nom>` | Forcer le redémarrage d'un Deployment |
| `kubectl get ingress` | Lister les Ingress |
| `kubectl describe ingress <nom>` | Détails et events d'un Ingress |
| `kubectl get networkpolicy` | Lister les NetworkPolicies |

---

## Pièges rencontrés — TP2

| Symptôme | Cause | Résolution |
|---|---|---|
| `initdb` échoue dans postgres-0 | `subPath` manquant — répertoire mountpoint non vide | Ajouter `subPath: pgdata` dans `volumeMounts` |
| `DATABASE_URL` non interpolée, backend en mode mémoire | Ordre des `env` incorrect | Définir `DB_USER`, `DB_PASS`, `DB_NAME` **avant** `DATABASE_URL` |
| PVC en `Pending` éternel | `WaitForFirstConsumer` — aucun pod ne réclame encore le volume | Normal ; vérifier le nom exact de la StorageClass (`local-path`) |
| PVC orphelins après `kubectl delete sts` | Les PVC ne sont pas supprimés automatiquement par le StatefulSet | `kubectl delete pvc data-postgres-0` à la main |
| 404 sur l'Ingress | Mauvais `host` ou DNS non configuré dans `/etc/hosts` | Tester avec `curl -H "Host: guestbook.labo.local"`, vérifier `kubectl describe ingress` |
| HTTPS ne fonctionne pas | Secret TLS dans un namespace différent de l'Ingress | Créer le Secret dans le même namespace que l'Ingress |
| Tout cassé après default-deny | DNS bloqué (port 53/UDP vers kube-dns) | Appliquer `allow-dns` en **premier** |
| Pod intrus toujours connecté après NetworkPolicy | Labels du pod ne matchent pas les selectors | Vérifier les labels avec `kubectl get pod --show-labels` |

---
---

# Compte Rendu — TP3 Kubernetes
**Binôme : Corentin GODON & Matthias DAUVEL**
**Module : Arthur BARADEL — KUBERNETES**
**Date : 11 Mai 2026**

---

## Pré-requis — Image backend v3.0

### Contexte

Le TP2 instrumentait le backend avec Postgres et une `DATABASE_URL`. Pour le TP3, le backend doit exposer des métriques Prometheus via un endpoint `/metrics`, afin d'être scrapé par la stack de monitoring.

### Modifications apportées

**`backend/requirements.txt`** — Ajout de `prometheus-client` :

```
flask==3.0.3
psycopg2-binary==2.9.9
prometheus-client==0.20.0
```

**`backend/app.py`** — Ajout de l'instrumentation :
- `Counter` `guestbook_messages_total` : incrémenté à chaque POST réussi
- `Histogram` `guestbook_request_seconds` : chronométre chaque requête par endpoint via `@app.before_request` / `@app.after_request`
- Route `/metrics` qui sert les métriques au format Prometheus

### Build et push

```powershell
$env:DOCKERHUB_USER = "mdprogra"

docker buildx build --platform linux/amd64,linux/arm64 `
  -t docker.io/$env:DOCKERHUB_USER/webapp-backend:v3.0 `
  ./backend --push --no-cache
```

> **Note** : le cluster k3s est composé de nœuds hétérogènes (`godon-k3s-agent-1` en amd64, `godon-k3s-agent-2` en arm64). Une image buildée pour une seule architecture provoque une erreur `no match for platform in manifest` sur les nœuds de l'autre architecture. L'option `--platform linux/amd64,linux/arm64` de `docker buildx` est donc obligatoire pour produire une image multi-arch compatible avec l'ensemble du cluster.

![Image webapp-backend:v3.0 publiée sur Docker Hub](../../Image/TP3/PreTP/00_docker_hub_v3.png)

✅ **Pré-requis validé** : l'image `mdprogra/webapp-backend:v3.0` est publiée sur Docker Hub avec l'endpoint `/metrics` opérationnel.

---

## Bloc 1 — Helm

### Objectif
Packager l'intégralité de l'application en chart Helm pour remplacer les `kubectl apply` manuels par un déploiement paramétrable, versionné et rollbackable.

### Étape 1.1 — Installation et premiers pas

```bash
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
helm search repo redis
```

Un chart Helm est structuré en trois parties : `Chart.yaml` (métadonnées), `values.yaml` (paramètres par défaut), `templates/` (manifests YAML templatés avec le moteur Go). Helm est à Kubernetes ce que `apt` est à Debian — un gestionnaire de paquets.

### Étape 1.2 — Installer un chart public

Exercice d'échauffement sur Redis pour maîtriser le geste avant de créer notre propre chart :

```bash
kubectl create namespace helm-demo
helm install demo-redis bitnami/redis \
  --namespace helm-demo \
  --set auth.password=demo123 \
  --set master.persistence.size=200Mi \
  --set replica.replicaCount=1
helm list -n helm-demo
kubectl get pod -n helm-demo
helm uninstall demo-redis -n helm-demo
kubectl delete namespace helm-demo
```

![helm search repo redis — charts Redis disponibles dans Bitnami](../../Image/TP3/Bloc1/01_helm_redis.png)

### Étape 1.3 — Squelette et structure du chart

```bash
helm create webapp-chart
```

On vide les templates générés automatiquement et on reconstruit depuis les YAML du TP2. Structure finale :

```
webapp-chart/
├── Chart.yaml
├── values.yaml
├── values-dev.yaml
└── templates/
    ├── _helpers.tpl
    ├── configmap.yaml
    ├── secret.yaml
    ├── frontend-deployment.yaml
    ├── frontend-service.yaml
    ├── backend-deployment.yaml
    ├── backend-service.yaml
    ├── postgres-statefulset.yaml
    ├── ingress.yaml
    ├── servicemonitor.yaml
    └── backend-hpa.yaml
```

**`Chart.yaml`** :

```yaml
apiVersion: v2
name: webapp-chart
description: Livre d'or k8s — chart Helm pédagogique
type: application
version: 0.1.0
appVersion: "3.0"
```

**`values.yaml`** :

```yaml
global:
  registry: docker.io/mdprogra
  imagePullPolicy: IfNotPresent

frontend:
  image: webapp-frontend
  tag: v1.2
  replicas: 2
  resources:
    requests: { cpu: 50m, memory: 64Mi }
    limits:   { cpu: 200m, memory: 128Mi }

backend:
  image: webapp-backend
  tag: v3.0
  replicas: 2
  appEnv: production
  welcomeMessage: "Bienvenue !"
  resources:
    requests: { cpu: 50m, memory: 64Mi }
    limits:   { cpu: 200m, memory: 256Mi }

postgres:
  enabled: true
  image: postgres:16-alpine
  storageSize: 1Gi
  user: guestbook
  password: ChangeMe_inTP3!
  database: guestbook

ingress:
  enabled: true
  host: guestbook.labo.local
  tls:
    enabled: true
    secretName: guestbook-tls
```

**`values-dev.yaml`** (override pour l'env de dev) :

```yaml
backend:
  replicas: 1
  appEnv: dev
frontend:
  replicas: 1
ingress:
  host: dev.guestbook.labo.local
  tls:
    enabled: false
```

**`templates/_helpers.tpl`** — fonctions réutilisables :

```yaml
{{- define "webapp.backendImage" -}}
{{ .Values.global.registry }}/{{ .Values.backend.image }}:{{ .Values.backend.tag }}
{{- end }}

{{- define "webapp.frontendImage" -}}
{{ .Values.global.registry }}/{{ .Values.frontend.image }}:{{ .Values.frontend.tag }}
{{- end }}
```

**`templates/backend-deployment.yaml`** (extrait) :

```yaml
spec:
  replicas: {{ .Values.backend.replicas }}
  template:
    spec:
      containers:
      - name: backend
        image: {{ include "webapp.backendImage" . }}
        {{- if .Values.postgres.enabled }}
        - name: DATABASE_URL
          value: "postgresql://$(DB_USER):$(DB_PASS)@postgres-0.postgres-svc:5432/$(DB_NAME)"
        {{- end }}
        resources:
          {{- toYaml .Values.backend.resources | nindent 10 }}
```

### Étape 1.4 — Lint, template, install, upgrade, rollback

```bash
# Vérification statique
helm lint webapp-chart

# Rendu sans déploiement
helm template webapp-chart --values webapp-chart/values-dev.yaml | less

# Installation complète
helm install gb webapp-chart \
  --namespace guestbook --create-namespace \
  --values webapp-chart/values-dev.yaml
helm list -n guestbook
helm get values gb -n guestbook
```

![helm install gb — release installée, tous les pods Running](../../Image/TP3/Bloc1/02_helm_install.png)

```bash
# Modifier replicas dans values, puis upgrade
helm upgrade gb webapp-chart -n guestbook --values webapp-chart/values-dev.yaml
helm history gb -n guestbook

# Rollback à la révision 1
helm rollback gb 1 -n guestbook
```

![helm history gb — historique des révisions avec install, upgrade et rollback](../../Image/TP3/Bloc1/03_helm_history.png)

> **Piège rencontré** : `nindent` et `indent` sont distincts — `nindent` ajoute un saut de ligne avant le bloc indenté, ce qui est nécessaire quand on insère un bloc YAML sous une clé. L'oubli provoque une erreur de parsing silencieuse détectée uniquement via `helm template --debug`.

> **Piège rencontré** : tenter un second `helm install` sur le même `release name` retourne `cannot re-use a name that is still in use`. Il faut soit `helm upgrade`, soit `helm uninstall` préalablement.

✅ **Checkpoint 1** : `helm install gb` déploie l'application complète en une commande. `values-dev.yaml` produit une configuration différente de `values.yaml`. `helm rollback` ramène à la révision précédente.

---

## Bloc 2 — Monitoring : Prometheus + Grafana

### Objectif
Mettre en place la stack `kube-prometheus-stack`, instrumenter le backend avec des métriques custom, et créer un dashboard Grafana qui visualise l'activité du livre d'or.

### Étape 2.1 — Installer kube-prometheus-stack

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

kubectl create namespace monitoring

helm install kps prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --set grafana.adminPassword=admin \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set alertmanager.enabled=false \
  --set prometheus.prometheusSpec.resources.requests.memory=400Mi

kubectl get pods -n monitoring
kubectl get svc -n monitoring
```

L'option `serviceMonitorSelectorNilUsesHelmValues=false` est cruciale : sans elle, Prometheus n'écoute que les ServiceMonitors portant son propre label de release et ignore tous ceux que nous allons créer dans `guestbook`.

![kube-prometheus-stack — pods monitoring en Running](../../Image/TP3/Bloc2/04_kps_pods.png)

### Étape 2.2 — Accès à Grafana et Prometheus

```bash
kubectl port-forward -n monitoring svc/kps-grafana 3000:80 --address 0.0.0.0 &
kubectl port-forward -n monitoring svc/kps-kube-prometheus-stack-prometheus 9090:9090 --address 0.0.0.0 &
```

Accès depuis le poste local via tunnel SSH :

```bash
ssh -N -L 3000:localhost:3000 -L 9090:localhost:9090 ubuntu@212.47.230.56
```

- Grafana : `http://localhost:3000` — login `admin` / `admin`
- Prometheus : `http://localhost:9090`

![Dashboard Grafana — Kubernetes / Compute Resources / Cluster](../../Image/TP3/Bloc2/05_grafana_dashboard.png)

### Étape 2.3 — ServiceMonitor et vérification du scraping

Ajout de `templates/servicemonitor.yaml` dans le chart. Le label `release: kps` est obligatoire pour que le Prometheus de kube-prometheus-stack accepte ce ServiceMonitor :

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: backend-metrics
  labels:
    release: kps
spec:
  selector:
    matchLabels:
      app: webapp
      tier: back
  endpoints:
  - port: http
    path: /metrics
    interval: 15s
  namespaceSelector:
    matchNames:
    - guestbook
```

Le port du Service backend doit être **nommé** (`name: http`) pour que la spec ServiceMonitor puisse le référencer, et les labels `app: webapp` / `tier: back` doivent être présents sur le Service :

```yaml
# Dans backend-service.yaml
metadata:
  labels:
    app: webapp
    tier: back
ports:
- port: 5000
  targetPort: 5000
  name: http
```

```bash
helm upgrade gb webapp-chart -n guestbook --values webapp-chart/values.yaml
kubectl get servicemonitor -n guestbook

# Vérification de l'endpoint /metrics
kubectl exec -n guestbook deploy/backend -- python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:5000/metrics').read().decode())" | head -20
```

![Prometheus Targets — serviceMonitor/guestbook/backend-metrics/0 en UP](../../Image/TP3/Bloc2/06_prometheus_target.png)

### Étape 2.4 — Requêtes PromQL et dashboard custom

Requêtes testées dans l'UI Prometheus :

```promql
guestbook_messages_total
rate(guestbook_request_seconds_count[1m])
histogram_quantile(0.95, sum(rate(guestbook_request_seconds_bucket[5m])) by (le, endpoint))
```

Dashboard Grafana créé avec trois panels :
- **Panel 1** — `guestbook_messages_total` : Time series, nombre cumulé de messages postés
- **Panel 2** — `rate(guestbook_request_seconds_count[1m])` : Time series par endpoint, trafic en temps réel
- **Panel 3** — `histogram_quantile(0.95, ...)` : p95 de latence par endpoint

![Dashboard Grafana custom — métriques du livre d'or en temps réel](../../Image/TP3/Bloc2/07_grafana_custom.png)

> **Piège rencontré** : le ServiceMonitor était bien créé mais Prometheus ne le détectait pas. Deux causes combinées : le label `release: kps` était absent, et les labels `app: webapp` / `tier: back` n'étaient pas présents sur le Service backend (Helm ne les injecte pas automatiquement). Vérifiable via `kubectl describe servicemonitor backend-metrics` et Prometheus UI → Status → Targets.

✅ **Checkpoint 2** : Prometheus scrape le backend (`UP` dans Targets). Métrique `guestbook_messages_total` interrogeable. Dashboard Grafana custom opérationnel avec les 3 panels.

---

## Bloc 3 — HorizontalPodAutoscaler

### Objectif
Configurer un HPA sur le Deployment backend pour qu'il scale automatiquement entre 2 et 8 réplicas selon l'utilisation CPU, et le valider sous charge artificielle.

### Étape 3.1 — Vérification de metrics-server

k3s embarque `metrics-server` par défaut :

```bash
kubectl top nodes
kubectl top pods -n guestbook
```

![kubectl top — consommation CPU/RAM des pods](../../Image/TP3/Bloc3/08_kubectl_top.png)

### Étape 3.2 — HPA sur CPU

Ajout de `templates/backend-hpa.yaml` dans le chart :

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: backend-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: backend
  minReplicas: 2
  maxReplicas: 8
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 60
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 60
    scaleUp:
      stabilizationWindowSeconds: 0
      policies:
      - type: Percent
        value: 100
        periodSeconds: 30
```

Le HPA nécessite que le Deployment ait des `resources.requests.cpu` définis — sans quoi il affiche `<unknown>/60%` et ne scale jamais.

```bash
helm upgrade gb webapp-chart -n guestbook --values webapp-chart/values.yaml
kubectl get hpa -n guestbook
kubectl describe hpa backend-hpa -n guestbook
```

![HPA créé — 2/2 réplicas, CPU cible à 60%](../../Image/TP3/Bloc3/09_hpa_initial.png)

### Étape 3.3 — Test de charge

L'image `williamyeh/hey` n'étant pas compatible arm64, on utilise un Job Python natif :

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: load-test
  namespace: guestbook
spec:
  parallelism: 5
  completions: 5
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: load
        image: python:3.12-alpine
        command: ["python3", "-c"]
        args:
          - |
            import urllib.request, threading, time
            def worker():
                end = time.time() + 300
                while time.time() < end:
                    try:
                        urllib.request.urlopen('http://backend-svc:5000/api/messages', timeout=2)
                    except:
                        pass
            threads = [threading.Thread(target=worker) for _ in range(50)]
            [t.start() for t in threads]
            [t.join() for t in threads]
```

```bash
kubectl apply -f load-test.yaml
```

Observation en temps réel dans deux terminaux séparés :

```bash
# Terminal 1
watch -n2 kubectl get hpa,deploy,pod -n guestbook

# Terminal 2
kubectl top pods -n guestbook
```

Séquence observée :
1. CPU backend monte au-dessus de 60%
2. HPA déclenche le scale-up : 2 → 4 → 8 réplicas
3. Sur Grafana, `rate(guestbook_request_seconds_count[1m])` explose
4. Après suppression du Job, la charge chute
5. Après 60s de stabilisation, scale-down à 2 réplicas

```bash
kubectl delete job load-test -n guestbook
```

![HPA scale-up sous charge — 8 réplicas actifs, CPU à 235%](../../Image/TP3/Bloc3/10_hpa_scaleup.png)

![Grafana — pic de trafic corrélé au scale-up HPA](../../Image/TP3/Bloc3/11_grafana_charge.png)

![HPA scale-down après charge — retour à 2 réplicas](../../Image/TP3/Bloc3/12_hpa_scaledown.png)

> **Piège rencontré** : le HPA affichait `<unknown>/60%` au démarrage. Cause : les `resources.requests.cpu` n'étaient pas définis dans le template backend du chart. Ajouté dans `values.yaml` sous `backend.resources.requests.cpu: 50m`, puis `helm upgrade`.

> **Piège rencontré** : l'image `williamyeh/hey` utilisée comme générateur de charge n'est pas compatible arm64. Remplacée par un Job Python utilisant `urllib.request` et `threading`, disponible nativement sur toutes les architectures.

✅ **Checkpoint 3** : HPA passe le backend de 2 à 8 réplicas sous charge, visible dans `kubectl get hpa` et Grafana. Retour à 2 réplicas après la fenêtre de stabilisation de 60s.

---

## Bloc 4 — Pipeline CI/CD

### Objectif
Automatiser le cycle build → test → deploy via un pipeline GitHub Actions, de sorte qu'un `git push` sur `master` déclenche automatiquement un `helm upgrade` sur le cluster.

### Étape 4.1 — Architecture

```
[git push]
    │
    ├─ build-backend  : docker buildx build & push (multi-arch amd64+arm64)
    ├─ build-frontend : docker buildx build & push (multi-arch amd64+arm64)
    ├─ helm-lint      : helm lint + helm template
    └─ deploy         : helm upgrade --install → cluster k3s
```

### Étape 4.2 — ServiceAccount dédié CI

Pour ne pas injecter un kubeconfig admin dans la CI, on crée un ServiceAccount `ci-deployer` limité au namespace `guestbook` :

```bash
kubectl apply -f ci-rbac.yaml
TOKEN=$(kubectl get secret ci-deployer-token -n guestbook -o jsonpath='{.data.token}' | base64 -d)
CA=$(kubectl get secret ci-deployer-token -n guestbook -o jsonpath='{.data.ca\.crt}')
```

Construction du kubeconfig CI avec l'IP publique du serveur et encodage :

```bash
cat > kubeconfig-ci.yaml <<EOF
apiVersion: v1
kind: Config
clusters:
- name: k3s
  cluster:
    server: https://212.47.230.56:6443
    certificate-authority-data: ${CA}
users:
- name: ci
  user:
    token: ${TOKEN}
contexts:
- name: ci@k3s
  context:
    cluster: k3s
    user: ci
    namespace: guestbook
current-context: ci@k3s
EOF

base64 -w 0 kubeconfig-ci.yaml
# → Copier dans GitHub Settings → Secrets and variables → Actions → KUBECONFIG_B64
```

![Secrets CI configurés dans GitHub Actions — KUBECONFIG_B64, DOCKERHUB_USER, DOCKERHUB_TOKEN](../../Image/TP3/Bloc4/13_gitlab_vars.png)

### Étape 4.3 — Le `.github/workflows/deploy.yml`

```yaml
name: CI/CD

on:
  push:
    branches: [ master ]

permissions:
  contents: read

env:
  IMAGE_BACKEND: docker.io/${{ secrets.DOCKERHUB_USER }}/webapp-backend
  IMAGE_FRONTEND: docker.io/${{ secrets.DOCKERHUB_USER }}/webapp-frontend

jobs:
  build-backend:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    - name: Login Docker Hub
      run: echo "${{ secrets.DOCKERHUB_TOKEN }}" | docker login -u "${{ secrets.DOCKERHUB_USER }}" --password-stdin
    - name: Build & push backend
      run: |
        docker buildx create --use
        docker buildx build \
          --platform linux/amd64,linux/arm64 \
          -t ${{ env.IMAGE_BACKEND }}:${{ github.sha }} \
          --push ./TP/webapp/backend

  build-frontend:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    - name: Login Docker Hub
      run: echo "${{ secrets.DOCKERHUB_TOKEN }}" | docker login -u "${{ secrets.DOCKERHUB_USER }}" --password-stdin
    - name: Build & push frontend
      run: |
        docker buildx create --use
        docker buildx build \
          --platform linux/amd64,linux/arm64 \
          -t ${{ env.IMAGE_FRONTEND }}:${{ github.sha }} \
          --push ./TP/webapp/frontend

  helm-lint:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    - name: Install Helm
      run: curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
    - name: Lint
      run: |
        helm lint TP/webapp-chart
        helm template TP/webapp-chart --values TP/webapp-chart/values.yaml > /tmp/rendered.yaml
        wc -l /tmp/rendered.yaml

  deploy:
    runs-on: ubuntu-latest
    needs: [build-backend, build-frontend, helm-lint]
    steps:
    - uses: actions/checkout@v4
    - name: Install Helm
      run: curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
    - name: Setup kubeconfig
      run: |
        mkdir -p ~/.kube
        echo "${{ secrets.KUBECONFIG_B64 }}" | base64 -d > ~/.kube/config
        chmod 600 ~/.kube/config
    - name: Deploy
      run: |
        helm upgrade --install gb TP/webapp-chart \
          --namespace guestbook \
          --values TP/webapp-chart/values.yaml \
          --set global.registry=docker.io/${{ secrets.DOCKERHUB_USER }} \
          --set backend.tag=${{ github.sha }} \
          --set frontend.tag=${{ github.sha }} \
          --wait --timeout 5m
```

### Étape 4.4 — Démonstration du pipeline

On modifie le message de bienvenue dans `backend/app.py` et on pousse sur `master`. Séquence observée dans GitHub Actions :

1. `build-backend` → image taguée avec le SHA du commit, poussée sur Docker Hub (multi-arch)
2. `helm-lint` → succès
3. `deploy` → `helm upgrade`, rollout OK, livre d'or accessible avec la nouvelle image

```bash
# Vérification post-déploiement
kubectl get pods -n guestbook
kubectl describe deploy/backend -n guestbook | grep Image
```

![Pipeline GitHub Actions — 4 jobs verts sur push](../../Image/TP3/Bloc4/14_pipeline_vert.png)

![Docker Hub — image taguée avec le SHA du commit](../../Image/TP3/Bloc4/15_dockerhub_sha.png)

![Livre d'or — nouvelle version déployée automatiquement](../../Image/TP3/Bloc4/16_app_deployee.png)

> **Piège rencontré** : `helm upgrade` échouait avec `Kubernetes cluster unreachable: https://127.0.0.1:6443`. Le kubeconfig généré automatiquement via `kubectl config view` contenait `127.0.0.1` au lieu de l'IP publique. Solution : construire le kubeconfig manuellement avec `server: https://212.47.230.56:6443`.

> **Bonne pratique** : ne jamais utiliser le mot de passe Docker Hub directement — utiliser un **PAT (Personal Access Token)** révocable créé sur hub.docker.com. Les secrets CI (`KUBECONFIG_B64`, `DOCKERHUB_TOKEN`) doivent être **masked** ET **protected** dans GitHub Actions.

✅ **Checkpoint 4** : Pipeline GitHub Actions vert sur push. Image avec SHA visible sur Docker Hub. Application redéployée automatiquement, vérifiable via le navigateur.

---

## Synthèse des commandes TP3

| Commande | Description |
|---|---|
| `helm repo add <nom> <url>` | Ajouter un dépôt de charts |
| `helm search repo <terme>` | Chercher un chart dans les dépôts |
| `helm install <release> <chart> --values <file>` | Installer une release |
| `helm upgrade <release> <chart> -n <ns>` | Mettre à jour une release |
| `helm rollback <release> <revision> -n <ns>` | Revenir à une révision |
| `helm history <release> -n <ns>` | Historique des révisions |
| `helm lint <chart>` | Vérification statique du chart |
| `helm template <chart> --values <file>` | Rendu sans déploiement |
| `helm uninstall <release> -n <ns>` | Supprimer une release |
| `kubectl top nodes` | Consommation CPU/RAM des nœuds |
| `kubectl top pods -n <ns>` | Consommation CPU/RAM des pods |
| `kubectl get hpa -n <ns>` | État de l'autoscaler |
| `kubectl describe hpa <nom> -n <ns>` | Détails et events du HPA |

---

## Pièges rencontrés — TP3

| Symptôme | Cause | Résolution |
|---|---|---|
| `cannot re-use a name that is still in use` | `helm install` sur une release existante | Utiliser `helm upgrade` ou `helm uninstall` d'abord |
| Templates Helm cassés silencieusement | `nindent` vs `indent`, quotes oubliées | `helm template --debug` pour voir l'erreur exacte |
| Prometheus n'a pas le target backend | Label `release: kps` absent du ServiceMonitor | Ajouter `release: kps` dans les labels du ServiceMonitor |
| ServiceMonitor ignoré | Port du Service non nommé ou labels manquants sur le Service | Ajouter `name: http` sur le port et les labels `app/tier` sur le Service |
| HPA bloqué à `<unknown>/60%` | `resources.requests.cpu` absent du Deployment | Définir `requests.cpu` dans `values.yaml` |
| `ErrImagePull` — `no match for platform in manifest` | Cluster multi-arch (amd64 + arm64) — image buildée pour une seule plateforme | Utiliser `docker buildx build --platform linux/amd64,linux/arm64 --push` |
| Job de charge sans effet (image incompatible arm64) | `williamyeh/hey` non disponible sur arm64 | Remplacer par un Job Python avec `urllib.request` et `threading` |
| Pipeline `kubectl: connection refused` sur `127.0.0.1` | Kubeconfig généré avec l'IP locale au lieu de l'IP publique | Construire le kubeconfig manuellement avec `server: https://212.47.230.56:6443` |
| `helm upgrade` timeout en CI | Readiness probe trop serrée, Postgres lent | Augmenter `initialDelaySeconds`, `--timeout 5m` |
| `docker push` denied en CI | Mauvais PAT Docker Hub ou `DOCKERHUB_USER` incorrect | Vérifier les secrets GitHub Actions, utiliser un PAT révocable |

---
---

# Compte Rendu — TP4 Kubernetes
**Binôme : Corentin GODON & Matthias DAUVEL**
**Module : Arthur BARADEL — KUBERNETES**
**Date : 18 Mai 2026**

---

## Bloc 1 — Setup Scaleway

### Objectif
Configurer l'environnement Scaleway : création du projet, génération d'une clé API IAM, installation et configuration de la CLI `scw`.

### Étape 1.1 — Compte et projet

Sur la console Scaleway :
1. Création du compte (carte bancaire requise).
2. `Organization → Projects` : création du projet `tp4-k8s-dauvel`.
3. `IAM → API Keys` : création d'une clé API liée au projet. Access Key et Secret Key notées (Secret Key affichée une seule fois).

### Étape 1.2 — CLI Scaleway

```bash
# Installation Linux
curl -fsSL https://raw.githubusercontent.com/scaleway/scaleway-cli/master/scripts/get.sh | sh

scw init
# Région : fr-par, Zone : fr-par-1, clés API saisies, project ID renseigné
scw info
```

La commande `scw info` retourne l'organisation et le projet configurés, confirmant que la CLI est correctement authentifiée.

![scw info — organisation et projet attendus confirmés](../../Image/TP4/Bloc1/01_scw_info.png)

✅ **Checkpoint 1** : `scw info` affiche l'organisation et le projet `tp4-k8s-dauvel` attendus.

---

## Bloc 2 — Création du cluster Kapsule multi-AZ

### Objectif
Provisionner un cluster Kubernetes managé Kapsule multi-AZ avec deux pools de nœuds dans deux zones de disponibilité différentes (`fr-par-1` et `fr-par-2`).

### Étape 2.1 — Lister les options disponibles

```bash
scw k8s version list region=fr-par
scw instance server-type list zone=fr-par-1 | grep -E "DEV1|GP1"
```

Le type `DEV1-M` (3 vCPU, 4 Go RAM, ~0,02 €/h) est sélectionné comme instance la moins chère éligible.

### Étape 2.2 — Création multi-AZ

Un seul cluster avec un seul control plane, deux pools dans deux AZ différentes :

```bash
scw k8s cluster create \
  name=tp4-cluster-dauvel \
  type=kapsule \
  version=1.32.13 \
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

Chaque nœud porte le label `topology.kubernetes.io/zone=fr-par-1` ou `fr-par-2`, confirmant la répartition multi-AZ.

![kubectl get nodes — 3 nœuds Ready répartis sur 2 AZ](../../Image/TP4/Bloc2/02_cluster_nodes.png)

✅ **Checkpoint 2** : 3 nœuds `Ready`, répartis sur `fr-par-1` (2 nœuds) et `fr-par-2` (1 nœud).

---

## Bloc 3 — Premier contact et exploration

### Objectif
Explorer le cluster Kapsule pour identifier les différences structurelles avec le cluster k3s du TP1, et documenter les observations dans `notes-exploration.md`.

### Étape 3.1 — Exploration des composants

```bash
kubectl get nodes -o wide
kubectl get pods -A
kubectl get sc
kubectl cluster-info
kubectl get pods -A | grep -E "apiserver|etcd|scheduler"
```

La dernière commande ne retourne aucun résultat — le control plane est invisible, géré par Scaleway.

![Pods système Kapsule — konnectivity-agent, csi-node, hubble visibles](../../Image/TP4/Bloc3/03_pods_system.png)

![StorageClasses Kapsule — 8 classes basées sur csi.scaleway.com](../../Image/TP4/Bloc3/04_storage_class.png)

### Différences observées (notes-exploration.md)

**1. Control plane invisible**
Sur k3s : `kube-apiserver`, `etcd`, `kube-scheduler`, `kube-controller-manager` tournent sur le nœud server et sont visibles via `kubectl get pods -A`. Sur Kapsule : aucun de ces pods n'est visible — ils sont gérés par Scaleway, accessibles uniquement via l'URL `https://cd95bb1a...api.k8s.fr-par.scw.cloud:6443`.

**2. Pas d'IngressController pré-installé**
Sur k3s : Traefik est installé par défaut. Sur Kapsule : aucun IngressController — il faut installer ingress-nginx manuellement via Helm.

**3. StorageClasses multiples**
Sur k3s : 1 seule StorageClass `local-path` (stockage local sur le nœud). Sur Kapsule : 8 StorageClasses basées sur `csi.scaleway.com` (`scw-bssd`, `sbs-default`, `sbs-5k`, `sbs-15k`, avec variantes `-retain`).

**4. CNI différent**
Sur k3s : Flannel + kube-router. Sur Kapsule : Cilium (avec Hubble pour l'observabilité réseau).

**5. Pods système spécifiques à Kapsule**
- `konnectivity-agent` : tunnel sécurisé entre control plane Scaleway et nœuds
- `csi-node` : driver Block Storage natif Scaleway
- `hubble-generate-certs` : certificats pour l'observabilité Cilium

Sur k3s : aucun de ces composants.

**6. metrics-server pré-installé**
Sur Kapsule comme sur k3s : `metrics-server` est présent par défaut.

✅ **Checkpoint 3** : 6 différences observables identifiées entre Kapsule et k3s.

---

## Bloc 4 — Container Registry Scaleway

### Objectif
Migrer les images Docker du livre d'or de Docker Hub vers le Container Registry Scaleway (SCR), et configurer l'authentification depuis le cluster via un `imagePullSecret`.

### Étape 4.1 — Namespace SCR

```bash
scw registry namespace create \
  name=tp4-dauvel \
  region=fr-par \
  is-public=false

export SCR_ENDPOINT=rg.fr-par.scw.cloud/tp4-dauvel
```

### Étape 4.2 — Push des images

```bash
echo "$SCW_SECRET_KEY" | docker login rg.fr-par.scw.cloud -u nologin --password-stdin

docker tag docker.io/mdprogra/webapp-backend:v2.0 $SCR_ENDPOINT/webapp-backend:v2.0
docker push $SCR_ENDPOINT/webapp-backend:v2.0

docker tag docker.io/mdprogra/webapp-frontend:v1.2 $SCR_ENDPOINT/webapp-frontend:v1.2
docker push $SCR_ENDPOINT/webapp-frontend:v1.2
```

### Étape 4.3 — imagePullSecret

```bash
kubectl create namespace guestbook
kubectl config set-context --current --namespace=guestbook

kubectl create secret docker-registry scw-registry-credentials \
  --docker-server=rg.fr-par.scw.cloud \
  --docker-username=nologin \
  --docker-password=$SCW_SECRET_KEY
```

![Images visibles dans la console SCR — webapp-backend:v2.0 et webapp-frontend:v1.2](../../Image/TP4/Bloc4/05_scr_images.png)

✅ **Checkpoint 4** : Images `webapp-backend:v2.0` et `webapp-frontend:v1.2` visibles dans la console SCR. Secret `scw-registry-credentials` créé dans le namespace `guestbook`.

---

## Bloc 5 — Migration du livre d'or

### Objectif
Adapter les manifests du TP2 pour le cluster Kapsule : nouvelles images SCR, `imagePullSecrets`, et StorageClass Block Storage `scw-bssd`.

### Étape 5.1 — Adaptation des manifests

Trois modifications appliquées aux manifests `tp4/manifests/` (copiés depuis le TP2) :

**1.** Remplacement des images : `docker.io/mdprogra/...` → `rg.fr-par.scw.cloud/tp4-dauvel/...`

**2.** Ajout dans chaque `spec.template.spec` des Deployments :
```yaml
imagePullSecrets:
- name: scw-registry-credentials
```

**3.** Dans le StatefulSet Postgres, changement de la `storageClassName` :
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

### Étape 5.2 — Déploiement

```bash
kubectl apply -f tp4/manifests/
kubectl get pods,pvc,svc
```

![Tous les pods Running — PVC data-postgres-0 Bound sur scw-bssd](../../Image/TP4/Bloc5/06_pods_running.png)

> **Piège rencontré** : le PVC `data-postgres-0` est resté en `Pending` pendant environ 90 secondes avant de passer en `Bound` — le provisionnement Block Storage prend du temps. Patience nécessaire avant de diagnostiquer un problème.

✅ **Checkpoint 5** : Tous les pods en `Running`, PVC `data-postgres-0` en `Bound` sur `scw-bssd`.

---

## Bloc 6 — LoadBalancer + Ingress + cert-manager + Let's Encrypt

### Objectif
Exposer le livre d'or en HTTPS avec un certificat Let's Encrypt **valide** (impossible en TP2 sur k3s) grâce à un vrai Load Balancer Scaleway et un domaine DNS public.

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
# → 51.158.58.244
```

Le Cloud Controller Manager Scaleway provisionne automatiquement un **vrai Load Balancer** visible dans la console Scaleway → Load Balancers.

![Load Balancer Scaleway provisionné automatiquement — IP 51.158.58.244](../../Image/TP4/Bloc6/07_lb_scaleway.png)

### Étape 6.2 — DNS

Enregistrement A `tp4.dauvel.mediaschool-rouen.fr → 51.158.58.244` créé chez le registrar.

```bash
dig +short tp4.dauvel.mediaschool-rouen.fr
# → 51.158.58.244
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

**`60-clusterissuer.yaml`** :

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: corentingodon21@gmail.com
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
    - tp4.dauvel.mediaschool-rouen.fr
    secretName: webapp-tls
  rules:
  - host: tp4.dauvel.mediaschool-rouen.fr
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

Le `Certificate` est passé de `Issuing` à `Ready` en **26 secondes**.

![Certificate Ready — cert-manager a obtenu le certificat Let's Encrypt](../../Image/TP4/Bloc6/08_certificate_ready.png)

![Livre d'or accessible en HTTPS — cadenas vert valide dans le navigateur](../../Image/TP4/Bloc6/09_https_browser.png)

```bash
curl https://tp4.dauvel.mediaschool-rouen.fr/
# → 200 OK, certificat Let's Encrypt valide
```

> **Différence clé avec le TP2** : sur k3s, nous avions un certificat auto-signé car Traefik utilisait un ServiceLB (NodePort déguisé) sans IP publique stable. Let's Encrypt ne peut pas faire sa validation HTTP-01 sur un port non standard. Sur Kapsule, le vrai Load Balancer Scaleway avec une IP publique stable permet la validation HTTP-01 sur le port 80 standard — le certificat est obtenu en quelques secondes.

✅ **Checkpoint 6** : Livre d'or accessible en HTTPS avec certificat Let's Encrypt valide. Cadenas vert sans avertissement dans le navigateur.

---

## Bloc 7 — Stockage persistant Block Storage

### Objectif
Inspecter la StorageClass `scw-bssd` et démontrer que le volume Block Storage suit le pod lors d'un replanification sur un autre nœud — contrairement à `local-path` sur k3s.

### Étape 7.1 — Inspecter la StorageClass

```bash
kubectl get sc
kubectl describe sc scw-bssd
```

Différence fondamentale entre `local-path` (k3s) et `scw-bssd` (Kapsule) :

| Aspect | `local-path` (k3s) | `scw-bssd` (Kapsule) |
|---|---|---|
| Type | Fichier local sur le nœud | Volume distant réseau |
| Si nœud disparaît | Données perdues | Volume rattachable à un autre nœud |
| Multi-AZ | Coincé sur le nœud | Coincé sur l'AZ du volume |
| Snapshots | Non | Oui |
| Coût | 0 € | ~0,10 €/Go/mois |

### Étape 7.2 — Démontrer la portabilité

```bash
kubectl get pod postgres-0 -o wide
# → postgres-0 tourne sur pool-paris-1-abcd (fr-par-1)

kubectl delete pod postgres-0 --force --grace-period=0
kubectl get pod postgres-0 -w
```

Scaleway détache le Block Storage du nœud d'origine et le rattache au nouveau nœud où le pod est replanifié. Les données sont préservées.

![PVC scw-bssd Bound — volume Block Storage provisionné par Scaleway](../../Image/TP4/Bloc7/10_pvc_bound.png)

![Block Storage visible dans la console Scaleway](../../Image/TP4/Bloc7/11_block_storage_scaleway.png)

![Pod postgres-0 replanifié avec données intactes](../../Image/TP4/Bloc7/12_pod_replanifie.png)

> **Limite observée** : le Block Storage est lié à une AZ. Un volume provisionné dans `fr-par-2` ne peut pas être monté depuis un nœud `fr-par-1`. Si tous les nœuds d'une AZ tombent, le pod Postgres ne peut pas être replanifié dans l'autre AZ. Solutions production : réplication applicative (Patroni, Postgres logical replication).

✅ **Checkpoint 7** : Pod `postgres-0` supprimé et replanifié sur un autre nœud avec son volume Block Storage rattaché automatiquement. Données préservées.

---

## Bloc 8 — Cluster Autoscaler en action

### Objectif
Activer l'autoscaling des nœuds sur le pool `pool-paris-1`, déclencher un scale-up via un workload gourmand en CPU, et observer le provisionnement automatique de nouveaux nœuds Scaleway.

### Étape 8.1 — Activer l'autoscaling

```bash
scw k8s pool list cluster-id=$CLUSTER_ID region=fr-par
export POOL_ID=<id-du-pool-paris-1>

scw k8s pool update $POOL_ID region=fr-par \
  autoscaling=true \
  min-size=2 \
  max-size=5
```

### Étape 8.2 — Déploiement de la charge (`80-greedy.yaml`)

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

Trois terminaux parallèles :

```bash
# Pods en attente
watch -n2 'kubectl get pods -n guestbook -l app=greedy'

# Nœuds qui apparaissent
watch -n5 'kubectl get nodes'

# Events autoscaler
kubectl get events -n kube-system --field-selector source=cluster-autoscaler -w
```

Séquence observée :
1. Pods en `Pending` avec `FailedScheduling` — ressources insuffisantes sur les 2 nœuds existants
2. Le Cluster Autoscaler détecte les pods non planifiables et déclenche le scale-up en **moins de 30 secondes**
3. 3 nouveaux nœuds DEV1-M provisionnés côté Scaleway dans `pool-paris-1` (`fr-par-1`)
4. Nœuds passent en `Ready`, pods `greedy` schedulés
5. Pool `pool-paris-1` passe de 2 à 5 nœuds (limite `max-size`)

![Pods greedy en Pending — FailedScheduling déclenche le Cluster Autoscaler](../../Image/TP4/Bloc8/13_pods_pending.png)

![Nouveaux nœuds DEV1-M provisionnés — kubectl get nodes montre 5 nœuds Ready](../../Image/TP4/Bloc8/14_nodes_scaleup.png)

![Console Scaleway — nouveaux nœuds visibles dans le pool pool-paris-1](../../Image/TP4/Bloc8/15_console_autoscaler.png)

### Étape 8.4 — Scale-down

```bash
kubectl delete -f 80-greedy.yaml
```

L'autoscaler attend ~10 min de sous-utilisation avant de supprimer les nœuds excédentaires, retournant à `min-size=2`.

> **Point de vigilance** : oublier de supprimer le Deployment `greedy` maintiendrait le cluster à 5 nœuds et ferait dériver la facture. Toujours nettoyer les workloads de test.

✅ **Checkpoint 8** : Scale-up de 2 à 5 nœuds observé en moins de 3 minutes. Nouveaux nœuds DEV1-M visibles dans la console Scaleway. Pods `Pending` schedulés automatiquement.

---

## Bloc 9 — Multi-AZ vs multi-région (théorique)

### Objectif
Comprendre les limites d'une architecture multi-AZ et les approches pour étendre vers le multi-région.

### Limite : Kapsule est mono-région

Notre cluster `tp4-cluster-dauvel` est multi-AZ (`fr-par-1` + `fr-par-2`). Il résiste à la panne d'une AZ Paris. Mais si **toute la région `fr-par` tombe**, le cluster entier est indisponible. Un cluster Kapsule ne peut pas avoir de nœuds dans plusieurs régions.

### Options pour le multi-région

1. **Deux clusters Kapsule indépendants**, un par région — avec Argo CD multi-cluster pour synchroniser les déploiements
2. **Scaleway Kosmos** — offre cousine permettant un control plane unique acceptant des nœuds de plusieurs régions et même d'autres cloud providers (AWS, GCP, on-premise)

### Ce que multi-AZ couvre et ne couvre pas

**Protège contre :**
- Panne d'un datacenter Scaleway Paris (fr-par-1 ou fr-par-2 isolément)
- Maintenance planifiée d'une AZ (pods replanifiés dans l'autre AZ)
- Panne d'un nœud individuel (autohealing + rescheduling)

**Ne protège pas contre :**
- Panne de toute la région `fr-par` (catastrophe naturelle, panne réseau régionale)
- Corruption des données Postgres (le Block Storage est lié à une AZ — un volume `fr-par-2` ne peut pas être monté depuis `fr-par-1`)
- Erreur humaine (suppression accidentelle du cluster ou des données)

✅ **Checkpoint 9** : Compréhension claire de ce que multi-AZ couvre, et de ce qu'il ne couvre pas. Limites de Kapsule mono-région identifiées, alternatives documentées.

---

## Bloc 10 — Questionnaire de comparaison

> Le questionnaire complet est disponible dans `tp4/comparaison.md`. Les points saillants sont reproduits ci-dessous.

### A. Bootstrap et administration

**Q1 — Nombre de commandes pour un cluster prêt**

k3s nécessitait 5-6 commandes (installation server, récupération token, jonction agents, configuration kubeconfig) sans compter la correction manuelle de l'IP et l'installation des composants supplémentaires. Kapsule se réduit à **2 commandes** : `scw k8s cluster create` + `scw k8s kubeconfig install`. CNI, metrics-server, StorageClasses et autohealing sont livrés préconfigurés.

**Q2 — Localisation du control plane**

k3s : `kube-apiserver`, `etcd`, `kube-scheduler`, `kube-controller-manager` tournent sur `GODON-k3s-server`, visibles via `kubectl get pods -A`. Kapsule : la commande `kubectl get pods -A | grep -E "apiserver|etcd|scheduler"` ne retourne aucun résultat — le control plane est géré par Scaleway sur une infrastructure dédiée.

**Q3 — Impact d'une panne du control plane**

k3s : `sudo systemctl stop k3s` rend l'API inaccessible, aucun scheduling possible, intervention SSH manuelle requise. Kapsule : Scaleway garantit la disponibilité du control plane via son SLA — l'utilisateur ne le saurait probablement pas.

### B. Networking et exposition

**Q4 — IngressController et ressources créées**

k3s : Traefik pré-installé avec un ServiceLB (klipper-lb, NodePort déguisé). Kapsule : `helm install ingress-nginx` a créé dans le cluster un Deployment, Service LoadBalancer, RBAC, ConfigMaps, IngressClass `nginx` — et **hors du cluster** un vrai Load Balancer Scaleway avec l'IP publique `51.158.58.244`.

**Q5 — Pourquoi Let's Encrypt valide sur Kapsule mais pas k3s**

Let's Encrypt impose une validation HTTP-01 sur le port 80 standard depuis Internet. Sur k3s, le port 80 était accessible via NodePort 30832 (non standard) sans IP publique stable ni DNS valide. Sur Kapsule, le vrai LB avec IP publique + DNS `tp4.dauvel.mediaschool-rouen.fr` permet la validation standard — certificat obtenu en 26 secondes.

### C. Stockage

**Q6 — PVC local-path vs scw-bssd quand un nœud tombe**

`local-path` : données perdues si le nœud hébergeant le volume disparaît (stockage local `/var/lib/rancher/k3s/storage/`). `scw-bssd` : volume réseau distant que Scaleway détache et rattache au nouveau nœud — données préservées. Démontré : après `kubectl delete pod postgres-0 --force`, le pod a redémarré en 4 secondes avec ses données intactes.

**Q7 — Coût du stockage**

`scw-bssd` : ~0,10 €/Go/mois (5 Go → ~0,50 €/mois). `local-path` : 0 € supplémentaire — disque local de la VM déjà payée, mais sans garantie de survie des données.

### D. Lifecycle et autoscaling

**Q8 — Ajouter un nœud : k3s vs Kapsule**

k3s : provisionner une VM, SSH, exécuter le script d'installation avec le token — ~5-10 min, manuel et peu reproductible. Kapsule : `scw k8s pool update <pool-id> size=4 region=fr-par` — une commande, ~2-3 min, entièrement scriptable.

**Q9 — Autoscaling observé au Bloc 8**

Scale-up déclenché en moins de 30 secondes après apparition des pods `Pending`. 3 nœuds DEV1-M provisionnés automatiquement. Sur k3s : le HPA scale les pods mais pas les nœuds — construire un Cluster Autoscaler sur k3s nécessiterait d'intégrer l'API Scaleway manuellement, pas réaliste sans effort significatif.

**Q10 — Mise à jour Kubernetes 1.32 → 1.33**

k3s : `curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.33.x sh -` sur chaque nœud manuellement, avec drain et vérification de compatibilité des APIs. Kapsule : `scw k8s cluster upgrade $CLUSTER_ID version=1.33.x region=fr-par` — Scaleway gère le rolling upgrade du control plane puis des nœuds. Précautions communes : vérifier les APIs dépréciées, tester en staging, planifier hors heures de pointe.

### E. Sécurité et coût

**Q11 — Récupération du kubeconfig et révocation**

k3s : `sudo cat /etc/rancher/k3s/k3s.yaml` — certificat client admin. Révocation complexe (recréer les certificats ou modifier RBAC manuellement). Kapsule : `scw k8s kubeconfig install $CLUSTER_ID` — basé sur les clés API Scaleway IAM. Révocation immédiate en supprimant la clé API dans la console IAM.

**Q12 — Estimation du coût mensuel**

| Ressource | Détail | Coût estimé/mois |
|---|---|---|
| 3 × DEV1-M (base) | 0,0198 €/h × 3 × 730h | ~43 € |
| 1 Load Balancer S | ~0,012 €/h × 730h | ~9 € |
| 5 Go Block Storage (scw-bssd) | ~0,10 €/Go × 5 | ~0,50 € |
| Container Registry | Gratuit jusqu'à 75 Go | 0 € |
| **Total estimé** | | **~57 €/mois** |

À comparer : notre cluster k3s TP1 avec 3 × BASIC3-X2C-8G coûtait ~25 €/mois, mais sans LB, stockage managé ni autohealing.

**Q13 — Incident à 3h du matin**

k3s : l'utilisateur doit intervenir manuellement (SSH, diagnostic, redémarrage). Sans monitoring, l'incident peut passer inaperçu pendant des heures. Pas de SLA. Kapsule : control plane KO → Scaleway intervient (SLA). Nœud worker KO → autohealing recrée automatiquement le nœud. L'utilisateur reste responsable de ses workloads, mais est déchargé de toute l'infrastructure sous-jacente.

### F. Géographie et résilience

**Q14 — Ce que multi-AZ couvre et ne couvre pas**

Protège contre la panne d'une AZ, la maintenance planifiée, la panne d'un nœud. Ne protège pas contre la panne de toute la région, une erreur humaine, la corruption des données (le Block Storage est lié à une AZ).

**Q15 — Mono-région suffit-il ?**

- App B2B française : mono-région (multi-AZ) **suffit** — utilisateurs locaux, RGPD natif.
- App SaaS européenne (Berlin + Madrid + Stockholm) : mono-région **ne suffit pas** — latence trop élevée et risque régional.
- Service soumis à HDS : mono-région **ne suffit pas** — HDS impose réplication géographique, PRA documenté, sites certifiés.

### Synthèse libre

**Q16 — Startup 3 devs, zéro SRE : k3s ou Kapsule ?**

**Kapsule, sans hésiter.** Avec 3 développeurs et zéro SRE, personne n'a le temps de gérer les mises à jour Kubernetes nœud par nœud, surveiller la santé d'etcd, ou intervenir à 3h du matin si un nœud tombe. On l'a vu concrètement : k3s demande beaucoup d'interventions manuelles — clés SSH, problèmes de CNI, NetworkPolicies non supportées par Flannel, architecture hétérogène ARM64/amd64 causant des `ErrImagePull`... Kapsule absorbe toute cette complexité. Le surcoût (~30 €/mois) est largement justifié par le temps économisé et la fiabilité gagnée. La seule raison de choisir k3s serait un besoin de contrôle total (air-gap, conformité spécifique, contrainte budgétaire extrême) ou un contexte edge/IoT.

---

## Bloc 11 — Destruction des ressources (OBLIGATOIRE)

### Objectif
Supprimer proprement toutes les ressources Scaleway créées pendant le TP pour éviter toute facturation continue.

### Étape 11.1 — Supprimer le Service LoadBalancer

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

Toutes ces listes doivent être vides.

![scw k8s cluster list — liste vide, aucun cluster actif](../../Image/TP4/Bloc11/16_cluster_list_vide.png)

![scw lb lb list — aucun Load Balancer actif](../../Image/TP4/Bloc11/17_lb_list_vide.png)

![scw block volume list — aucun Block Storage actif](../../Image/TP4/Bloc11/18_volume_list_vide.png)

![scw registry namespace list — aucun Container Registry actif](../../Image/TP4/Bloc11/19_registry_vide.png)

![Console Scaleway — projet tp4-k8s-dauvel vide, aucune ressource active](../../Image/TP4/Bloc11/20_console_projet_vide.png)

> **Pièges observés** : supprimer le Service LoadBalancer **avant** le cluster est essentiel. Sans cette étape, le Load Balancer Scaleway devient orphelin et continue d'être facturé même après suppression du cluster.

✅ **Checkpoint 11** : Toutes les listes vides confirmées. Aucune ressource active dans le projet Scaleway.

---

## Synthèse des commandes TP4

| Commande | Description |
|---|---|
| `scw init` | Configurer la CLI Scaleway |
| `scw info` | Vérifier l'organisation et le projet actifs |
| `scw k8s cluster create ...` | Créer un cluster Kapsule |
| `scw k8s cluster wait $ID region=fr-par` | Attendre que le cluster soit prêt |
| `scw k8s kubeconfig install $ID region=fr-par` | Télécharger le kubeconfig |
| `scw k8s pool list cluster-id=$ID region=fr-par` | Lister les pools de nœuds |
| `scw k8s pool update $POOL_ID autoscaling=true min-size=2 max-size=5` | Activer l'autoscaling |
| `scw registry namespace create name=... region=fr-par` | Créer un namespace SCR |
| `docker login rg.fr-par.scw.cloud -u nologin --password-stdin` | Authentification SCR |
| `kubectl get certificate -n guestbook -w` | Suivre l'émission d'un certificat cert-manager |
| `kubectl describe challenge` | Diagnostiquer un blocage Let's Encrypt |
| `kubectl scale deploy/greedy --replicas=10` | Déclencher le scale-up du Cluster Autoscaler |
| `scw k8s cluster delete $ID with-additional-resources=true` | Supprimer le cluster et ses ressources |
| `scw lb lb list region=fr-par` | Vérifier qu'aucun LB n'est orphelin |
| `scw block volume list zone=fr-par-1` | Vérifier qu'aucun volume n'est orphelin |

---

## Pièges rencontrés — TP4

| Symptôme | Cause | Résolution |
|---|---|---|
| PVC en `Pending` prolongé | Provisionnement Block Storage lent (30s-2min) | Patienter, `kubectl describe pvc` pour confirmer |
| `ImagePullBackOff` | `imagePullSecrets` oublié dans le Deployment ou Secret dans le mauvais namespace | Vérifier `kubectl describe pod`, recréer le secret dans `guestbook` |
| `EXTERNAL-IP` reste `<pending>` | CCM en cours de provisionnement du LB | Attendre 1-2 min, `kubectl logs -n kube-system -l app=scaleway-cloud-controller-manager` |
| Certificate stuck en `Issuing` | DNS non propagé ou Let's Encrypt ne peut pas atteindre le cluster | `kubectl describe challenge`, tester avec l'environnement `staging` d'abord |
| Autoscaler ne déclenche pas | `requests.cpu` trop basse, pods schedulables sur les nœuds existants | Augmenter `requests.cpu` à `1500m`, vérifier `kubectl describe pod` |
| LB orphelin facturé après destruction | Service LoadBalancer non supprimé avant `cluster delete` | Toujours supprimer le Service LB et attendre 30s avant de supprimer le cluster |
| Volume Block Storage orphelin | PVC non supprimé avant `cluster delete` | `kubectl delete pvc`, puis `scw block volume delete` si nécessaire |