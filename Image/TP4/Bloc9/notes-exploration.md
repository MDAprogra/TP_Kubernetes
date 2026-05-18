# Notes d'exploration — Cluster Kapsule tp4-cluster-dauvel

## Différences observées vs k3s

### 1. Control plane invisible
Sur k3s : `kube-apiserver`, `etcd`, `kube-scheduler`, `kube-controller-manager` tournent 
sur le nœud server et sont visibles via `kubectl get pods -A`.
Sur Kapsule : aucun de ces pods n'est visible — ils sont gérés par Scaleway, 
accessibles uniquement via l'URL `https://cd95bb1a...api.k8s.fr-par.scw.cloud:6443`.

### 2. Pas d'IngressController pré-installé
Sur k3s : Traefik est installé par défaut.
Sur Kapsule : aucun IngressController — il faut installer ingress-nginx manuellement via Helm.

### 3. StorageClasses multiples
Sur k3s : 1 seule StorageClass `local-path` (stockage local sur le nœud).
Sur Kapsule : 8 StorageClasses basées sur `csi.scaleway.com` 
(`scw-bssd`, `sbs-default`, `sbs-5k`, `sbs-15k`, avec variantes `-retain`).

### 4. CNI différent
Sur k3s : Flannel + kube-router.
Sur Kapsule : Cilium (avec hubble pour l'observabilité réseau).

### 5. Pods système spécifiques à Kapsule
- `konnectivity-agent` : tunnel sécurisé entre control plane Scaleway et nœuds
- `csi-node` : driver Block Storage natif Scaleway
- `hubble-generate-certs` : certificats pour l'observabilité Cilium
Sur k3s : aucun de ces composants.

### 6. metrics-server pré-installé
Sur Kapsule : `metrics-server` est présent par défaut.
Sur k3s : aussi présent par défaut sur k3s.