variable "kubeconfig_path" {
  description = "Path to the kubeconfig file."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "kubeconfig context to deploy into. Docker Desktop's cluster is called docker-desktop."
  type        = string
  default     = "docker-desktop"
}

variable "app_namespace" {
  description = "Namespace for the KoalaTech application (both colours and the databases)."
  type        = string
  default     = "koalatech"
}

variable "monitoring_namespace" {
  description = "Namespace for Prometheus and Grafana."
  type        = string
  default     = "monitoring"
}

variable "production_port" {
  description = "Local port for the production endpoint (http://localhost:<port>)."
  type        = number
  default     = 8080
}

variable "preview_port" {
  description = "Local port for the preview endpoint, which points at the idle colour."
  type        = number
  default     = 8081
}

variable "grafana_port" {
  description = "Local port for Grafana."
  type        = number
  default     = 3001
}

variable "admin_username" {
  description = "Default administrator created by the user-service."
  type        = string
  default     = "admin"
}

variable "admin_email" {
  description = "Default administrator email."
  type        = string
  default     = "admin@koalatech.edu.au"
}

variable "admin_password" {
  description = "Default administrator password. Leave empty to generate a random one."
  type        = string
  default     = ""
  sensitive   = true
}

variable "database_storage" {
  description = "Persistent volume size for each PostgreSQL database."
  type        = string
  default     = "1Gi"
}

variable "kube_prometheus_stack_version" {
  description = "Pinned version of the kube-prometheus-stack Helm chart."
  type        = string
  default     = "91.8.2"
}
