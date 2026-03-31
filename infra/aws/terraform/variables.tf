variable "aws_region" {
  type        = string
  description = "Регион AWS"
  default     = "eu-central-1"
}

variable "project_name" {
  type        = string
  default     = "bybit-scalper"
}

variable "vpc_id" {
  type        = string
  description = "Существующий VPC (для data source)"
}
