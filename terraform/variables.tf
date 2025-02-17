variable "tags" {
  type = map(string)
  default = {
    Name    = "QuizPleaseNotifier"
    Project = "QuizPlease"
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "aws_profile" {
  type    = string
  default = "default"

}

variable "aws_credentials_file" {
  type    = list(string)
  default = ["$HOME/.aws/credentials"]
}

variable "bot_token" {
  type = string
}

variable "group_id" {
  type = string
}

variable "dynamodb_reg_table_arn" {
  type = string
}

variable "dynamodb_reg_table_name" {
  type    = string
  default = "QuizPleaseReg"
}

variable "resource_name" {
  description = "The prefix for all resource names"
  type        = string
  default     = "QuizPleaseNotifier"
}
