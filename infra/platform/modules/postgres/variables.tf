variable "name" {
  description = "Name of the StatefulSet and its Service, e.g. users-db."
  type        = string
}

variable "database" {
  description = "Database to create inside PostgreSQL."
  type        = string
}

variable "namespace" {
  type = string
}

variable "secret_name" {
  description = "Secret holding POSTGRES_PASSWORD."
  type        = string
}

variable "config_name" {
  description = "ConfigMap holding POSTGRES_USER."
  type        = string
}

variable "storage_size" {
  type    = string
  default = "1Gi"
}

variable "image" {
  type    = string
  default = "postgres:16-alpine"
}

variable "common_labels" {
  type    = map(string)
  default = {}
}
