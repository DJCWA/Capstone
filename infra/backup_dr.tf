// infra/backup_dr.tf
// Cross-region S3 replication for scanned uploads (primary -> DR region)

############################
# DR S3 bucket (target)
############################

resource "aws_s3_bucket" "uploads_dr" {
  provider = aws.dr
  bucket   = "${var.app_name}-uploads-dr"

  force_destroy = true

  tags = {
    Name        = "${var.app_name}-uploads-dr"
    Environment = "prod"
    Component   = "dr-uploads"
    Owner       = "group6"
  }
}

resource "aws_s3_bucket_versioning" "uploads_dr_versioning" {
  provider = aws.dr
  bucket   = aws_s3_bucket.uploads_dr.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads_dr_sse" {
  provider = aws.dr
  bucket   = aws_s3_bucket.uploads_dr.bucket

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "uploads_dr_pab" {
  provider = aws.dr
  bucket   = aws_s3_bucket.uploads_dr.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

############################
# Replication IAM Role
############################

data "aws_iam_policy_document" "s3_replication_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "s3_replication_role" {
  name               = "${var.app_name}-s3-replication-role"
  assume_role_policy = data.aws_iam_policy_document.s3_replication_assume_role.json

  tags = {
    Name  = "${var.app_name}-s3-replication-role"
    Owner = "group6"
  }
}

data "aws_iam_policy_document" "s3_replication_policy" {
  # Allow S3 to read from source bucket (primary region)
  statement {
    sid    = "AllowReplicationConfigOnSource"
    effect = "Allow"

    actions = [
      "s3:GetReplicationConfiguration",
      "s3:ListBucket",
    ]

    resources = [
      aws_s3_bucket.uploads.arn,
    ]
  }

  statement {
    sid    = "AllowObjectReadsFromSource"
    effect = "Allow"

    actions = [
      "s3:GetObjectVersion",
      "s3:GetObjectVersionAcl",
      "s3:GetObjectVersionTagging",
    ]

    resources = [
      "${aws_s3_bucket.uploads.arn}/*",
    ]
  }

  # Allow writes into destination (DR bucket)
  statement {
    sid    = "AllowReplicationToDestination"
    effect = "Allow"

    actions = [
      "s3:ReplicateObject",
      "s3:ReplicateDelete",
      "s3:ReplicateTags",
      "s3:GetObjectVersionTagging",
      "s3:PutBucketVersioning",
      "s3:PutBucketTagging",
    ]

    resources = [
      aws_s3_bucket.uploads_dr.arn,
      "${aws_s3_bucket.uploads_dr.arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "s3_replication_policy" {
  name   = "${var.app_name}-s3-replication-policy"
  role   = aws_iam_role.s3_replication_role.id
  policy = data.aws_iam_policy_document.s3_replication_policy.json
}

############################
# S3 Replication Configuration
############################

resource "aws_s3_bucket_replication_configuration" "uploads_replication" {
  # Source bucket is defined in s3_lambda.tf as aws_s3_bucket.uploads
  bucket = aws_s3_bucket.uploads.id
  role   = aws_iam_role.s3_replication_role.arn

  rule {
    id     = "replicate-clean-uploads"
    status = "Enabled"

    # Replicate everything in the bucket
    filter {}

    destination {
      bucket        = aws_s3_bucket.uploads_dr.arn
      storage_class = "STANDARD"

      # Optional metrics; not required for basic DR
      metrics {
        status = "Disabled"
      }

      # We are NOT using S3 Replication Time Control, so we omit replication_time
    }
  }

  # Make sure the IAM role and both buckets exist first
  depends_on = [
    aws_iam_role_policy.s3_replication_policy,
    aws_s3_bucket.uploads,
    aws_s3_bucket.uploads_dr,
    aws_s3_bucket_versioning.uploads_dr_versioning,
  ]
}
