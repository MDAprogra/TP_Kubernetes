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
Externaliser la configuration applicative et les credentials hors du code source. Comprendre les différences entre ConfigMap (données publiques) et Secret (données sensibles), et les différentes méthodes de création.

### Étape 1.1 — Namespace dédié

Pour isoler les ressources du TP2 du namespace `default`, on crée un namespace `guestbook` et on le définit comme contexte courant :

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

![ConfigMap créé en impératif](../../Image/TP2/Bloc1/01_configmap_imperatif.png)

**Méthode 2 — à partir d'un fichier** :

```bash
echo "welcome=Bienvenue sur le livre d'or persistant !" > app.properties
kubectl create configmap demo-cm --from-file=app.properties
kubectl get cm demo-cm -o yaml
kubectl delete cm demo-cm
```

![ConfigMap créé depuis un fichier](../../Image/TP2/Bloc1/02_configmap_fichier.png)

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

![ConfigMap déclaratif — webapp-config créé](../../Image/TP2/Bloc1/03_configmap_declaratif.png)

### Étape 1.3 — Secret : credentials Postgres

Le Secret est encodé en **base64**, pas chiffré — quiconque a accès à l'API Kubernetes peut décoder les valeurs. En production, il faudrait coupler avec `EncryptionConfiguration` ou un gestionnaire externe (Vault, Sealed Secrets).

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

![Secret postgres-credentials — valeurs encodées en base64](../../Image/TP2/Bloc1/04_secret.png)

### Étape 1.4 — Injection de la config dans un pod test

Le pod `config-tester` (`12-test-config.yaml`) injecte le ConfigMap et le Secret comme variables d'environnement, et monte le ConfigMap en volume :

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

![Checkpoint 1 — ConfigMap et Secret opérationnels](../../Image/TP2/Bloc1/06_checkpoint1.png)

> **Piège rencontré** : modifier un ConfigMap ne redémarre pas automatiquement les pods qui l'utilisent. Il faut déclencher un `kubectl rollout restart deploy/<nom>` manuellement.

✅ **Checkpoint 1** : ConfigMap `webapp-config` et Secret `postgres-credentials` créés dans le namespace `guestbook`. Le pod test affiche les variables attendues et accède au ConfigMap monté en volume.

---

## Bloc 2 — PV / PVC / StorageClass

### Objectif
Comprendre le modèle de stockage persistant de Kubernetes : PersistentVolume (PV), PersistentVolumeClaim (PVC) et StorageClass. Observer le provisionnement dynamique de k3s et démontrer la survie des données après suppression d'un pod.

### Étape 2.1 — Inspection de ce qui existe en k3s

k3s embarque `local-path-provisioner` qui crée des PV automatiquement sur le disque du nœud hébergeant le pod :

```bash
kubectl get sc
kubectl describe sc local-path
kubectl get pv     # vide initialement
kubectl get pvc -A
```

![StorageClass local-path — provisionnement dynamique WaitForFirstConsumer](../../Image/TP2/Bloc2/07_storageclass.png)

Points clés observés :
- `Provisioner: rancher.io/local-path`
- `ReclaimPolicy: Delete` — le PV est supprimé avec le PVC
- `volumeBindingMode: WaitForFirstConsumer` — le PV n'est créé qu'au moment où un pod le réclame, pour garantir la colocalisation pod/volume sur le même nœud

### Étape 2.2 — Premier PVC et démonstration de WaitForFirstConsumer

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
kubectl get pvc    # → Pending (aucun pod ne l'utilise encore)
kubectl get pv     # → toujours vide
```

![PVC demo-pvc en Pending — WaitForFirstConsumer en action](../../Image/TP2/Bloc2/08_pvc_pending.png)

Après attachement au pod `writer` (`21-pod-pvc.yaml`) :

```bash
kubectl apply -f 21-pod-pvc.yaml
kubectl get pv     # → un PV apparaît, lié au PVC
kubectl exec writer -- cat /data/log.txt
```

![PVC Bound après attachement au pod — PV créé dynamiquement](../../Image/TP2/Bloc2/09_pvc_bound.png)

### Étape 2.3 — Démonstration de la persistance

```bash
kubectl delete pod writer
kubectl apply -f 21-pod-pvc.yaml
kubectl exec writer -- cat /data/log.txt
# La ligne du run précédent est toujours présente
```

Les données survivent à la suppression du pod : seul le PVC et le PV doivent être supprimés pour libérer le stockage.

```bash
kubectl delete -f 21-pod-pvc.yaml
kubectl delete -f 20-pvc-demo.yaml
```

> **Piège rencontré** : avec `accessModes: ReadWriteOnce`, le volume ne peut être monté que par **un seul pod à la fois**. Tenter de scaler un Deployment avec un PVC `local-path` en RWO provoque un blocage immédiat.

✅ **Checkpoint 2** : PVC créé, PV provisionné dynamiquement à la première utilisation. Données persistantes après suppression du pod. Comportement `WaitForFirstConsumer` observé.

---

## Bloc 3 — StatefulSet Postgres

### Objectif
Déployer PostgreSQL en StatefulSet pour bénéficier d'une identité réseau stable, d'un volume dédié par réplica, et d'un démarrage/arrêt ordonné — caractéristiques essentielles pour les bases de données.

### Étape 3.1 — Pourquoi un StatefulSet ?

Contrairement aux Deployments (pods interchangeables), un StatefulSet garantit :
- **Identité stable** : `postgres-0`, `postgres-1`, …
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
kubectl describe sts postgres
```

![StatefulSet postgres-0 en Running avec PVC data-postgres-0 lié](../../Image/TP2/Bloc3/10_postgres_running.png)

![PVC data-postgres-0 — 1Gi Bound](../../Image/TP2/Bloc3/11_postgres_pvc.png)

### Étape 3.3 — Vérification de Postgres et résolution DNS

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

![Connexion psql à postgres-0 — DNS headless résolu](../../Image/TP2/Bloc3/12_postgres_connect.png)

### Étape 3.4 — Test de persistance

```bash
kubectl exec postgres-0 -- psql -U guestbook -d guestbook -c "INSERT INTO test VALUES (42);"
kubectl delete pod postgres-0
kubectl get pod postgres-0 -w   # recréation par le StatefulSet controller
kubectl exec postgres-0 -- psql -U guestbook -d guestbook -c "SELECT * FROM test;"
# → les valeurs 1 et 42 sont toujours présentes
```

> **Piège rencontré** : supprimer un StatefulSet avec `kubectl delete sts postgres` ne supprime **pas** les PVC. Les volumes `data-postgres-0` persistent et doivent être supprimés manuellement avec `kubectl delete pvc data-postgres-0` si l'on souhaite repartir de zéro.

✅ **Checkpoint 3** : `postgres-0` en Running, PVC `data-postgres-0` lié. DNS `postgres-0.postgres-svc` résolvable. Données persistantes après suppression et recréation du pod.

---

## Bloc 4 — Backend v2 connecté à Postgres

### Objectif
Déployer le backend v2.0 en le connectant à PostgreSQL via ConfigMap et Secret, puis démontrer que les messages du livre d'or survivent aux redémarrages du backend.

### Étape 4.1 — Deployment backend v2

**`40-backend-v2.yaml`** — L'ordre des variables `env` est crucial : `DB_USER`, `DB_PASS` et `DB_NAME` doivent être définis **avant** `DATABASE_URL` pour que la substitution `$(...)` fonctionne :

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

![Backend v2 — 2 pods en Running, connectés à Postgres](../../Image/TP2/Bloc4/13_backend_v2_running.png)

### Étape 4.2 — Redéploiement du frontend

Le frontend ne change pas par rapport au TP1. On réapplique les manifests existants (le `nginx.conf` continue de proxier vers `backend-svc:5000`) :

```bash
kubectl apply -f 02-frontend-deploy.yaml
kubectl apply -f 03-frontend-svc.yaml
```

![Frontend déployé dans le namespace guestbook](../../Image/TP2/Bloc4/15_frontend_running.png)

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

![Livre d'or v2 — interface avec backend_mode: postgres](../../Image/TP2/Bloc4/16_guestbook_v2.png)

Les messages sont **toujours présents** après le rollout restart — contrairement au TP1 où ils étaient perdus. La vérification directe en SQL confirme :

```bash
kubectl exec postgres-0 -- psql -U guestbook -d guestbook -c "SELECT * FROM messages;"
```

![Table messages PostgreSQL — données persistées](../../Image/TP2/Bloc4/14_postgres_table.png)

![Persistance vérifiée — messages survivent au rollout restart](../../Image/TP2/Bloc4/17_persistance.png)

> **Moment pédagogique clé** : le champ `backend_mode` de la réponse JSON renvoie `"postgres"` (et non `"memory"` comme en TP1), confirmant que le backend utilise bien la base de données.

✅ **Checkpoint 4** : le livre d'or persiste à travers les redémarrages. `backend_mode: "postgres"` confirmé. Messages visibles dans la table SQL.

---

## Bloc 5 — Ingress Traefik + TLS

### Objectif
Remplacer l'accès NodePort par un Ingress Traefik (niveau L7) permettant le routage HTTP par nom d'hôte, puis activer HTTPS avec un certificat auto-signé.

### Étape 5.1 — Inspection de Traefik

k3s embarque Traefik comme IngressController. Il est déployé dans `kube-system` et écoute sur les ports 80 et 443 de chaque nœud via un Service LoadBalancer :

```bash
kubectl get pods -n kube-system | grep traefik
kubectl get svc -n kube-system traefik
```

Différence fondamentale : les Services opèrent en **L4** (TCP/UDP), tandis qu'un Ingress opère en **L7** (HTTP) et peut router en fonction du `Host` ou du chemin.

### Étape 5.2 — Premier Ingress, routage par nom d'hôte

Ajout dans `/etc/hosts` sur la machine de test :

```
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

![Ingress HTTP — livre d'or accessible sur guestbook.labo.local](../../Image/TP2/Bloc5/18_ingress_http.png)

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

Mise à jour de l'Ingress via **`51-ingress-tls.yaml`** — l'annotation `traefik.ingress.kubernetes.io/router.entrypoints` active l'écoute sur les deux entrypoints :

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

L'avertissement de certificat dans le navigateur est attendu (cert auto-signé non reconnu par une CA de confiance). En production, on utiliserait `cert-manager` avec Let's Encrypt.

> **Piège rencontré** : le Secret TLS doit être dans le **même namespace** que l'Ingress. Un Secret dans `default` pour un Ingress dans `guestbook` est silencieusement ignoré par Traefik.

✅ **Checkpoint 5** : le livre d'or est joignable via `http://guestbook.labo.local/`. HTTPS fonctionne avec l'avertissement de certificat auto-signé attendu.

---

## Synthèse des commandes TP2

| Commande | Description |
|---|---|
| `kubectl create namespace <ns>` | Créer un namespace |
| `kubectl config set-context --current --namespace=<ns>` | Changer de namespace courant |
| `kubectl create configmap <nom> --from-literal=K=V` | ConfigMap impératif |
| `kubectl create configmap <nom> --from-file=<fichier>` | ConfigMap depuis un fichier |
| `kubectl get cm,secret` | Lister ConfigMaps et Secrets |
| `kubectl get sc` | Lister les StorageClasses |
| `kubectl get pv,pvc` | Lister PersistentVolumes et Claims |
| `kubectl get sts` | Lister les StatefulSets |
| `kubectl exec -it postgres-0 -- psql -U guestbook -d guestbook` | Console psql dans le pod Postgres |
| `kubectl rollout restart deploy/<nom>` | Forcer le redémarrage d'un Deployment |
| `kubectl get ingress` | Lister les Ingress |
| `kubectl describe ingress <nom>` | Détails d'un Ingress |

---

## Pièges rencontrés — TP2

| Symptôme | Cause | Résolution |
|---|---|---|
| `initdb` échoue dans postgres-0 | `subPath` manquant (répertoire non vide) | Ajouter `subPath: pgdata` dans `volumeMounts` |
| `DATABASE_URL` non interpolée | Ordre des `env` incorrect | Définir `DB_USER`, `DB_PASS`, `DB_NAME` avant `DATABASE_URL` |
| PVC en `Pending` éternel | `WaitForFirstConsumer` — aucun pod ne réclame le volume | Normal jusqu'au déploiement du pod ; vérifier le nom de la StorageClass |
| Données perdues après `kubectl delete sts` | Les PVC ne sont pas supprimés automatiquement | Supprimer manuellement avec `kubectl delete pvc data-postgres-0` |
| 404 sur l'Ingress | Mauvais `host` ou DNS non configuré dans `/etc/hosts` | `curl -H "Host: guestbook.labo.local"` pour tester, vérifier `kubectl describe ingress` |
| HTTPS ne fonctionne pas | Secret TLS dans un namespace différent de l'Ingress | Créer le Secret dans le même namespace que l'Ingress |

