{{/*
Common labels for all Nekazari resources.
*/}}
{{- define "nekazari.labels" -}}
app.kubernetes.io/part-of: nekazari
app.kubernetes.io/managed-by: helm
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
