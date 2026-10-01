output "production_url" {
  description = "Production endpoint (live colour)."
  value       = "http://localhost:${var.production_port}"
}

output "preview_url" {
  description = "Preview endpoint (idle colour, used for smoke tests)."
  value       = "http://localhost:${var.preview_port}"
}

output "grafana_url" {
  description = "Grafana with the Blue/Green Release dashboard (anonymous read-only access)."
  value       = "http://localhost:${var.grafana_port}"
}

output "grafana_admin_password" {
  description = "Grafana admin password (terraform output -raw grafana_admin_password)."
  value       = random_password.grafana_admin.result
  sensitive   = true
}

output "app_admin_password" {
  description = "KoalaTech admin password (terraform output -raw app_admin_password)."
  value       = local.admin_password
  sensitive   = true
}

output "databases" {
  description = "PostgreSQL Services created, one per microservice."
  value       = [for db in module.database : db.service_name]
}
