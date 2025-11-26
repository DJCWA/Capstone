resource "aws_s3_bucket" "uploads" {
  bucket = "${var.app_name}-uploads"
  versioning {
    enabled = true
  }
}

resource "aws_s3_bucket" "uploads_dr" {
  bucket = "${var.app_name}-uploads-dr"
}

resource "aws_s3_bucket_replication_configuration" "uploads_replication" {
  bucket = aws_s3_bucket.uploads.id
  role   = aws_iam_role.s3_replication_role.arn

  rule {
    id     = "replicate-all"
    status = "Enabled"

    filter {}

    destination {
      bucket        = aws_s3_bucket.uploads_dr.arn
      storage_class = "STANDARD"
    }
  }
}

# IAM role for replication (policy omitted for brevity)

resource "aws_iam_role" "lambda_scan_role" {
  name               = "${var.app_name}-lambda-scan-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_scan_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "lambda_s3_full" {
  role       = aws_iam_role.lambda_scan_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}

data "archive_file" "lambda_scan_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda-scan"
  output_path = "${path.module}/../lambda-scan.zip"
}

resource "aws_lambda_function" "scan" {
  function_name = "${var.app_name}-scan-file"
  role          = aws_iam_role.lambda_scan_role.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.12"
  filename      = data.archive_file.lambda_scan_zip.output_path
  timeout       = 60

  environment {
    variables = {
      # Add env vars if needed
    }
  }
}

resource "aws_s3_bucket_notification" "uploads_notification" {
  bucket = aws_s3_bucket.uploads.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.scan.arn
    events              = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_lambda_permission.allow_s3_invoke]
}

resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id  = "AllowExecutionFromS3"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scan.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.uploads.arn
}
