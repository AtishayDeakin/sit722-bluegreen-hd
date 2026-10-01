terraform {
  required_version = ">= 1.7.0"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.2"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.3"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  # State lives OUTSIDE the repository so the pipeline's checkout step can
  # never delete it. The path is supplied at init time:
  #   terraform init -backend-config="path=$HOME/.koalatech/terraform.tfstate"
  backend "local" {}
}
