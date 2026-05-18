# TP Kubernetes — Master 1 Informatique

**Binôme : Corentin GODON & Matthias DAUVEL**  
**Module : Arthur BARADEL — Orchestration de conteneurs**

---

## Compte rendu

Le compte rendu complet des quatre TP est disponible ici :

📄 [`TP/Compte_Rendu/GODON_DAUVEL_CR.md`](TP/Compte_Rendu/GODON_DAUVEL_CR.md)

Il couvre :

| TP | Thème | Blocs |
|---|---|---|
| TP1 | Cluster k3s, Pods, Deployments, Services | Pré-TP + Blocs 1→5 |
| TP2 | ConfigMaps, Secrets, PVC, Postgres, Ingress TLS, NetworkPolicies | Pré-requis + Blocs 1→7 |
| TP3 | Helm, Prometheus/Grafana, HPA, CI/CD GitHub Actions | Pré-requis + Blocs 1→4 |
| TP4 | Cluster managé Kapsule, Container Registry, LB, cert-manager, Autoscaling | Blocs 1→11 |

---

## Application fil rouge — Livre d'or

L'application déployée tout au long des TP est un **livre d'or** composé de trois tiers :

| Composant | Technologie | Image Docker |
|---|---|---|
| Frontend | nginx + HTML/JS | `mdprogra/webapp-frontend` |
| Backend | Python Flask | `mdprogra/webapp-backend` |
| Base de données | PostgreSQL 16 | `postgres:16-alpine` |

Les images sont disponibles sur Docker Hub : **https://hub.docker.com/u/mdprogra**

---

## Infrastructure

### TP1 → TP3 — Cluster k3s auto-géré

| VM | Hostname | IP publique | Rôle | Architecture |
|---|---|---|---|---|
| VM 1 | `GODON-k3s-server` | `212.47.230.56` | Control plane | amd64 |
| VM 2 | `GODON-k3s-agent-1` | `163.172.161.25` | Worker 1 | amd64 |
| VM 3 | `GODON-k3s-agent-2` | `212.47.246.29` | Worker 2 | arm64 |

> ⚠️ Le cluster est hétérogène (amd64 + arm64) — toutes les images sont buildées en multi-arch avec `docker buildx --platform linux/amd64,linux/arm64`.

### TP4 — Cluster managé Kapsule (Scaleway)

| Pool | Zone | Nœuds | Type |
|---|---|---|---|
| `pool-paris-1` | fr-par-1 | 2 (autoscaling 2→5) | DEV1-M |
| `pool-paris-2` | fr-par-2 | 1 | DEV1-M |

---

## Structure du dépôt

```
.
├── README.md
├── TP/
│   ├── Compte_Rendu/
│   │   └── GODON_DAUVEL_CR.md      # Compte rendu TP1 + TP2 + TP3 + TP4
│   └── webapp/
│       ├── backend/                # API Flask (v1.0 → v3.0)
│       │   ├── app.py
│       │   ├── requirements.txt
│       │   └── Dockerfile
│       └── frontend/               # nginx + HTML/JS
│           ├── nginx.conf
│           ├── Dockerfile
│           └── html/
├── webapp-chart/                   # Chart Helm (TP3)
│   ├── Chart.yaml
│   ├── values.yaml
│   ├── values-dev.yaml
│   └── templates/
├── tp4/
│   ├── manifests/                  # Manifests Kubernetes TP4
│   │   ├── 10-configmap.yaml
│   │   ├── 11-secret.yaml
│   │   ├── 30-postgres-statefulset.yaml
│   │   ├── 40-backend.yaml
│   │   ├── 41-frontend.yaml
│   │   ├── 60-clusterissuer.yaml
│   │   ├── 61-ingress.yaml
│   │   └── 80-greedy.yaml
│   ├── comparaison.md              # Questionnaire k3s vs Kapsule (16 questions)
│   └── notes-exploration.md        # Observations Bloc 3
├── .github/
│   └── workflows/
│       └── deploy.yml              # Pipeline CI/CD GitHub Actions
└── Image/
    ├── TP1/                        # Captures TP1 (Blocs 1→5)
    ├── TP2/                        # Captures TP2 (Blocs 1→7)
    ├── TP3/                        # Captures TP3 (Blocs 1→4)
    └── TP4/                        # Captures TP4 (Blocs 1→11)
```

---

## Pipeline CI/CD

Le pipeline GitHub Actions (`.github/workflows/deploy.yml`) se déclenche sur chaque push sur `master` et exécute :

1. **build-backend** — `docker buildx` multi-arch (amd64+arm64), push sur Docker Hub
2. **build-frontend** — idem
3. **helm-lint** — validation statique du chart Helm
4. **deploy** — `helm upgrade --install` sur le cluster k3s via kubeconfig encodé en base64

Les secrets CI configurés dans GitHub Actions : `DOCKERHUB_USER`, `DOCKERHUB_TOKEN`, `KUBECONFIG_B64`.

---

## Versions déployées

| Version | Composant | Nouveautés |
|---|---|---|
| `v1.0` | frontend + backend | Version initiale (TP1) |
| `v2.0` | backend | Connexion PostgreSQL (TP2) |
| `v3.0` | backend | Métriques Prometheus `/metrics` (TP3) |