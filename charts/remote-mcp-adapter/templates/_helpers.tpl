{{/*
Expand the name of the chart.
*/}}
{{- define "remote-mcp-adapter.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "remote-mcp-adapter.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "remote-mcp-adapter.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "remote-mcp-adapter.labels" -}}
helm.sh/chart: {{ include "remote-mcp-adapter.chart" . }}
{{ include "remote-mcp-adapter.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "remote-mcp-adapter.selectorLabels" -}}
app.kubernetes.io/name: {{ include "remote-mcp-adapter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "remote-mcp-adapter.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "remote-mcp-adapter.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Allow the release namespace to be overridden for multi-namespace deployments in combined charts
*/}}
{{- define "remote-mcp-adapter.namespace" -}}
{{- if .Values.namespaceOverride }}
{{- .Values.namespaceOverride }}
{{- else }}
{{- .Release.Namespace }}
{{- end }}
{{- end }}

{{/*
Return the appropriate apiVersion for podDisruptionBudget.
*/}}
{{- define "remote-mcp-adapter.podDisruptionBudget.apiVersion" -}}
{{- if $.Values.podDisruptionBudget.apiVersion }}
{{- print $.Values.podDisruptionBudget.apiVersion }}
{{- else if $.Capabilities.APIVersions.Has "policy/v1/PodDisruptionBudget" }}
{{- print "policy/v1" }}
{{- else }}
{{- print "policy/v1beta1" }}
{{- end }}
{{- end }}

{{/*
Return the adapter config directory.
*/}}
{{- define "remote-mcp-adapter.configDirectory" -}}
{{- $configValues := .Values.config | default (dict) -}}
{{- index $configValues "directory" | default "/etc/remote-mcp-adapter" -}}
{{- end }}

{{/*
Return the full adapter config file path.
*/}}
{{- define "remote-mcp-adapter.configFilePath" -}}
{{- $configDirectory := include "remote-mcp-adapter.configDirectory" . | trimSuffix "/" -}}
{{- printf "%s/config.yaml" $configDirectory -}}
{{- end }}

{{/*
Return the rendered adapter configmap name.
*/}}
{{- define "remote-mcp-adapter.configMapName" -}}
{{- printf "%s-config" (include "remote-mcp-adapter.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{/*
Return the shared storage mount path.
*/}}
{{- define "remote-mcp-adapter.sharedMountPath" -}}
{{- $configValues := .Values.config | default (dict) -}}
{{- $configFile := index $configValues "config.yaml" | default (dict) -}}
{{- $storageConfig := index $configFile "storage" | default (dict) -}}
{{- index $storageConfig "root" | default "/data/shared" -}}
{{- end }}

{{/*
Render env/envFrom blocks for a container.

Input:
  root: root chart context
  fixedEnv: map of env vars always emitted directly
  environment:
    env: map of explicit env vars
    envFromSecret:
      name: existing secret name
      keys: optional list of keys to load selectively

If `keys` is omitted or empty, the entire secret is loaded via envFrom.
If `keys` is provided, only those secret keys are loaded as env vars.
*/}}
{{- define "remote-mcp-adapter.renderEnvironment" -}}
{{- $root := .root -}}
{{- $fixedEnv := .fixedEnv | default (dict) -}}
{{- $environment := .environment | default (dict) -}}
{{- $envValues := index $environment "env" | default (dict) -}}
{{- $envFromSecret := index $environment "envFromSecret" | default (dict) -}}
{{- $secretName := index $envFromSecret "name" | default "" -}}
{{- $secretKeys := index $envFromSecret "keys" | default (list) -}}
{{- if or $fixedEnv $envValues (and $secretName (gt (len $secretKeys) 0)) }}
env:
{{- range $name, $value := $fixedEnv }}
  - name: {{ $name | quote }}
    value: {{ tpl ($value | toString) $root | quote }}
{{- end }}
{{- range $name, $value := $envValues }}
  - name: {{ $name | quote }}
    value: {{ tpl ($value | toString) $root | quote }}
{{- end }}
{{- if and $secretName (gt (len $secretKeys) 0) }}
{{- range $key := $secretKeys }}
  - name: {{ $key | quote }}
    valueFrom:
      secretKeyRef:
        name: {{ $secretName | quote }}
        key: {{ $key | quote }}
{{- end }}
{{- end }}
{{- end }}
{{- if and $secretName (eq (len $secretKeys) 0) }}
envFrom:
  - secretRef:
      name: {{ $secretName | quote }}
{{- end }}
{{- end }}