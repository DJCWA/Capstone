########################
# S3 cross-region DR for CLEAN uploads
########################

# Secondary provider in DR region (e.g., us-east-1)
provider "aws" {
  alias  = "dr"
  region = var.dr_region
}

# DR bucket that receives only CLEAN objects from primary region
resource "aws_s3_bucket" "uploads_dr" {
  provider      = aws.dr
  bucket        = "${var.app_name}-uploads-dr"
  force_destroy = true

  tags = {
    Name        = "${var.app_name}-uploads-dr"
    Environment = "prod"
    Purpose     = "dr-clean-uploads"
  }
}

resource "aws_s3_bucket_versioning" "uploads_dr_versioning" {
  provider = aws.dr
  bucket   = aws_s3_bucket.uploads_dr.id

  versioning_configuration {
    status = "Enabled"
  }
}

# IAM role used by S3 for replication from CLEAN bucket -> DR bucket
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
}

data "aws_iam_policy_document" "s3_replication_policy_doc" {
  statement {
    sid    = "AllowReplicationFromCleanToDr"
    effect = "Allow"

    actions = [
      "s3:GetReplicationConfiguration",
      "s3:ListBucket"
    ]

    resources = [
      aws_s3_bucket.uploads_clean.arn
    ]
  }

  statement {
    sid    = "AllowObjectRead"
    effect = "Allow"

    actions = [
      "s3:GetObjectVersion",
      "s3:GetObjectVersionAcl",
      "s3:GetObjectVersionTagging"
    ]

    resources = [
      "${aws_s3_bucket.uploads_clean.arn}/*"
    ]
  }

  statement {
    sid    = "AllowObjectWrite"
    effect = "Allow"

    actions = [
      "s3:ReplicateObject",
      "s3:ReplicateDelete",
      "s3:ReplicateTags",
      "s3:GetObjectVersionTagging",
      "s3:PutObjectVersionTagging"
    ]

    resources = [
      aws_s3_bucket.uploads_dr.arn,
      "${aws_s3_bucket.uploads_dr.arn}/*"
    ]
  }
}

resource "aws_iam_role_policy" "s3_replication_policy" {
  name   = "${var.app_name}-s3-replication-policy"
  role   = aws_iam_role.s3_replication_role.id
  policy = data.aws_iam_policy_document.s3_replication_policy_doc.json
}

# Replicate only from CLEAN bucket to DR bucket
resource "aws_s3_bucket_replication_configuration" "uploads_clean_replication" {
  bucket = aws_s3_bucket.uploads_clean.id
  role   = aws_iam_role.s3_replication_role.arn

  rule {
    id     = "replicate-clean-to-dr"
    status = "Enabled"

    filter {
      prefix = ""
    }

    destination {
      bucket        = aws_s3_bucket.uploads_dr.arn
      storage_class = "STANDARD"
    }
  }

  depends_on = [
    aws_s3_bucket_versioning.uploads_clean_versioning,
    aws_s3_bucket_versioning.uploads_dr_versioning
  ]
}
