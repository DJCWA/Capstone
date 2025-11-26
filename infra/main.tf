terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket = "allen-capstone-tf-state"
    key    = "ecs-dr/terraform.tfstate"
    region = "ca-central-1"
  }
}

provider "aws" {
  region = var.region
}