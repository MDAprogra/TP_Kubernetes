# TP 2 — Configuration, persistance et networking avancé

> Master 1 Informatique — Module Orchestration de conteneurs
> Prérequis : TP1 validé (cluster k3s 3 nœuds, livre d'or v1.0 déployable)

## Thématique fil rouge

**« Le livre d'or grandit : persistance Postgres, configuration externalisée, exposition propre, isolation réseau »**

À la fin du TP, le livre d'or stocke ses données en base, est configuré via ConfigMaps/Secrets, accessible via un nom de domaine en HTTPS via Ingress, et le trafic interne est filtré par NetworkPolicies (frontend → backend → postgres uniquement).

## Objectifs spécifiques

1. Externaliser configuration (ConfigMap) et secrets (Secret), comprendre leurs limites de sécurité
2. Comprendre PV / PVC / StorageClass et le provisionnement dynamique
3. Déployer Postgres en StatefulSet avec stockage persistant et identité stable
4. Configurer un Ingress Traefik (host-based, TLS auto-signé)
5. Sécuriser le trafic intra-cluster avec des NetworkPolicies (default-deny + allow ciblé)

## Plan de la séance

1. Briefing, rappel TP1, build de `webapp-backend:v2.0`
2. Bloc 1 — ConfigMaps & Secrets
3. Bloc 2 — PV / PVC / StorageClass
4. Bloc 3 — StatefulSet Postgres
5. Bloc 4 — Backend v2 connecté à Postgres
6. Bloc 5 — Ingress Traefik + TLS
7. Bloc 6 — NetworkPolicies
8. Bloc 7 — Défis + débriefing

---

## Briefing initial

Au tableau :

- Rappel : Pods éphémères, Deployments stateless. Que se passe-t-il quand un pod redémarre ? Les données en RAM disparaissent. Démontrable en redémarrant le backend du TP1 → tous les messages perdus.
- Anti-pattern : tout configurer en `env: value:` dur dans le Deployment. On veut **séparer code, config et secrets**.
- Plan : on va ajouter une vraie base, externaliser la config, mettre une porte d'entrée propre, et fermer les portes inutiles.

### Pré-requis : image backend v2.0

Le backend doit savoir parler à Postgres si `DATABASE_URL` est positionnée, sinon retomber en mode mémoire.

**`backend/app.py`** (version 2.0) :

```python
from flask import Flask, jsonify, request
import os, socket, datetime
import psycopg2
from psycopg2 import pool

app = Flask(__name__)

DB_URL = os.environ.get("DATABASE_URL")
DB_POOL = None
MEM_MESSAGES = []

def init_db():
    global DB_POOL
    if not DB_URL:
        return
    DB_POOL = pool.SimpleConnectionPool(1, 5, dsn=DB_URL)
    with DB_POOL.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages(
                    id SERIAL PRIMARY KEY,
                    text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            conn.commit()
        DB_POOL.putconn(conn)

@app.route("/api/messages", methods=["GET", "POST"])
def messages():
    if DB_POOL:
        conn = DB_POOL.getconn()
        try:
            with conn.cursor() as cur:
                if request.method == "POST":
                    text = request.get_json(force=True).get("text", "")
                    cur.execute("INSERT INTO messages(text) VALUES (%s)", (text,))
                    conn.commit()
                cur.execute("SELECT text FROM messages ORDER BY id")
                msgs = [r[0] for r in cur.fetchall()]
        finally:
            DB_POOL.putconn(conn)
    else:
        if request.method == "POST":
            MEM_MESSAGES.append(request.get_json(force=True).get("text", ""))
        msgs = MEM_MESSAGES

    return jsonify({
        "messages": msgs,
        "served_by": socket.gethostname(),
        "env": os.environ.get("APP_ENV", "dev"),
        "welcome": os.environ.get("WELCOME_MESSAGE", ""),
        "backend_mode": "postgres" if DB_POOL else "memory",
        "ts": datetime.datetime.utcnow().isoformat()
    })

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
```

**`backend/requirements.txt`** :

```
flask==3.0.3
psycopg2-binary==2.9.9
```

Build et push :

```bash
docker build -t docker.io/$DOCKERHUB_USER/webapp-backend:v2.0 ./backend
docker push docker.io/$DOCKERHUB_USER/webapp-backend:v2.0
```

---

## Bloc 1 — ConfigMaps & Secrets

### Étape 1.1 — Créer un namespace dédié

Pour ne pas polluer `default`, et pratiquer l'isolation logique :

```bash
kubectl create namespace guestbook
kubectl config set-context --current --namespace=guestbook
kubectl config view --minify | grep namespace
```

Toutes les commandes du TP utiliseront ce namespace.

### Étape 1.2 — ConfigMap : trois manières de créer

**Méthode 1 — impérative à partir de littéraux** :

```bash
kubectl create configmap demo-cm \
  --from-literal=APP_ENV=tp2 \
  --from-literal=LOG_LEVEL=info
kubectl get cm demo-cm -o yaml
kubectl delete cm demo-cm
```

**Méthode 2 — à partir d'un fichier** :

```bash
echo "welcome=Bienvenue sur le livre d'or persistant !" > app.properties
kubectl create configmap demo-cm --from-file=app.properties
kubectl get cm demo-cm -o yaml
kubectl delete cm demo-cm
```

**Méthode 3 — déclarative (à privilégier)** :

**`10-configmap.yaml`** :

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

### Étape 1.3 — Secret : credentials Postgres

Insister sur le fait que Secret est **encodé** en base64, pas chiffré au repos par défaut. Utile pour éviter l'affichage clair, pas pour la sécurité forte (qui passe par `EncryptionConfiguration` côté kube-apiserver, hors scope TP).

**`11-secret.yaml`** :

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

> Note : `stringData` accepte du clair (Kubernetes encode pour vous). `data:` exigerait du base64 manuel — montrer la différence avec `echo -n "guestbook" | base64`.

```bash
kubectl apply -f 11-secret.yaml
kubectl get secret postgres-credentials -o yaml
echo "Z3Vlc3Rib29r" | base64 -d   # vérifier le décodage
```

### Étape 1.4 — Atelier : injecter la config dans un pod

Petit pod de test :

**`12-test-config.yaml`** :

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

**Checkpoint 1** ✅
- ConfigMap et Secret visibles via `kubectl get cm,secret`
- Pod test affiche les variables d'environnement attendues
- ConfigMap monté en volume aussi accessible

### Pièges bloc 1

- **Modifier un ConfigMap ne redémarre pas les pods** : il faut soit `kubectl rollout restart deploy/<nom>`, soit utiliser un outil comme `stakater/Reloader` (mention culturelle).
- **Secret non chiffré** : visible par tout pod ayant le bon ServiceAccount. Penser RBAC en prod.
- **`stringData` vs `data`** : confusion classique. `stringData` est plus simple en TP.

---

## Bloc 2 — PV / PVC / StorageClass

### Étape 2.1 — Inspecter ce qui existe en k3s

k3s embarque `local-path-provisioner` qui crée des PV à la volée sur le disque du nœud où le pod tourne.

```bash
kubectl get sc
kubectl describe sc local-path
kubectl get pv     # vide pour l'instant
kubectl get pvc -A # vide
```

Discussion : `Provisioner`, `ReclaimPolicy: Delete`, `volumeBindingMode: WaitForFirstConsumer` (le PV n'est créé que quand un pod le réclame, pour qu'il soit colocalisé).

### Étape 2.2 — Premier PVC

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
kubectl get pvc
kubectl get pv     # toujours vide ! WaitForFirstConsumer
```

Attacher à un pod :

**`21-pod-pvc.yaml`** :

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: writer
spec:
  containers:
  - name: writer
    image: busybox:1.36
    command: ["sh", "-c", "echo hello-$(date) >> /data/log.txt && sleep 3600"]
    volumeMounts:
    - name: data
      mountPath: /data
  volumes:
  - name: data
    persistentVolumeClaim:
      claimName: demo-pvc
```

```bash
kubectl apply -f 21-pod-pvc.yaml
kubectl get pv     # un PV apparaît, lié au PVC
kubectl exec writer -- cat /data/log.txt
```

### Étape 2.3 — Démontrer la persistance

```bash
kubectl delete pod writer
kubectl apply -f 21-pod-pvc.yaml
kubectl exec writer -- cat /data/log.txt
# La ligne du run précédent est toujours là
```

Bonus : `kubectl get pod writer -o wide` pour voir sur quel nœud, puis montrer que `local-path` colocalise le pod et le volume sur ce nœud.

```bash
kubectl delete -f 21-pod-pvc.yaml
kubectl delete -f 20-pvc-demo.yaml
```

**Checkpoint 2** ✅
- PVC créé, PV provisionné dynamiquement à l'utilisation
- Données qui survivent à la suppression du pod
- Compréhension de `WaitForFirstConsumer`

### Pièges bloc 2

- **`Pending` éternel** sur le PVC : pas de StorageClass par défaut, ou nom mal écrit (`local-path` ≠ `localpath`).
- **Volume non démonté** lors du `kubectl delete pvc` : peut nécessiter de supprimer le PV à la main si reclaimPolicy mal positionné.
- **`ReadWriteOnce`** : le volume ne peut être monté que par **un pod à la fois**. Bloquant si on tente plusieurs réplicas avec un `local-path`.

---

## Bloc 3 — StatefulSet Postgres

### Étape 3.1 — Pourquoi un StatefulSet pour Postgres ?

Au tableau :
- Identité réseau stable : `postgres-0`, `postgres-1`, …
- Volume dédié par réplica via `volumeClaimTemplates`
- Démarrage et arrêt ordonnés
- Indispensable pour bases de données, files de messages, etc.

### Étape 3.2 — Headless service + StatefulSet

**`30-postgres.yaml`** :

```yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres-svc
  labels: { app: postgres }
spec:
  clusterIP: None        # headless : DNS direct vers les pods
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

```bash
kubectl apply -f 30-postgres.yaml
kubectl get sts,pod,pvc,svc -l app=postgres
kubectl describe sts postgres
```

> Le `subPath: pgdata` évite qu'`initdb` se plaigne d'un répertoire `lost+found` dans le mountpoint.

### Étape 3.3 — Vérifier Postgres

```bash
kubectl exec -it postgres-0 -- psql -U guestbook -d guestbook -c "\dt"
kubectl exec -it postgres-0 -- psql -U guestbook -d guestbook -c \
  "CREATE TABLE test(id INT); INSERT INTO test VALUES (1); SELECT * FROM test;"
```

Test de la résolution DNS depuis un pod éphémère :

```bash
kubectl run dns-test --rm -it --image=busybox:1.36 -- sh
# nslookup postgres-svc
# nslookup postgres-0.postgres-svc
# exit
```

### Étape 3.4 — Test de persistance

```bash
kubectl exec postgres-0 -- psql -U guestbook -d guestbook -c \
  "INSERT INTO test VALUES (42);"

kubectl delete pod postgres-0
kubectl get pod postgres-0 -w   # recréation, Ctrl+C
kubectl exec postgres-0 -- psql -U guestbook -d guestbook -c \
  "SELECT * FROM test;"
# Les données sont là
```

**Checkpoint 3** ✅
- `postgres-0` running avec un PVC `data-postgres-0` lié
- DNS `postgres-0.postgres-svc` résolvable
- Données qui survivent au redémarrage du pod

### Pièges bloc 3

- **`initdb` qui échoue** : oubli du `subPath`, ou variables manquantes (`POSTGRES_PASSWORD` obligatoire).
- **PVC orphelins** : supprimer un StatefulSet ne supprime PAS les PVC. `kubectl delete pvc -l app=postgres` à la main.
- **Service sans `clusterIP: None`** : le DNS pointe vers une VIP, pas vers les pods nommés. Le headless est crucial.

---

## Bloc 4 — Backend v2 connecté à Postgres

### Étape 4.1 — Mettre à jour le Deployment backend

**`40-backend-v2.yaml`** :

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
        image: docker.io/<login>/webapp-backend:v2.0
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

> L'ordre des variables compte : `DB_USER`, `DB_PASS`, `DB_NAME` doivent être définies **avant** `DATABASE_URL` pour que la substitution `$(...)` fonctionne.

```bash
kubectl apply -f 40-backend-v2.yaml
kubectl rollout status deploy/backend
kubectl logs -l tier=back
```

### Étape 4.2 — Redéployer le frontend

Reprendre le `02-frontend-deploy.yaml` du TP1 (en pensant bien à utiliser `docker.io/<login>/webapp-frontend:v1.0`) et son service `frontend-svc`. Le frontend ne change pas (le `nginx.conf` proxie toujours vers `backend-svc:5000`).

```bash
kubectl apply -f 02-frontend-deploy.yaml
kubectl apply -f 03-frontend-svc.yaml
```

### Étape 4.3 — Test complet et démonstration de persistance

Exposer temporairement avec port-forward :

```bash
kubectl port-forward svc/frontend-svc 8080:80 --address 0.0.0.0
```

Poster 3 messages depuis le navigateur, puis :

```bash
kubectl rollout restart deploy/backend
kubectl rollout status deploy/backend
```

Recharger la page : les messages sont **toujours là**. Comparer avec le comportement du TP1 (mémoire perdue à chaque restart). Moment fort pédagogique.

```bash
kubectl exec postgres-0 -- psql -U guestbook -d guestbook -c \
  "SELECT * FROM messages;"
```

**Checkpoint 4** ✅
- Le livre d'or persiste à travers les redémarrages
- Le champ `backend_mode` du JSON renvoie `"postgres"`
- Les messages sont visibles dans la table SQL

---

## Bloc 5 — Ingress Traefik + TLS

### Étape 5.1 — Concept et inspection

Au tableau : Service expose au niveau L4 (TCP/UDP), Ingress expose au niveau L7 (HTTP, host, path). Traefik est l'IngressController embarqué de k3s.

```bash
kubectl get pods -n kube-system | grep traefik
kubectl get svc -n kube-system traefik
```

Le service `traefik` est un LoadBalancer ServiceLB qui écoute sur `:80` et `:443` de chaque nœud.

### Étape 5.2 — Premier Ingress, host-based

Préparer la résolution DNS locale. Sur la machine de l'étudiant (et **toutes celles depuis lesquelles on testera**) :

```bash
# /etc/hosts (Linux/macOS) ou C:\Windows\System32\drivers\etc\hosts
<IP_NOEUD_K3S>  guestbook.labo.local
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
curl -H "Host: guestbook.labo.local" http://<IP_NOEUD>/
```

Ouvrir `http://guestbook.labo.local/` dans le navigateur.

### Étape 5.3 — Activer TLS auto-signé

Générer le certificat :

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout tls.key -out tls.crt \
  -subj "/CN=guestbook.labo.local/O=labo" \
  -addext "subjectAltName=DNS:guestbook.labo.local"

kubectl create secret tls guestbook-tls \
  --cert=tls.crt --key=tls.key
kubectl get secret guestbook-tls
```

Mettre à jour l'Ingress :

**`51-ingress-tls.yaml`** :

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

Dans le navigateur, l'avertissement de certificat est attendu (auto-signé). Importer le `tls.crt` dans le navigateur pour le retirer (optionnel).

**Checkpoint 5** ✅
- Le livre d'or est joignable via `http://guestbook.labo.local/`
- HTTPS fonctionne (avec warning de cert auto-signé toléré)

### Pièges bloc 5

- **404 Traefik** : mauvais `host` ou `pathType`. Vérifier `kubectl describe ingress`.
- **DNS** : l'étudiant a oublié `/etc/hosts` et tape `guestbook.labo.local` qui n'est pas résolvable.
- **Certificat ignoré** : le secret n'est pas dans le **même namespace** que l'Ingress.

---

## Bloc 6 — NetworkPolicies

### Étape 6.1 — Vérifier que le CNI applique les policies

k3s utilise `flannel` + `kube-router` (ce dernier applique les NetworkPolicies depuis k3s v1.21).

```bash
kubectl get pods -n kube-system | grep -E "flannel|kube-router"
```

Si `kube-router` n'apparaît pas, c'est que k3s a été démarré avec `--disable-network-policy`. Sur le server : `cat /etc/systemd/system/k3s.service`. À corriger avant de continuer.

### Étape 6.2 — Tester l'absence d'isolation

Avant la policy, n'importe quel pod peut joindre Postgres :

```bash
kubectl run pwn --rm -it --image=postgres:16-alpine -- sh
# psql -h postgres-0.postgres-svc -U guestbook -d guestbook
# (mot de passe demandé : ChangeMe_inTP2!)
# SELECT * FROM messages;
# exit
```

Constat : aucune isolation par défaut. C'est le comportement Kubernetes natif et c'est dangereux en multi-tenant.

### Étape 6.3 — Default-deny + allow ciblé

**`60-default-deny.yaml`** :

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
```

Le livre d'or est désormais cassé (et c'est volontaire). Le formateur fait constater :

```bash
curl -k https://guestbook.labo.local/   # timeout ou 502
kubectl logs -l tier=front --tail=20
```

**`61-allow-rules.yaml`** :

```yaml
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
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-ingress-to-frontend
spec:
  podSelector:
    matchLabels: { app: webapp, tier: front }
  policyTypes: [Ingress]
  ingress:
  - from: []   # toutes sources (Traefik est dans kube-system)
    ports:
    - port: 80
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-front-to-back
spec:
  podSelector:
    matchLabels: { app: webapp, tier: back }
  policyTypes: [Ingress]
  ingress:
  - from:
    - podSelector:
        matchLabels: { app: webapp, tier: front }
    ports:
    - port: 5000
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-back-to-postgres
spec:
  podSelector:
    matchLabels: { app: postgres }
  policyTypes: [Ingress]
  ingress:
  - from:
    - podSelector:
        matchLabels: { app: webapp, tier: back }
    ports:
    - port: 5432
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-front-egress
spec:
  podSelector:
    matchLabels: { app: webapp, tier: front }
  policyTypes: [Egress]
  egress:
  - to:
    - podSelector:
        matchLabels: { app: webapp, tier: back }
    ports:
    - port: 5000
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-back-egress
spec:
  podSelector:
    matchLabels: { app: webapp, tier: back }
  policyTypes: [Egress]
  egress:
  - to:
    - podSelector:
        matchLabels: { app: postgres }
    ports:
    - port: 5432
```

> Note : la policy `allow-dns` est indispensable, sinon les pods ne résolvent plus aucun nom et l'app reste cassée. Le piège classique.

```bash
kubectl apply -f 61-allow-rules.yaml
curl -k https://guestbook.labo.local/   # à nouveau OK
```

### Étape 6.4 — Vérifier que l'isolation fonctionne

```bash
kubectl run pwn --rm -it --image=postgres:16-alpine -- sh
# psql -h postgres-0.postgres-svc -U guestbook -d guestbook
# → timeout : la NetworkPolicy bloque le pod intrus
# exit
```

**Checkpoint 6** ✅
- Le livre d'or fonctionne avec policies actives
- Un pod hors `tier=back` ne peut plus joindre Postgres

### Pièges bloc 6

- **Tout est cassé après default-deny** : oubli de la policy DNS (port 53/UDP vers `kube-dns`).
- **Selectors trop larges** : `podSelector: {}` veut dire **tous les pods du namespace**. Bien réfléchir.
- **NetworkPolicy ignorée** : CNI qui ne les applique pas. Vérifier `kube-router` ou installer Calico.

---

## Bloc 7 — Défis ouverts

À réaliser à la maison, à choisir 2 sur 3 :

**Défi A — Init container et migrations** : ajouter un `initContainer` au backend qui exécute une migration SQL (créer un index sur `created_at`) avant le démarrage du conteneur principal. Penser à `psql` ou un `wait-for-it`.

**Défi B — Sauvegarde Postgres en CronJob** : écrire un `CronJob` qui exécute `pg_dump` régulièrement, écrit le résultat dans un PVC dédié `pg-backups`, et nettoie les sauvegardes anciennes.

**Défi C — Ingress path-based** : déployer une seconde app simple (par exemple `nginx` qui sert une page « admin ») et faire en sorte que `https://guestbook.labo.local/admin/` route vers cette nouvelle app, tandis que `/` reste sur le frontend principal. Manipuler les middlewares Traefik si besoin.

---

## Pièges fréquents — synthèse TP2

| Symptôme | Cause probable | Diagnostic |
|---|---|---|
| Pod backend `CrashLoopBackOff` après v2 | `DATABASE_URL` mal interpolée | `kubectl logs <pod>`, vérifier ordre des `env` |
| Postgres `Pending` | PVC non lié, StorageClass | `kubectl describe pvc data-postgres-0` |
| `initdb` plante | Pas de `subPath`, ou mot de passe vide | Logs du pod postgres-0 |
| 404 sur l'Ingress | Mauvais `host` ou DNS non configuré | `curl -H "Host:..."`, `/etc/hosts` |
| Tout coupé après NetworkPolicy | DNS bloqué | Ajouter `allow-dns` |
| ConfigMap modifié, app pas mise à jour | Pas de redémarrage auto | `kubectl rollout restart` |
| Plusieurs réplicas Postgres | Tentative scale > 1 sur RWO | Garder `replicas: 1`, ou Patroni (hors scope) |

---

## Ressources documentaires

- ConfigMaps : https://kubernetes.io/docs/concepts/configuration/configmap/
- Secrets : https://kubernetes.io/docs/concepts/configuration/secret/
- Persistent Volumes : https://kubernetes.io/docs/concepts/storage/persistent-volumes/
- StatefulSets : https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/
- Ingress Traefik dans k3s : https://docs.k3s.io/networking/networking-services
- NetworkPolicies : https://kubernetes.io/docs/concepts/services-networking/network-policies/

---

## Grille d'évaluation TP2 (sur 20)

| Critère | Points |
|---|---|
| ConfigMap + Secret correctement définis et utilisés (env + volume) | 3 |
| StatefulSet Postgres opérationnel avec PVC qui survit | 4 |
| Backend v2 connecté à Postgres, persistance vérifiée | 3 |
| Ingress fonctionnel en HTTP puis HTTPS | 3 |
| NetworkPolicies : default-deny + allow ciblés, DNS inclus | 4 |
| Qualité du namespace, des labels, du dépôt Git | 1 |
| Défis ouverts (2 sur 3) | 2 |

---

## Livrables attendus

Sur le dépôt Git, sous `tp2/` :

- Tous les YAML numérotés (`10-` à `61-`)
- Code source `backend/app.py` v2 et `Dockerfile`
- `README.md` avec ordre d'application des manifests et capture du livre d'or persistant
- Capture d'écran montrant que les messages survivent à un `rollout restart`
- Capture montrant que le pod intrus `pwn` ne peut plus joindre Postgres
