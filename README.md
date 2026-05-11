# TP Kubernetes — Master 1 Informatique

**Binôme : Corentin GODON & Matthias DAUVEL**
**Module : Arthur BARADEL — Orchestration de conteneurs**

---

## Compte rendu

Le compte rendu complet des trois TP est disponible ici :

📄 [`TP/Compte_Rendu/GODON_DAUVEL_CR.md`](TP/Compte_Rendu/GODON_DAUVEL_CR.md)

Il couvre :

| TP | Thème | Blocs |
|---|---|---|
| TP1 | Cluster k3s, Pods, Deployments, Services | Pré-TP + Blocs 1→5 |
| TP2 | ConfigMaps, Secrets, PVC, Postgres, Ingress TLS, NetworkPolicies | Pré-requis + Blocs 1→7 |
| TP3 | Helm, Prometheus/Grafana, HPA, CI/CD GitLab | Pré-requis + Blocs 1→4 |

---

## Structure du dépôt

```
.
├── TP1.md                        # Sujet TP1
├── TP2.md                        # Sujet TP2
├── TP3.md                        # Sujet TP3
├── TP/
│   ├── Compte_Rendu/
│   │   └── GODON_DAUVEL_CR.md    # Compte rendu (TP1 + TP2 + TP3)
│   └── webapp/
│       ├── backend/              # API Flask (v1→v3)
│       └── frontend/             # nginx + HTML
└── Image/
    ├── TP1/                      # Captures TP1 (Blocs 1→5)
    ├── TP2/                      # Captures TP2 (Blocs 1→7)
    └── TP3/                      # Captures TP3 (Blocs 1→4)
```
