variable "region" {
  type    = string
  default = "ca-central-1"
}

variable "app_name" {
  type    = string
  default = "allen-capstone"
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "db_subnet_ids" {
  type = list(string)
}
