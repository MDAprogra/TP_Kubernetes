{{- define "webapp.fullname" -}}
{{ .Release.Name }}-{{ .Chart.Name }}
{{- end }}

{{- define "webapp.frontendImage" -}}
{{ .Values.global.registry }}/{{ .Values.frontend.image }}:{{ .Values.frontend.tag }}
{{- end }}

{{- define "webapp.backendImage" -}}
{{ .Values.global.registry }}/{{ .Values.backend.image }}:{{ .Values.backend.tag }}
{{- end }}
