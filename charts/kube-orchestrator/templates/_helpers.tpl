{{- define "kube-orchestrator.name" -}}
{{- default "kube-orchestrator" .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end -}}

{{- define "kube-orchestrator.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "kube-orchestrator.name" .) | trunc 63 | trimSuffix "-" }}
{{- end -}}
{{- end -}}

{{- define "kube-orchestrator.controllerName" -}}
{{- printf "%s-controller" (include "kube-orchestrator.fullname" .) }}
{{- end -}}

{{- define "kube-orchestrator.metricsReceiverName" -}}
{{- printf "%s-metrics-receiver" (include "kube-orchestrator.fullname" .) }}
{{- end -}}

{{- define "kube-orchestrator.nodeAgentName" -}}
{{- printf "%s-node-agent" (include "kube-orchestrator.fullname" .) }}
{{- end -}}

{{- define "kube-orchestrator.image" -}}
{{- printf "%s:%s" .image.repository .image.tag }}
{{- end -}}

{{- define "kube-orchestrator.labels" -}}
app.kubernetes.io/part-of: kube-orchestrator
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- /*
  组件安全上下文：与 manifests/ 裸 YAML 的加固基线一致
  （非 root 65532、drop ALL capabilities、只读根文件系统）。
*/ -}}
{{- define "kube-orchestrator.securityContext" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 65532
{{- end -}}
