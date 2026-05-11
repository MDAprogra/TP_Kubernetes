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