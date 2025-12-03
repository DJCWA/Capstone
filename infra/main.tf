terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote backend for Terraform state
  backend "s3" {
    bucket = "allen-capstone-tf-state"
    key    = "ecs-dr/terraform.tfstate"
    region = "ca-central-1"
  }
}

# Primary provider (app / ECS / primary S3 / DynamoDB / Lambda, etc.)
provider "aws" {
  region = var.region
}

# DR provider (used for DR S3 bucket in a different region)
provider "aws" {
  alias  = "dr"
  region = var.dr_region
}
