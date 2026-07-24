{{- define "test-chart.labels" -}}
app: {{ .Release.Name }}
{{- end -}}

{{- define "test-chart.namespace" -}}
{{ .Release.Namespace }}
{{- end -}}
