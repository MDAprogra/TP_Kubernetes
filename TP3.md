# TP 3 — Helm, monitoring, autoscaling, CI/CD

> Master 1 Informatique — Module Orchestration de conteneurs
> Prérequis : TP2 validé (livre d'or persistant, Ingress TLS, NetworkPolicies)

## Thématique fil rouge

**« De l'application déployée à la main au pipeline complet : production-ready »**

À la fin du TP, le livre d'or est packagé en chart Helm, observable via Prometheus + Grafana avec une métrique métier custom, autoscalé sur la charge CPU, et redéployable via un pipeline CI/CD complet.

## Objectifs spécifiques

1. Comprendre Helm : packager, paramétrer, versionner et déployer un chart
2. Mettre en place une stack de monitoring (Prometheus + Grafana via kube-prometheus-stack)
3. Exposer une métrique applicative custom et la visualiser
4. Configurer un HorizontalPodAutoscaler et le valider sous charge
5. Construire un pipeline CI/CD complet (build → test → push → deploy) sur dépôt Git

## Plan de la séance

1. Briefing, état du système TP2, intro Helm
2. Bloc 1 — Helm (consommation, création, templating)
3. Bloc 2 — Monitoring : Prometheus + Grafana
4. Bloc 3 — HorizontalPodAutoscaler + test de charge
5. Bloc 4 — Pipeline CI/CD
6. Restitution + grille finale

---

## Briefing initial

Au tableau :

- État des lieux : on a une dizaine de manifests YAML manuels, une douzaine de `kubectl apply`. Que se passe-t-il quand on déploie en `dev`, `staging`, `prod` ? Que se passe-t-il quand on monte de version ? Comment versionner et rollback ?
- Annoncer les 4 piliers : packaging (Helm), observabilité (Prometheus/Grafana), élasticité (HPA), automatisation (CI/CD).
- Métaphore Helm = `apt` pour Kubernetes : un chart est un paquet, `values.yaml` est la config, le chart museum/registry est le repo.

---

## Bloc 1 — Helm

### Étape 1.1 — Installation et premiers pas

```bash
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
helm search repo redis
```

Discussion : un chart contient `Chart.yaml` (métadonnées), `values.yaml` (paramètres par défaut), `templates/` (manifests templatés Go), parfois des dépendances.

### Étape 1.2 — Installer un chart public

Pour acquérir le geste, sans rapport direct avec notre app :

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

### Étape 1.3 — Squelette de chart

```bash
helm create webapp-chart
tree webapp-chart
```

Parcourir : `templates/_helpers.tpl`, `templates/deployment.yaml`, `templates/service.yaml`, `values.yaml`. Faire un `helm template webapp-chart` pour montrer ce que ça génère sans rien envoyer au cluster.

### Étape 1.4 — Convertir notre app en chart

Vider le contenu généré et repartir de nos YAML TP2. Structure cible :

```
webapp-chart/
├── Chart.yaml
├── values.yaml
├── values-dev.yaml
├── values-prod.yaml
└── templates/
    ├── _helpers.tpl
    ├── configmap.yaml
    ├── secret.yaml
    ├── frontend-deployment.yaml
    ├── frontend-service.yaml
    ├── backend-deployment.yaml
    ├── backend-service.yaml
    ├── postgres-statefulset.yaml
    └── ingress.yaml
```

**`Chart.yaml`** :

```yaml
apiVersion: v2
name: webapp-chart
description: Livre d'or k8s — chart Helm pédagogique
type: application
version: 0.1.0
appVersion: "2.0"
```

**`values.yaml`** (remplacer `<login>` par le compte Docker Hub) :

```yaml
global:
  registry: docker.io/<login>
  imagePullPolicy: IfNotPresent

frontend:
  image: webapp-frontend
  tag: v1.0
  replicas: 2
  resources:
    requests: { cpu: 50m, memory: 64Mi }
    limits:   { cpu: 200m, memory: 128Mi }

backend:
  image: webapp-backend
  tag: v2.0
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
    enabled: false
    secretName: guestbook-tls
```

**`values-dev.yaml`** (override) :

```yaml
backend:
  replicas: 1
  appEnv: dev
frontend:
  replicas: 1
ingress:
  host: dev.guestbook.labo.local
```

**`templates/_helpers.tpl`** :

```yaml
{{- define "webapp.fullname" -}}
{{ .Release.Name }}-{{ .Chart.Name }}
{{- end }}

{{- define "webapp.frontendImage" -}}
{{ .Values.global.registry }}/{{ .Values.frontend.image }}:{{ .Values.frontend.tag }}
{{- end }}

{{- define "webapp.backendImage" -}}
{{ .Values.global.registry }}/{{ .Values.backend.image }}:{{ .Values.backend.tag }}
{{- end }}
```

**`templates/configmap.yaml`** :

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: webapp-config
data:
  APP_ENV: {{ .Values.backend.appEnv | quote }}
  WELCOME_MESSAGE: {{ .Values.backend.welcomeMessage | quote }}
  LOG_LEVEL: "info"
```

**`templates/backend-deployment.yaml`** (extrait clé) :

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
  labels:
    app: webapp
    tier: back
spec:
  replicas: {{ .Values.backend.replicas }}
  selector:
    matchLabels: { app: webapp, tier: back }
  template:
    metadata:
      labels: { app: webapp, tier: back }
    spec:
      containers:
      - name: backend
        image: {{ include "webapp.backendImage" . }}
        imagePullPolicy: {{ .Values.global.imagePullPolicy }}
        ports:
        - containerPort: 5000
        envFrom:
        - configMapRef: { name: webapp-config }
        env:
        {{- if .Values.postgres.enabled }}
        - name: DB_USER
          valueFrom: { secretKeyRef: { name: postgres-credentials, key: POSTGRES_USER } }
        - name: DB_PASS
          valueFrom: { secretKeyRef: { name: postgres-credentials, key: POSTGRES_PASSWORD } }
        - name: DB_NAME
          valueFrom: { secretKeyRef: { name: postgres-credentials, key: POSTGRES_DB } }
        - name: DATABASE_URL
          value: "postgresql://$(DB_USER):$(DB_PASS)@postgres-0.postgres-svc:5432/$(DB_NAME)"
        {{- end }}
        readinessProbe:
          httpGet: { path: /api/health, port: 5000 }
          initialDelaySeconds: 5
        resources:
          {{- toYaml .Values.backend.resources | nindent 10 }}
```

> Les autres templates (frontend, postgres, ingress, secret) suivent la même logique. Les étudiants finissent les fichiers en s'inspirant des YAML TP2.

### Étape 1.5 — Tester, installer, mettre à jour

```bash
helm lint webapp-chart
helm template webapp-chart --values webapp-chart/values-dev.yaml | less
helm install gb webapp-chart \
  --namespace guestbook --create-namespace \
  --values webapp-chart/values-dev.yaml
helm list -n guestbook
helm get values gb -n guestbook

# Modifier replicas dans values, puis :
helm upgrade gb webapp-chart -n guestbook --values webapp-chart/values-dev.yaml
helm history gb -n guestbook
helm rollback gb 1 -n guestbook
```

**Checkpoint 1** ✅
- `helm install` déploie l'application complète en une commande
- `values-dev.yaml` et `values.yaml` produisent des configurations différentes
- `helm rollback` ramène à la version précédente

### Pièges bloc 1

- **Indentation YAML cassée par les templates Go** : `nindent` et `indent` sont distincts. `nindent` ajoute un saut de ligne avant.
- **Quotes oubliées** : `{{ .Values.x }}` non quoté pour une string contenant un `:` casse YAML. Toujours `| quote` les chaînes libres.
- **Conflits de release** : tenter de réinstaller sur le même `release name` sans `--upgrade`. Bien faire la distinction `install` vs `upgrade`.

---

## Bloc 2 — Monitoring

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
```

> L'option `serviceMonitorSelectorNilUsesHelmValues=false` est cruciale : sans elle, Prometheus n'écoute que ses propres ServiceMonitors et ignorera ceux que les étudiants vont créer.

```bash
kubectl get pods -n monitoring
kubectl get svc -n monitoring
```

### Étape 2.2 — Accéder à Grafana et Prometheus

```bash
kubectl port-forward -n monitoring svc/kps-grafana 3000:80 --address 0.0.0.0 &
kubectl port-forward -n monitoring svc/kps-kube-prometheus-stack-prometheus 9090:9090 --address 0.0.0.0 &
```

Ouvrir :
- Grafana : `http://<IP_NOEUD>:3000` — login `admin` / `admin`
- Prometheus : `http://<IP_NOEUD>:9090`

Faire explorer un dashboard Grafana inclus (`Kubernetes / Compute Resources / Cluster`).

### Étape 2.3 — Instrumenter le backend

Construire `webapp-backend:v3.0` qui expose `/metrics` et compte les messages.

**`backend/app.py`** (v3.0, additions à v2.0) :

```python
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from flask import Response
import time

# en haut du fichier, après création de l'app
MESSAGES_TOTAL = Counter(
    "guestbook_messages_total",
    "Nombre total de messages postés"
)
REQUEST_LATENCY = Histogram(
    "guestbook_request_seconds",
    "Latence des requêtes",
    ["endpoint"]
)

# Dans la route messages, après l'INSERT réussi :
#   MESSAGES_TOTAL.inc()

# Wrapper de chronométrage simple : décorer chaque route
@app.before_request
def _start_timer():
    request._start = time.time()

@app.after_request
def _record_latency(resp):
    elapsed = time.time() - getattr(request, "_start", time.time())
    REQUEST_LATENCY.labels(endpoint=request.path).observe(elapsed)
    return resp

@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)
```

**`requirements.txt`** :

```
flask==3.0.3
psycopg2-binary==2.9.9
prometheus-client==0.20.0
```

Build et push :

```bash
docker build -t docker.io/$DOCKERHUB_USER/webapp-backend:v3.0 ./backend
docker push docker.io/$DOCKERHUB_USER/webapp-backend:v3.0
```

Mettre à jour `values.yaml` du chart : `backend.tag: v3.0`, puis :

```bash
helm upgrade gb webapp-chart -n guestbook --values webapp-chart/values.yaml
kubectl exec -n guestbook deploy/backend -- wget -qO- localhost:5000/metrics | head -30
```

### Étape 2.4 — ServiceMonitor

Ajouter dans le chart `templates/servicemonitor.yaml` :

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: backend-metrics
  labels:
    release: kps        # important : matche le selector du Prometheus
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

> Pour que ce ServiceMonitor matche, le `Service` du backend doit avoir un port nommé `http`. Mettre à jour `backend-service.yaml` :

```yaml
ports:
- port: 5000
  targetPort: 5000
  name: http
```

```bash
helm upgrade gb webapp-chart -n guestbook --values webapp-chart/values.yaml
kubectl get servicemonitor -n guestbook
```

Attendre une trentaine de secondes, puis dans Prometheus UI → `Status / Targets` → vérifier que `backend-metrics` est `UP`. Lancer la query :

```promql
guestbook_messages_total
rate(guestbook_request_seconds_count[1m])
```

### Étape 2.5 — Dashboard Grafana custom

Dans Grafana → `+` → `Import` → entrer le JSON suivant ou créer manuellement :

- Panel 1 : `guestbook_messages_total` (stat, big number)
- Panel 2 : `rate(guestbook_request_seconds_count[1m])` (graph par endpoint)
- Panel 3 : `histogram_quantile(0.95, sum(rate(guestbook_request_seconds_bucket[5m])) by (le, endpoint))` — p95 de latence

Charger le navigateur sur le livre d'or, poster quelques messages, voir les courbes monter.

**Checkpoint 2** ✅
- Prometheus scrape le backend (`UP` dans Targets)
- Métrique `guestbook_messages_total` interrogeable
- Dashboard Grafana custom visible

### Pièges bloc 2

- **ServiceMonitor ignoré** : oubli de `release: kps` dans les labels, ou `serviceMonitorSelectorNilUsesHelmValues=true`.
- **Port non nommé** : la spec ServiceMonitor référence un port par nom, pas par numéro.
- **Grafana login impossible** : password mis en `values` non honoré sur upgrade. Faire un `kubectl exec` dans le pod Grafana et `grafana-cli admin reset-admin-password admin`.

---

## Bloc 3 — HorizontalPodAutoscaler

### Étape 3.1 — Vérifier metrics-server

k3s embarque `metrics-server` par défaut.

```bash
kubectl top nodes
kubectl top pods -n guestbook
```

Si `kubectl top` échoue : redémarrer le pod metrics-server, ou vérifier les certs (`--kubelet-insecure-tls` parfois nécessaire en lab).

### Étape 3.2 — HPA sur CPU

Ajouter `templates/backend-hpa.yaml` au chart :

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

> Pré-requis indispensable : le Deployment backend doit avoir des `resources.requests.cpu` définis (sinon le HPA ne sait pas calculer l'utilisation).

```bash
helm upgrade gb webapp-chart -n guestbook --values webapp-chart/values.yaml
kubectl get hpa -n guestbook
kubectl describe hpa backend-hpa -n guestbook
```

### Étape 3.3 — Test de charge

Lancer un générateur de charge depuis un Job :

**`load-test.yaml`** :

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
      - name: hey
        image: williamyeh/hey:latest
        args:
          - "-z"
          - "5m"
          - "-c"
          - "50"
          - "http://backend-svc:5000/api/messages"
```

```bash
kubectl apply -f load-test.yaml
```

Dans des terminaux séparés :

```bash
watch -n2 kubectl get hpa,deploy,pod -n guestbook
kubectl top pods -n guestbook
```

Observer en direct :
- L'utilisation CPU du backend monte
- Le HPA déclenche scale-up (4, 6, 8 replicas)
- Sur Grafana, `rate(guestbook_request_seconds_count[1m])` explose, p95 grimpe
- Après la fin du Job, scale-down après la fenêtre de stabilisation

```bash
kubectl delete job load-test -n guestbook
```

**Checkpoint 3** ✅
- HPA passe le backend de 2 à au moins 4 réplicas sous charge
- Retour à 2 après la fin de la charge
- Visualisable dans Grafana et `kubectl get hpa`

### Pièges bloc 3

- **HPA bloqué à `<unknown>/60%`** : pas de `requests.cpu`, ou metrics-server KO.
- **Pas de scale-up malgré charge** : la charge est sur le frontend, pas le backend, ou la latence vient de Postgres.
- **Flapping** : enlever ou augmenter `stabilizationWindowSeconds`.

---

## Bloc 4 — Pipeline CI/CD

Au choix selon l'infrastructure du labo. On propose **GitLab CI** (le plus probable en école), avec mention des alternatives.

### Étape 4.1 — Architecture du pipeline

Au tableau, schéma :

```
[push git] → [stage build]: docker build & push vers Docker Hub
           → [stage test]:  helm lint + helm template
           → [stage deploy]: helm upgrade --install sur le cluster
```

Pré-requis dans le projet GitLab :
- Un GitLab Runner (shared ou perso) qui peut atteindre le cluster k3s
- Variables CI : `KUBECONFIG_B64`, `DOCKERHUB_USER`, `DOCKERHUB_TOKEN`

### Étape 4.2 — Préparer un ServiceAccount déploiement

Plutôt que de balancer un kubeconfig admin dans la CI, créer un SA dédié au namespace `guestbook` :

**`ci-rbac.yaml`** :

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ci-deployer
  namespace: guestbook
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: ci-deployer
  namespace: guestbook
rules:
- apiGroups: ["", "apps", "batch", "networking.k8s.io", "autoscaling", "monitoring.coreos.com"]
  resources: ["*"]
  verbs: ["*"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ci-deployer
  namespace: guestbook
subjects:
- kind: ServiceAccount
  name: ci-deployer
  namespace: guestbook
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: ci-deployer
---
apiVersion: v1
kind: Secret
metadata:
  name: ci-deployer-token
  namespace: guestbook
  annotations:
    kubernetes.io/service-account.name: ci-deployer
type: kubernetes.io/service-account-token
```

```bash
kubectl apply -f ci-rbac.yaml
TOKEN=$(kubectl get secret ci-deployer-token -n guestbook -o jsonpath='{.data.token}' | base64 -d)
CA=$(kubectl get secret ci-deployer-token -n guestbook -o jsonpath='{.data.ca\.crt}')
SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
```

Construire un kubeconfig CI :

```bash
cat > kubeconfig-ci.yaml <<EOF
apiVersion: v1
kind: Config
clusters:
- name: k3s
  cluster:
    server: ${SERVER}
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
# Copier la sortie dans GitLab → Settings → CI/CD → Variables → KUBECONFIG_B64 (masked, protected)
```

### Étape 4.3 — Le `.gitlab-ci.yml`

```yaml
stages:
  - build
  - test
  - deploy

variables:
  REGISTRY: docker.io
  IMAGE_BACKEND: $REGISTRY/$DOCKERHUB_USER/webapp-backend
  IMAGE_FRONTEND: $REGISTRY/$DOCKERHUB_USER/webapp-frontend
  TAG: $CI_COMMIT_SHORT_SHA

build-backend:
  stage: build
  image: docker:26
  services:
    - docker:26-dind
  script:
    - echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USER" --password-stdin
    - docker build -t $IMAGE_BACKEND:$TAG ./backend
    - docker push $IMAGE_BACKEND:$TAG
  rules:
    - changes:
      - backend/**/*

build-frontend:
  stage: build
  image: docker:26
  services:
    - docker:26-dind
  script:
    - echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USER" --password-stdin
    - docker build -t $IMAGE_FRONTEND:$TAG ./frontend
    - docker push $IMAGE_FRONTEND:$TAG
  rules:
    - changes:
      - frontend/**/*

helm-lint:
  stage: test
  image: alpine/helm:3.14.0
  script:
    - helm lint webapp-chart
    - helm template webapp-chart --values webapp-chart/values.yaml > /tmp/rendered.yaml
    - wc -l /tmp/rendered.yaml

deploy:
  stage: deploy
  image: alpine/helm:3.14.0
  before_script:
    - apk add --no-cache coreutils
    - mkdir -p ~/.kube
    - echo "$KUBECONFIG_B64" | base64 -d > ~/.kube/config
    - chmod 600 ~/.kube/config
  script:
    - |
      helm upgrade --install gb webapp-chart \
        --namespace guestbook \
        --values webapp-chart/values.yaml \
        --set global.registry=docker.io/$DOCKERHUB_USER \
        --set backend.tag=$TAG \
        --set frontend.tag=$TAG \
        --wait --timeout 5m
    - kubectl rollout status deploy/backend -n guestbook
    - kubectl rollout status deploy/frontend -n guestbook
  environment:
    name: production
    url: https://guestbook.labo.local
  only:
    - main
```

### Étape 4.4 — Démontrer le pipeline

Faire un commit qui modifie `backend/app.py` (par exemple changer le message de bienvenue), pousser sur `main`. Suivre dans GitLab :

1. `build-backend` → image taguée avec le SHA, poussée sur Docker Hub
2. `helm-lint` → succès
3. `deploy` → `helm upgrade`, rollout, livre d'or accessible avec la nouvelle image

Discussion :
- Stratégies de tag : SHA vs SemVer vs `latest` (anti-pattern)
- GitOps (ArgoCD, Flux) comme étape suivante
- Tests d'intégration (mention culturelle)

**Checkpoint 4** ✅
- Pipeline GitLab vert sur push
- Image avec SHA visible sur `https://hub.docker.com/r/<login>/webapp-backend/tags`
- App déployée automatiquement, vérifiable via le navigateur

### Pièges bloc 4

- **Runner sans accès au cluster** : firewall, ou kubeconfig mal encodé.
- **`helm upgrade` qui timeout** : probes mal calibrées, ou ressources insuffisantes.
- **Variables CI non protégées** : `KUBECONFIG_B64` et `DOCKERHUB_TOKEN` doivent être `masked` ET `protected`.
- **Token sans expiration géré** : depuis k8s 1.24, les tokens ne sont plus auto-générés. D'où la déclaration explicite du Secret.
- **Token Docker Hub fuité** : ne jamais utiliser le mot de passe du compte ; toujours un PAT (Personal Access Token) révocable.

### Alternatives

- **GitHub Actions** : remplacer `.gitlab-ci.yml` par `.github/workflows/deploy.yml`, mêmes étapes.
- **ArgoCD (GitOps)** : approche pull au lieu de push. Plus robuste, plus avancée. Cf. https://argo-cd.readthedocs.io/
- **Flux CD** : équivalent ArgoCD, plus minimaliste.

---

## Restitution finale

Chaque binôme :
- Démo live courte : leur livre d'or, dashboard Grafana, scale via HPA, déclenchement d'un déploiement
- Q&R rapide

---

## Pièges fréquents — synthèse TP3

| Symptôme | Cause probable | Diagnostic |
|---|---|---|
| `helm install` plante avec « release exists » | Mauvais `release name` ou `--upgrade` oublié | `helm list -A` |
| Templates Helm cassés | Indentation, quotes, `nindent` vs `indent` | `helm template ... --debug` |
| Prometheus n'a pas le target | Label `release: kps` manquant | `kubectl describe servicemonitor` |
| Grafana ne montre pas la métrique | ServiceMonitor inactif, port mal nommé | UI Prometheus → Targets |
| HPA `<unknown>` | `resources.requests.cpu` manquant | `kubectl describe hpa` |
| HPA scale up sans raison | Probe gourmande, ou autre conteneur consomme | Grafana `container_cpu_usage_seconds_total` |
| Pipeline `kubectl: connection refused` | Kubeconfig CA mal encodé | Décoder localement, tester |
| `docker push` denied | Mauvais token Docker Hub, ou dépôt d'un autre login | Vérifier `DOCKERHUB_USER` / `DOCKERHUB_TOKEN` |

---

## Ressources documentaires

- Helm : https://helm.sh/docs/
- Prometheus Operator : https://prometheus-operator.dev/
- kube-prometheus-stack : https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack
- HPA : https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/
- prometheus-client Python : https://github.com/prometheus/client_python
- GitLab CI Kubernetes : https://docs.gitlab.com/ee/user/clusters/agent/
- Docker Hub PAT : https://docs.docker.com/security/for-developers/access-tokens/

