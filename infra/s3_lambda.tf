########################
# S3 buckets for uploads (RAW + CLEAN)
########################

resource "aws_s3_bucket" "uploads_raw" {
  bucket        = "${var.app_name}-uploads-raw"
  force_destroy = true

  tags = {
    Name        = "${var.app_name}-uploads-raw"
    Environment = "prod"
    Purpose     = "raw-uploads"
  }
}

resource "aws_s3_bucket_versioning" "uploads_raw_versioning" {
  bucket = aws_s3_bucket.uploads_raw.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket" "uploads_clean" {
  bucket        = "${var.app_name}-uploads-clean"
  force_destroy = true

  tags = {
    Name        = "${var.app_name}-uploads-clean"
    Environment = "prod"
    Purpose     = "clean-uploads"
  }
}

resource "aws_s3_bucket_versioning" "uploads_clean_versioning" {
  bucket = aws_s3_bucket.uploads_clean.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Default encryption for both buckets
resource "aws_s3_bucket_server_side_encryption_configuration" "uploads_raw_sse" {
  bucket = aws_s3_bucket.uploads_raw.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads_clean_sse" {
  bucket = aws_s3_bucket.uploads_clean.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

########################
# Lambda IAM role + policy
########################

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scan_lambda_role" {
  name               = "${var.app_name}-scan-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy_attachment" "scan_lambda_basic" {
  role       = aws_iam_role.scan_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "scan_lambda_policy_doc" {
  statement {
    sid    = "S3Access"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectTagging",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:PutObjectTagging",
      "s3:DeleteObject"
    ]

    resources = [
      "${aws_s3_bucket.uploads_raw.arn}/*",
      "${aws_s3_bucket.uploads_clean.arn}/*"
    ]
  }
}

resource "aws_iam_role_policy" "scan_lambda_policy" {
  name   = "${var.app_name}-scan-lambda-policy"
  role   = aws_iam_role.scan_lambda_role.id
  policy = data.aws_iam_policy_document.scan_lambda_policy_doc.json
}

########################
# Lambda function that scans RAW bucket and promotes to CLEAN bucket
########################

resource "aws_lambda_function" "scan" {
  function_name = "${var.app_name}-clamav-scan"
  role          = aws_iam_role.scan_lambda_role.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.12"

  filename         = "${path.module}/lambda-scan.zip"
  source_code_hash = filebase64sha256("${path.module}/lambda-scan.zip")

  timeout     = 300
  memory_size = 10240

  layers = [var.clamav_layer_arn]

  environment {
    variables = {
      SAFE_BUCKET = aws_s3_bucket.uploads_clean.bucket
    }
  }

  tags = {
    Name = "${var.app_name}-scan-lambda"
  }
}

########################
# S3 -> Lambda trigger on RAW bucket
########################

resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id  = "AllowExecutionFromS3UploadsRaw"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scan.arn
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.uploads_raw.arn
}

resource "aws_s3_bucket_notification" "uploads_raw_notification" {
  bucket = aws_s3_bucket.uploads_raw.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.scan.arn
    events              = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_lambda_permission.allow_s3_invoke]
}
