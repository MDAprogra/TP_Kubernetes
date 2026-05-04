# TP 1 — Bootstrap k3s & fondamentaux Kubernetes

> Master 1 Informatique — Module Orchestration de conteneurs
> Prérequis : Docker maîtrisé (Dockerfile, compose, registry), compte Docker Hub actif

## Thématique fil rouge

**« Du cluster vide à la première application multi-tier déployée »**

À la fin du TP, chaque binôme dispose d'un cluster k3s à 3 nœuds qui sert un livre d'or, accessible depuis le réseau du labo, avec frontend et backend scalés indépendamment.

## Objectifs spécifiques

1. Installer et configurer un cluster k3s multi-nœuds (1 server + 2 agents)
2. Comprendre et utiliser les primitives Pod, ReplicaSet, Deployment, Service
3. Déployer une application multi-tier avec communication inter-services
4. Maîtriser les outils de diagnostic (`describe`, `logs`, `events`, `exec`)
5. Faire varier la charge via le scaling déclaratif et impératif

## Plan de la séance

1. Briefing, rappel architecture k8s, distribution des accès
2. Bloc 1 — Installation k3s multi-nœuds, premiers `kubectl`
3. Bloc 2 — Pods, Deployments, ReplicaSets
4. Bloc 3 — Services (ClusterIP, NodePort)
5. Bloc 4 — Application multi-tier complète
6. Bloc 5 — Défis ouverts + débriefing

---

## Pré-TP — Construction des images Docker du fil rouge

À réaliser en autonomie avant TP1, ou en début de séance. Les images produites sont **réutilisées telles quelles** en TP2 et TP3.

### Pré-requis Docker Hub

Chaque binôme se connecte avec son compte Docker Hub avant tout build :

```bash
docker login
# Username: <login>
# Password: <token>  (à créer dans https://hub.docker.com → Account Settings → Security)
```

Les images seront publiées dans des **dépôts publics** sur `docker.io/<login>/` pour éviter d'avoir à gérer des `imagePullSecrets` côté k3s.

### Architecture cible

- `webapp-frontend` : nginx servant une page HTML statique qui appelle l'API
- `webapp-backend` : API Flask exposant `/api/messages` et `/api/health`

### Arborescence

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

### Backend

**`backend/app.py`**

```python
from flask import Flask, jsonify, request
import os, socket, datetime

app = Flask(__name__)
MESSAGES = ["Bienvenue sur le livre d'or k8s !"]

@app.route("/api/messages", methods=["GET", "POST"])
def messages():
    if request.method == "POST":
        data = request.get_json(force=True)
        MESSAGES.append(data.get("text", ""))
    return jsonify({
        "messages": MESSAGES,
        "served_by": socket.gethostname(),
        "env": os.environ.get("APP_ENV", "dev"),
        "ts": datetime.datetime.utcnow().isoformat()
    })

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

**`backend/requirements.txt`**

```
flask==3.0.3
```

**`backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 5000
ENV APP_ENV=prod
CMD ["python", "app.py"]
```

### Frontend

**`frontend/html/index.html`**

```html
<!doctype html>
<html lang="fr">
<head><meta charset="utf-8"><title>K8s Guestbook</title></head>
<body>
  <h1>Livre d'or Kubernetes</h1>
  <p>Servi par : <span id="host"></span></p>
  <ul id="list"></ul>
  <input id="msg" placeholder="Votre message"/>
  <button onclick="post()">Envoyer</button>
  <script src="app.js"></script>
</body>
</html>
```

**`frontend/html/app.js`**

```javascript
async function refresh() {
  const r = await fetch("/api/messages");
  const data = await r.json();
  document.getElementById("host").textContent = data.served_by;
  document.getElementById("list").innerHTML =
    data.messages.map(m => `<li>${m}</li>`).join("");
}
async function post() {
  const text = document.getElementById("msg").value;
  await fetch("/api/messages", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text})
  });
  document.getElementById("msg").value = "";
  refresh();
}
refresh();
setInterval(refresh, 5000);
```

**`frontend/nginx.conf`** — le proxy `/api` est crucial : utilisé en TP1 via Service ClusterIP, en TP2 via Ingress.

```nginx
server {
  listen 80;
  root /usr/share/nginx/html;
  index index.html;

  location /api/ {
    proxy_pass http://backend-svc:5000/api/;
    proxy_set_header Host $host;
  }
}
```

**`frontend/Dockerfile`**

```dockerfile
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY html/ /usr/share/nginx/html/
EXPOSE 80
```

### Build et publication

```bash
export DOCKERHUB_USER=<login>
export TAG=v1.0

docker build -t docker.io/$DOCKERHUB_USER/webapp-backend:$TAG ./backend
docker push docker.io/$DOCKERHUB_USER/webapp-backend:$TAG

docker build -t docker.io/$DOCKERHUB_USER/webapp-frontend:$TAG ./frontend
docker push docker.io/$DOCKERHUB_USER/webapp-frontend:$TAG
```

Vérifier sur `https://hub.docker.com/u/<login>` que les deux dépôts sont **Public**. Si Docker Hub les a créés en Private par défaut, basculer manuellement en Public.

**Vérification finale** : `docker run -p 8080:80 docker.io/$DOCKERHUB_USER/webapp-frontend:$TAG` doit afficher la page (l'appel API échouera tant que le backend ne tourne pas — c'est normal).

---

## Briefing initial

Au tableau, sans slides longues :

- Différence Docker / Kubernetes : Kubernetes orchestre des conteneurs sur plusieurs hôtes, gère le cycle de vie, l'auto-réparation, le scaling.
- Architecture : control-plane (api-server, scheduler, controller-manager, etcd) vs nodes qui exécutent kubelet + runtime + kube-proxy.
- k3s vs k8s vanilla : binaire unique, SQLite par défaut au lieu d'etcd, Traefik et ServiceLB embarqués, idéal labo/edge.
- Consigne : tout sera fait en YAML versionné, pas en clic-clic. Chaque binôme crée un dépôt Git `tp-k8s-<nom>`.

---

## Bloc 1 — Installation du cluster k3s

### Pré-requis machines

3 VMs Linux (Ubuntu 22.04+ ou Debian 12) sur le même réseau, ports `6443/TCP` (API) et `8472/UDP` (Flannel VXLAN) ouverts entre elles. Désactiver le firewall ou l'ajuster.

Convention de nommage :
- `k3s-server` — control plane
- `k3s-agent-1`, `k3s-agent-2` — workers

### Étape 1.1 — Installer le server

Sur **`k3s-server`** :

```bash
curl -sfL https://get.k3s.io | sh -
sudo systemctl status k3s
sudo kubectl get nodes
```

Récupérer le token de jonction :

```bash
sudo cat /var/lib/rancher/k3s/server/node-token
```

Récupérer l'IP du server :

```bash
ip -4 addr show | grep inet
```

### Étape 1.2 — Joindre les agents

Sur **chaque agent**, en remplaçant `<IP_SERVER>` et `<TOKEN>` :

```bash
curl -sfL https://get.k3s.io | \
  K3S_URL=https://<IP_SERVER>:6443 \
  K3S_TOKEN=<TOKEN> \
  sh -
sudo systemctl status k3s-agent
```

### Étape 1.3 — Vérification du cluster

Sur le server :

```bash
sudo kubectl get nodes -o wide
sudo kubectl get pods -A
```

**Checkpoint 1.A** ✅
- `kubectl get nodes` montre 3 nœuds en `Ready`
- Les pods système (`coredns`, `traefik`, `local-path-provisioner`, `metrics-server`) sont `Running`

### Étape 1.4 — Configurer kubectl côté étudiant

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
chmod 600 ~/.kube/config
kubectl config view
```

Pour piloter depuis sa machine perso (optionnel mais formateur) :
- `scp` du fichier `k3s.yaml`
- Remplacer `127.0.0.1` par l'IP publique du server dans `clusters[0].cluster.server`
- `export KUBECONFIG=~/k3s-tp.yaml`

### Étape 1.5 — Premiers kubectl

Manipulations à faire taper à tous, rythmées :

```bash
kubectl version
kubectl cluster-info
kubectl get all -A
kubectl get nodes -o yaml | head -50
kubectl describe node k3s-server
kubectl api-resources | head -30
kubectl explain pod
kubectl explain pod.spec.containers
```

Faire écrire dans le Git de chaque binôme un fichier `notes.md` avec : adresses des nœuds, version k3s, observations de `describe node`.

### Pièges fréquents bloc 1

- **Agents `NotReady`** : 9 fois sur 10, c'est le firewall ou un mauvais token. `sudo journalctl -u k3s-agent -f` sur l'agent.
- **Erreur TLS** sur `kubectl` distant : oubli de remplacer `127.0.0.1` dans le kubeconfig.
- **Disque saturé** : `/var/lib/rancher/k3s/agent/containerd` grossit vite. Prévoir 20 Go minimum.

---

## Bloc 2 — Pods, ReplicaSets, Deployments

### Étape 2.1 — Premier Pod impératif

```bash
kubectl run nginx-test --image=nginx:1.27-alpine
kubectl get pods
kubectl get pods -o wide
kubectl describe pod nginx-test
kubectl logs nginx-test
kubectl exec -it nginx-test -- sh
# dans le pod :
#   wget -qO- localhost
#   exit
kubectl delete pod nginx-test
```

À faire discuter : sur quel nœud le pod a-t-il atterri ? Pourquoi celui-là ? Que se passe-t-il si on le supprime ?

### Étape 2.2 — Pod déclaratif via YAML

Faire créer **`01-pod.yaml`** :

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
kubectl get pod nginx-pod -o yaml | less
kubectl delete -f 01-pod.yaml
```

### Étape 2.3 — Deployment frontend

**`02-frontend-deploy.yaml`** (remplacer `<login>` par le compte Docker Hub) :

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
        image: docker.io/<login>/webapp-frontend:v1.0
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

**Démontrer l'auto-réparation** :

```bash
kubectl get pods -l tier=front
kubectl delete pod <un-des-pods-frontend>
kubectl get pods -l tier=front -w
```

Le ReplicaSet recrée immédiatement. Couper avec `Ctrl+C`.

### Étape 2.4 — Scaling

```bash
kubectl scale deploy/frontend --replicas=5
kubectl get pods -l tier=front
kubectl scale deploy/frontend --replicas=2
```

Faire la même chose en YAML : modifier `replicas: 2` puis `kubectl apply -f`. Discussion sur l'idempotence et la dérive impératif/déclaratif.

### Étape 2.5 — Rolling update

Construire et pousser une `webapp-frontend:v1.1` (changer le `<h1>` de la page).

```bash
kubectl set image deploy/frontend frontend=docker.io/<login>/webapp-frontend:v1.1
kubectl rollout status deploy/frontend
kubectl rollout history deploy/frontend
kubectl rollout undo deploy/frontend
```

**Checkpoint 2** ✅
- 3 puis 5 puis 2 réplicas observés
- Suppression manuelle d'un pod → recréation automatique
- Rollback fonctionnel

### Pièges fréquents bloc 2

- **`ImagePullBackOff`** : typo dans le nom de l'image, mauvais tag, ou dépôt resté en Private sur Docker Hub. Vérifier `kubectl describe pod` puis ouvrir l'URL `https://hub.docker.com/r/<login>/webapp-frontend` dans un navigateur en navigation privée.
- **Rate limit Docker Hub** (anonyme : ~100 pulls / 6h par IP) : visible dans `describe pod`. Solution rapide : `docker login` côté nœud puis `docker pull` manuel pour cacher l'image, ou créer un `imagePullSecret`.
- **Pod en `Pending`** : ressources insuffisantes (`describe pod` → `Events`).
- **Selector mismatch** : `selector.matchLabels` ≠ `template.metadata.labels` → le Deployment plante au `apply`.

---

## Bloc 3 — Services et exposition

### Étape 3.1 — Service ClusterIP

**`03-frontend-svc.yaml`** :

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
```

Test depuis un pod éphémère :

```bash
kubectl run curl-test --rm -it --image=curlimages/curl:latest -- sh
# dans le pod :
#   curl http://frontend-svc/
#   exit
```

### Étape 3.2 — Service NodePort (exposition extérieure)

**`04-frontend-nodeport.yaml`** :

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

```bash
kubectl apply -f 04-frontend-nodeport.yaml
curl http://<IP_NIMPORTE_QUEL_NOEUD>:30080/
```

Faire ouvrir dans le navigateur. Le frontend s'affiche, l'API est cassée → transition naturelle vers le backend.

**Checkpoint 3** ✅
- Service `ClusterIP` joignable depuis un pod du cluster
- Frontend accessible sur `:30080` depuis le réseau du labo

---

## Bloc 4 — Application multi-tier complète

### Étape 4.1 — Backend

**`05-backend.yaml`** (Deployment + Service dans un seul fichier) :

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
        image: docker.io/<login>/webapp-backend:v1.0
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

> ⚠️ Le nom du Service `backend-svc` doit correspondre à celui codé en dur dans le `nginx.conf` du frontend (`proxy_pass http://backend-svc:5000`). C'est l'occasion d'expliquer la résolution DNS interne via `coredns`.

### Étape 4.2 — Test end-to-end

Recharger `http://<IP_NOEUD>:30080/` dans le navigateur, taper un message, vérifier qu'il s'affiche, observer le `served_by` qui change selon le pod backend qui répond.

```bash
kubectl logs -l tier=back -f
```

### Étape 4.3 — Observer la résolution de service

```bash
kubectl exec -it <un-pod-frontend> -- sh
# dans le pod :
#   nslookup backend-svc
#   wget -qO- http://backend-svc:5000/api/health
```

Discussion : `backend-svc.default.svc.cluster.local`, FQDN, namespaces.

### Étape 4.4 — Diagnostic guidé

Le formateur **casse intentionnellement** quelque chose (par ex. mauvais nom d'image, mauvais port dans le Service). Les étudiants doivent diagnostiquer avec :

```bash
kubectl get events --sort-by=.lastTimestamp
kubectl describe pod <pod-en-échec>
kubectl logs <pod> --previous
kubectl get endpoints backend-svc
```

**Checkpoint 4** ✅
- Le livre d'or est fonctionnel de bout en bout
- Au moins une panne diagnostiquée et corrigée

---

## Bloc 5 — Défis ouverts

À choisir 2 sur 3, à rendre via le dépôt Git :

**Défi A — Affinity** : faire en sorte que les pods backend soient anti-affinés entre eux (jamais deux backend sur le même nœud). Fournir le YAML.

**Défi B — Init container** : ajouter un `initContainer` au backend qui attend que `postgres-svc` soit résolvable (anticipation TP2) et logue « ready ».

**Défi C — Multi-conteneurs** : modifier le pod frontend pour ajouter un sidecar qui tail les logs nginx et les expose via un endpoint `/sidecar-logs` (utiliser `busybox` + `tail -f`).

---

## Pièges fréquents — synthèse TP1

| Symptôme | Cause probable | Diagnostic |
|---|---|---|
| `ImagePullBackOff` | Typo, dépôt Private, ou rate limit Docker Hub | `kubectl describe pod` → champ `Events` |
| `CrashLoopBackOff` | App plante au démarrage | `kubectl logs <pod> --previous` |
| `Pending` indéfini | Ressources insuffisantes ou taint | `kubectl describe pod` → Events |
| Service sans `Endpoints` | Mauvais selector | `kubectl describe svc`, vérifier les labels |
| Probe qui tue le pod | `initialDelaySeconds` trop court | Allonger ou ajuster le path |
| DNS ne résout pas | coredns en panne ou mauvais FQDN | `kubectl get pods -n kube-system` |

---

## Ressources documentaires

- Documentation k3s : https://docs.k3s.io/
- Concepts Kubernetes : https://kubernetes.io/docs/concepts/
- `kubectl` cheat sheet : https://kubernetes.io/docs/reference/kubectl/cheatsheet/
- `kubectl explain` à utiliser systématiquement plutôt que de chercher en ligne

---