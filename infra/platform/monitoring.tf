# ---------------------------------------------------------------------------
# Monitoring: Prometheus (the data the rollback decision is based on) and
# Grafana (the live blue/green dashboard), via the kube-prometheus-stack chart.
# ---------------------------------------------------------------------------

resource "random_password" "grafana_admin" {
  length  = 16
  special = false
}

resource "helm_release" "monitoring" {
  name       = "monitoring"
  namespace  = kubernetes_namespace_v1.monitoring.metadata[0].name
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  version    = var.kube_prometheus_stack_version

  timeout = 900
  wait    = true

  values = [
    templatefile("${path.module}/monitoring/values.yaml.tftpl", {
      app_namespace          = var.app_namespace
      grafana_port           = var.grafana_port
      grafana_admin_password = random_password.grafana_admin.result
    })
  ]
}

# Grafana's sidecar picks up any ConfigMap labelled grafana_dashboard=1.
resource "kubernetes_config_map_v1" "bluegreen_dashboard" {
  metadata {
    name      = "koalatech-bluegreen-dashboard"
    namespace = kubernetes_namespace_v1.monitoring.metadata[0].name
    labels    = merge(local.common_labels, { grafana_dashboard = "1" })
  }

  data = {
    "koalatech-bluegreen.json" = file("${path.module}/monitoring/bluegreen-dashboard.json")
  }

  depends_on = [helm_release.monitoring]
}
