variable "region" {
  description = "Primary AWS region for all resources"
  type        = string
  default     = "ca-central-1"
}

variable "dr_region" {
  description = "DR AWS region used for cross-region replication"
  type        = string
  default     = "us-east-1"
}

variable "app_name" {
  description = "Prefix for resource names (must be globally unique for S3 buckets)"
  type        = string
  default     = "allen-capstone-group6"
}

variable "frontend_image" {
  description = "ECR image URI for the frontend container"
  type        = string
}

variable "backend_image" {
  description = "ECR image URI for the backend container"
  type        = string
}

variable "clamav_layer_arn" {
  description = "ARN of the pre-built ClamAV Lambda layer"
  type        = string
}
