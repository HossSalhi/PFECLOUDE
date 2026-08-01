{{/* Labels communs a toutes les ressources du chart */}}
{{- define "helios.labels" -}}
app.kubernetes.io/part-of: helios
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}
