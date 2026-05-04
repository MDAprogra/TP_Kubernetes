# Compte Rendu — TP1 Kubernetes
**Binôme : Corentin GODON & [Prénom] DAUVEL**  
**Module : Orchestration de conteneurs — Master Expert IT**  
**Date : 04 Mai 2026**

---

## Pré-TP — Construction des images Docker

### Objectif
Construire et publier les images Docker du frontend (nginx) et du backend (Flask) sur Docker Hub, afin qu'elles soient accessibles depuis le cluster k3s.

### Ce que nous avons fait
Nous avons créé la structure de fichiers suivante :

```
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

Le backend est une API Flask exposant deux routes :
- `GET/POST /api/messages` — gestion des messages du livre d'or
- `GET /api/health` — healthcheck

Le frontend est un serveur nginx servant une page HTML statique qui appelle l'API backend via un proxy `/api/` défini dans `nginx.conf`.

### Problème rencontré — Architecture AMD64
Lors du premier build, les images ont été buildées sans préciser la plateforme cible. Les VMs Scaleway étant en `linux/amd64`, les pods tombaient en `ImagePullBackOff` avec l'erreur :

```
no match for platform in manifest: not found
```

**Solution** : rebuild en forçant explicitement la plateforme AMD64 avec `docker buildx` :

```bash
docker buildx create --use --name mybuilder
docker buildx build --platform linux/amd64 \
  -t mdprogra/webapp-frontend:v1.2 ./frontend --push --no-cache

docker buildx build --platform linux/amd64 \
  -t mdprogra/webapp-backend:v1.2 ./backend --push --no-cache
```

Les images sont publiées en **public** sur Docker Hub : `https://hub.docker.com/u/mdprogra`

---

## Bloc 1 — Installation du cluster k3s

### Objectif
Installer un cluster k3s multi-nœuds composé d'un server (control plane) et deux agents (workers) sur des VMs Scaleway.

### Infrastructure Scaleway

| VM | IP publique | Rôle |
|---|---|---|
| `GODON-k3s-server` | `212.47.230.56` | Control plane |
| `GODON-k3s-agent-1` | `163.172.161.25` | Worker 1 |
| `GODON-k3s-agent-2` | `212.47.246.29` | Worker 2 |

Toutes les VMs sont des instances `BASIC3-X2C-8G` / `BASIC2-A4C-8G` sous Ubuntu 26.04, zone PAR-1.

### Installation du server

```bash
curl -sfL https://get.k3s.io | sh -
sudo kubectl get nodes
```

Récupération du token de jonction :
```bash
sudo cat /var/lib/rancher/k3s/server/node-token
```

### Jonction des agents

Sur chaque agent :
```bash
curl -sfL https://get.k3s.io | \
  K3S_URL=https://212.47.230.56:6443 \
  K3S_TOKEN=<TOKEN> \
  sh -
```

### Configuration de kubectl

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
chmod 600 ~/.kube/config
echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc
source ~/.bashrc
```

### Vérification

```
NAME                STATUS   ROLES           AGE     VERSION
godon-k3s-agent-1   Ready    <none>          4m15s   v1.35.4+k3s1
godon-k3s-agent-2   Ready    <none>          3m26s   v1.35.4+k3s1
godon-k3s-server    Ready    control-plane   6m34s   v1.35.4+k3s1
```

✅ **Checkpoint 1** : 3 nœuds en `Ready`, pods système (`coredns`, `traefik`, `metrics-server`) en `Running`.

---

## Bloc 2 — Pods, Deployments, ReplicaSets

### Objectif
Comprendre les primitives Kubernetes : Pod, ReplicaSet, Deployment. Tester l'auto-réparation, le scaling et le rolling update.

### Étape 1 — Pod simple (`01-pod.yaml`)

Création d'un pod nginx basique pour comprendre la structure d'un manifest YAML :

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
kubectl get pods
kubectl delete -f 01-pod.yaml
```

Un Pod seul n'est pas auto-réparé en cas de suppression — c'est le rôle du Deployment.

### Étape 2 — Deployment frontend (`02-frontend-deploy.yaml`)

Un Deployment gère un ReplicaSet qui maintient le nombre souhaité de pods en vie.

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

### Étape 3 — Auto-réparation

Kubernetes surveille en permanence l'état du cluster. Si un pod est supprimé, le ReplicaSet en recrée un automatiquement :

```bash
kubectl delete pod frontend-5f6fd4c584-8xbs5
kubectl get pods -l tier=front -w
```

On observe immédiatement la création du pod `hf7pv` en remplacement — preuve de l'auto-réparation.

### Étape 4 — Scaling

```bash
# Scale à 5 replicas
kubectl scale deploy/frontend --replicas=5

# Retour à 2 replicas
kubectl scale deploy/frontend --replicas=2
```

Le scaling est **déclaratif** : on déclare l'état souhaité et Kubernetes s'assure de le respecter.

### Étape 5 — Rolling update & Rollback

```bash
# Mise à jour de l'image
kubectl set image deploy/frontend frontend=docker.io/mdprogra/webapp-frontend:v1.1
kubectl rollout status deploy/frontend

# Rollback à la version précédente
kubectl rollout undo deploy/frontend
kubectl rollout history deploy/frontend
```

Le rolling update remplace les pods progressivement sans interruption de service. Le rollback revient à la révision précédente.

✅ **Checkpoint 2** : Auto-réparation, scaling et rollback validés.

---

## Bloc 3 — Services et exposition

### Objectif
Exposer les pods via des Services pour permettre la communication interne (ClusterIP) et externe (NodePort).

### Service ClusterIP (`03-frontend-svc.yaml`)

Un Service ClusterIP expose les pods **uniquement à l'intérieur du cluster**, avec une IP stable indépendante des pods :

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

### Service NodePort (`04-frontend-nodeport.yaml`)

Un Service NodePort expose les pods **depuis l'extérieur du cluster** sur un port fixe de chaque nœud :

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

Le frontend est accessible sur `http://212.47.230.56:30080`.

✅ **Checkpoint 3** : Frontend accessible depuis le navigateur sur `:30080`.

---

## Bloc 4 — Application multi-tier complète

### Objectif
Déployer le backend Flask et connecter les deux tiers via la résolution DNS interne de Kubernetes.

### Backend (`05-backend.yaml`)

Le fichier contient le Deployment et le Service dans un seul manifest (séparés par `---`) :

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

### Résolution DNS interne

Le nom `backend-svc` dans `nginx.conf` est résolu automatiquement par CoreDNS :

```bash
kubectl exec -it <pod-frontend> -- sh
nslookup backend-svc
# → backend-svc.default.svc.cluster.local → 10.43.51.23
```

Kubernetes expose chaque Service via un nom DNS au format :  
`<nom-service>.<namespace>.svc.cluster.local`

### Test end-to-end

L'application est accessible sur `http://212.47.230.56:30080`. Le livre d'or affiche le nom du pod backend qui répond (`served_by`), ce qui change selon le pod sélectionné par le load balancer.

✅ **Checkpoint 4** : Application multi-tier fonctionnelle de bout en bout.

---

## Problème rencontré — ImagePullBackOff sur agent-2

Le nœud `godon-k3s-agent-2` présentait systématiquement des erreurs `ImagePullBackOff`. Diagnostic via :

```bash
kubectl describe pod <pod> 
# → Events: Failed to pull image: no match for platform in manifest
```

**Cause** : images buildées sans `--platform linux/amd64`.  
**Solution** : rebuild avec `docker buildx --platform linux/amd64`.

Le nœud `agent-2` continuait à avoir des problèmes de pull après correction, probablement dû à un cache containerd corrompu. Les pods se sont répartis sur `agent-1` et `server` sans impact sur le fonctionnement global.

---

## Bloc 5 — Défis ouverts

### Défi A — Anti-affinité des pods backend

#### Objectif
Garantir que deux pods backend ne soient **jamais schedulés sur le même nœud**. Cela améliore la haute disponibilité : si un nœud tombe, au moins un pod backend reste disponible sur un autre nœud.

#### Concept
L'anti-affinité (`podAntiAffinity`) est une règle de scheduling qui dit au scheduler Kubernetes : "ne place pas ce pod sur un nœud où tourne déjà un pod avec ces labels".

Il existe deux modes :
- `requiredDuringSchedulingIgnoredDuringExecution` — **strict** : le pod restera en `Pending` s'il ne trouve pas de nœud libre
- `preferredDuringSchedulingIgnoredDuringExecution` — **souple** : préférence, mais pas bloquant

Nous utilisons le mode **strict** avec `topologyKey: kubernetes.io/hostname` pour séparer les pods par nœud physique.

#### YAML (`05-backend.yaml` mis à jour)

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

✅ **Défi A validé** : l'anti-affinité garantit la répartition des pods backend sur des nœuds distincts.

---

## Synthèse des commandes clés

| Commande | Description |
|---|---|
| `kubectl apply -f <fichier>` | Appliquer un manifest YAML |
| `kubectl get pods -w` | Observer les pods en temps réel |
| `kubectl describe pod <pod>` | Détails et événements d'un pod |
| `kubectl logs <pod>` | Logs d'un pod |
| `kubectl scale deploy/<nom> --replicas=N` | Scaling impératif |
| `kubectl rollout undo deploy/<nom>` | Rollback |
| `kubectl exec -it <pod> -- sh` | Shell dans un pod |
| `kubectl get svc` | Lister les services |
