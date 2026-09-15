#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${WORKER_NAMESPACE:-default}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-180s}"

cd "${PROJECT_ROOT}"

kubectl create configmap batch-backend-script \
  --namespace "${NAMESPACE}" \
  --from-file=batch_server_report_sim.py=workers/batch_server_report_sim.py \
  --dry-run=client \
  -o yaml |
  kubectl apply -f -

for manifest in \
  manifests/workers/ubuntu-194-216.yaml \
  manifests/workers/ubuntu-200-226.yaml
do
  deployment="$(basename "${manifest}" .yaml)"
  kubectl apply -f "${manifest}"
  kubectl rollout status \
    "deployment/${deployment}" \
    --namespace "${NAMESPACE}" \
    --timeout "${ROLLOUT_TIMEOUT}"
done

kubectl get pods \
  --namespace "${NAMESPACE}" \
  --selector 'slot in (216,226)' \
  --output wide
