variable "region" {
  description = "Primary AWS region for all resources"
  type        = string
  default     = "ca-central-1"
}

variable "dr_region" {
  description = "Secondary DR region used for cross-region replication"
  type        = string
  default     = "us-east-1"
}

variable "app_name" {
  description = "Prefix for resource names"
  type        = string
  default     = "group6-capstone"
}

variable "frontend_image" {
  description = "ECR image URI for the frontend container"
  type        = string
}

variable "backend_image" {
  description = "ECR image URI for the backend container"
  type        = string
}
