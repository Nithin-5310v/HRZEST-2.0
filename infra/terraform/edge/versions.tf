terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.60"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = merge({ Project = "hrzest", Layer = "edge", ManagedBy = "terraform" }, var.tags)
  }
}
