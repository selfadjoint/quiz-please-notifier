terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.26"
    }
  }

  required_version = ">= 1.10.0"
  backend "s3" {}
}


provider "aws" {
  region                   = var.aws_region
  shared_credentials_files = var.aws_credentials_file
  profile                  = var.aws_profile
}

# Archive the Lambda code directory into a zip file.
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/lambda.zip"
}

resource "aws_iam_role" "lambda_execution_role" {
  name = var.resource_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action = "sts:AssumeRole",
        Effect = "Allow",
        Sid    = "",
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_policy_attachment" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "notifier" {
  description      = "Game notifier"
  function_name    = var.resource_name
  role             = aws_iam_role.lambda_execution_role.arn
  handler          = "main.lambda_handler"
  runtime          = "python3.11"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  timeout          = 300

  environment {
    variables = {
      BOT_TOKEN   = var.bot_token
      GROUP_ID    = var.group_id
      DB_HOST     = var.db_host
      DB_PORT     = var.db_port
      DB_NAME     = var.db_name
      DB_USER     = var.db_user
      DB_PASSWORD = var.db_password
    }
  }

  tags = var.tags
}

resource "aws_lambda_permission" "allow_execution" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notifier.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule_rule.arn
}

resource "aws_cloudwatch_event_rule" "schedule_rule" {
  name                = var.resource_name
  description         = "Scheduled rule to notify about games and send ЧГК question"
  schedule_expression = "cron(0 6 ? * * *)" # Runs every day at 06:00 UTC
}

resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.schedule_rule.name
  target_id = var.resource_name
  arn       = aws_lambda_function.notifier.arn
  input     = jsonencode({ "action" : "send_question" })
}

# ЧГК answer trigger at 12:00 UTC (15:00 Moscow)
resource "aws_cloudwatch_event_rule" "answer_rule" {
  name                = "${var.resource_name}-answer"
  description         = "Scheduled rule to send ЧГК answer"
  schedule_expression = "cron(0 12 ? * * *)" # Runs every day at 12:00 UTC
}

resource "aws_cloudwatch_event_target" "answer_target" {
  rule      = aws_cloudwatch_event_rule.answer_rule.name
  target_id = "${var.resource_name}-answer"
  arn       = aws_lambda_function.notifier.arn
  input     = jsonencode({ "action" : "send_answer" })
}

resource "aws_lambda_permission" "allow_answer_execution" {
  statement_id  = "AllowExecutionFromCloudWatchAnswer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notifier.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.answer_rule.arn
}
