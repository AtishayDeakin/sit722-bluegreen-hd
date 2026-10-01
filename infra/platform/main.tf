# ---------------------------------------------------------------------------
# Platform for the blue/green release process.
#
# Ownership boundary (important):
#   Terraform owns the PLATFORM: namespaces, secrets, databases, the stable
#   per-colour Services, the production/preview endpoints and monitoring.
#   The pipeline owns RELEASES: which image runs in which colour, and which
#   colour production points at. Terraform is told to ignore the selector on
#   the traffic Services, so a terraform apply never undoes a swap.
# ---------------------------------------------------------------------------

locals {
  # Same service catalogue the deployment controller uses.
  catalogue = jsondecode(file("${path.module}/../../deploy/services.json"))
  backends  = local.catalogue.backends
  colours   = ["blue", "green"]

  # One stable ClusterIP Service per backend per colour, e.g. user-service-green.
  colour_services = {
    for pair in setproduct(keys(local.backends), local.colours) :
    "${pair[0]}-${pair[1]}" => { service = pair[0], colour = pair[1] }
  }

  common_labels = {
    "app.kubernetes.io/part-of"    = "koalatech"
    "app.kubernetes.io/managed-by" = "terraform"
  }
}

resource "kubernetes_namespace_v1" "app" {
  metadata {
    name   = var.app_namespace
    labels = local.common_labels
  }
}

resource "kubernetes_namespace_v1" "monitoring" {
  metadata {
    name   = var.monitoring_namespace
    labels = local.common_labels
  }
}

# ---------------------------------------------------------------------------
# Configuration and secrets (generated, never committed to Git)
# ---------------------------------------------------------------------------

resource "random_password" "jwt_secret" {
  length  = 48
  special = false
}

resource "random_password" "database" {
  length  = 24
  special = false
}

resource "random_password" "admin" {
  length  = 16
  special = false
}

locals {
  admin_password = var.admin_password != "" ? var.admin_password : random_password.admin.result
}

resource "kubernetes_config_map_v1" "app_config" {
  metadata {
    name      = "koalatech-config"
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels    = local.common_labels
  }

  data = {
    POSTGRES_USER                   = "koalatech"
    JWT_ALGORITHM                   = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES     = "30"
    DEFAULT_ADMIN_USERNAME          = var.admin_username
    DEFAULT_ADMIN_EMAIL             = var.admin_email
    AZURE_STORAGE_CONNECTION_STRING = ""
    AZURE_STORAGE_CONTAINER_NAME    = "profile-photos"
  }
}

resource "kubernetes_secret_v1" "app_secrets" {
  metadata {
    name      = "koalatech-secrets"
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels    = local.common_labels
  }

  data = {
    JWT_SECRET_KEY         = random_password.jwt_secret.result
    POSTGRES_PASSWORD      = random_password.database.result
    DEFAULT_ADMIN_PASSWORD = local.admin_password
  }
}

# ---------------------------------------------------------------------------
# Databases: one PostgreSQL per microservice, shared by blue and green.
# ---------------------------------------------------------------------------

module "database" {
  source   = "./modules/postgres"
  for_each = local.backends

  name          = "${each.value.database}-db"
  database      = each.value.database
  namespace     = kubernetes_namespace_v1.app.metadata[0].name
  secret_name   = kubernetes_secret_v1.app_secrets.metadata[0].name
  config_name   = kubernetes_config_map_v1.app_config.metadata[0].name
  storage_size  = var.database_storage
  common_labels = local.common_labels
}

# ---------------------------------------------------------------------------
# Stable per-colour Services. They never change, so each colour's frontend
# can always find its own backends (user-service-blue, user-service-green...).
# ---------------------------------------------------------------------------

resource "kubernetes_service_v1" "colour_backend" {
  for_each = local.colour_services

  metadata {
    name      = each.key
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = merge(local.common_labels, {
      app   = each.value.service
      color = each.value.colour
    })
  }

  spec {
    type = "ClusterIP"

    selector = {
      app   = each.value.service
      color = each.value.colour
    }

    port {
      name        = "http"
      port        = 8000
      target_port = "http"
    }
  }
}

# ---------------------------------------------------------------------------
# Traffic endpoints. Docker Desktop publishes LoadBalancer Services on
# localhost, so these become http://localhost:8080 and http://localhost:8081.
#
#   koalatech-prod     what users hit; points at the LIVE colour
#   koalatech-preview  points at the IDLE colour for smoke testing
#
# The colour in the selector is switched by the pipeline (the "swap"), so
# Terraform only sets the starting value and then ignores it.
# ---------------------------------------------------------------------------

resource "kubernetes_service_v1" "traffic" {
  for_each = {
    "koalatech-prod"    = { port = var.production_port, role = "production" }
    "koalatech-preview" = { port = var.preview_port, role = "preview" }
  }

  metadata {
    name      = each.key
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels    = merge(local.common_labels, { role = each.value.role })
  }

  spec {
    type = "LoadBalancer"

    selector = {
      app   = "frontend"
      color = "blue"
    }

    port {
      name        = "http"
      port        = each.value.port
      target_port = "http"
    }
  }

  wait_for_load_balancer = false

  lifecycle {
    # Owned by the release pipeline after creation (see note above).
    ignore_changes = [
      spec[0].selector,
      metadata[0].annotations,
    ]
  }
}
