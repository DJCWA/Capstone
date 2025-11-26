########################
# S3 bucket for uploads
########################

resource "aws_s3_bucket" "uploads" {
  bucket = "${var.app_name}-uploads"

  force_destroy = true

  tags = {
    Name = "${var.app_name}-uploads"
  }
}

# NEW STYLE: separate versioning resource (no deprecation warning)
resource "aws_s3_bucket_versioning" "uploads_versioning" {
  bucket = aws_s3_bucket.uploads.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "uploads_pab" {
  bucket = aws_s3_bucket.uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

########################
# IAM Role for Lambda
########################

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_scan_role" {
  name               = "${var.app_name}-lambda-scan-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_scan_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "lambda_s3_access" {
  role       = aws_iam_role.lambda_scan_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}

########################
# Package Lambda code
########################

data "archive_file" "lambda_scan_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda-scan"
  output_path = "${path.module}/../lambda-scan.zip"
}

########################
# Lambda function
########################

resource "aws_lambda_function" "scan" {
  function_name = "${var.app_name}-scan-file"
  role          = aws_iam_role.lambda_scan_role.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.12"
  filename      = data.archive_file.lambda_scan_zip.output_path
  timeout       = 60
  memory_size   = 1024

  # If you’ve created the ClamAV layer already, add its ARN here:
  # layers = [
  #   "arn:aws:lambda:ca-central-1:123456789012:layer:allen-capstone-clamav:1"
  # ]

  environment {
    variables = {}
  }
}

########################
# S3 → Lambda notification
########################

resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id  = "AllowExecutionFromS3"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scan.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.uploads.arn
}

resource "aws_s3_bucket_notification" "uploads_notification" {
  bucket = aws_s3_bucket.uploads.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.scan.arn
    events              = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_lambda_permission.allow_s3_invoke]
}
