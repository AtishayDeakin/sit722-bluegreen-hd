output "service_name" {
  description = "DNS name the microservice uses to reach this database."
  value       = kubernetes_service_v1.this.metadata[0].name
}
