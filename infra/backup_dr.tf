############################################
# Cross-region S3 replication for DR
# - Primary bucket: aws_s3_bucket.uploads (var.region)
# - DR bucket:      aws_s3_bucket.uploads_dr (var.dr_region via provider alias "dr")
############################################

# IAM trust policy: allow S3 to assume the replication role
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

# IAM role S3 uses for replication
resource "aws_iam_role" "s3_replication_role" {
  name               = "${var.app_name}-s3-replication-role"
  assume_role_policy = data.aws_iam_policy_document.s3_replication_assume_role.json
}

# IAM policy attached to the replication role
data "aws_iam_policy_document" "s3_replication_policy" {
  # Allow S3 to read replication configuration and list the source bucket
  statement {
    effect = "Allow"
    actions = [
      "s3:GetReplicationConfiguration",
      "s3:ListBucket"
    ]
    resources = [
      aws_s3_bucket.uploads.arn
    ]
  }

  # Allow S3 to read versions and metadata from the source bucket
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObjectVersion",
      "s3:GetObjectVersionAcl",
      "s3:GetObjectVersionTagging"
    ]
    resources = [
      "${aws_s3_bucket.uploads.arn}/*"
    ]
  }

  # Allow S3 to replicate into the DR bucket
  statement {
    effect = "Allow"
    actions = [
      "s3:ReplicateObject",
      "s3:ReplicateDelete",
      "s3:ReplicateTags",
      "s3:GetObjectVersionTagging",
      "s3:PutObjectVersionTagging",
      "s3:ObjectOwnerOverrideToBucketOwner"
    ]
    resources = [
      "${aws_s3_bucket.uploads_dr.arn}/*"
    ]
  }
}

resource "aws_iam_role_policy" "s3_replication_policy" {
  name   = "${var.app_name}-s3-replication-policy"
  role   = aws_iam_role.s3_replication_role.id
  policy = data.aws_iam_policy_document.s3_replication_policy.json
}

############################################
# DR bucket in secondary region
############################################

resource "aws_s3_bucket" "uploads_dr" {
  provider = aws.dr

  bucket = "${var.app_name}-uploads-dr"

  force_destroy = true

  tags = {
    Name        = "${var.app_name}-uploads-dr"
    Environment = "prod"
    Component   = "dr"
  }
}

# Enable versioning on DR bucket (required for replication)
resource "aws_s3_bucket_versioning" "uploads_dr_versioning" {
  provider = aws.dr
  bucket   = aws_s3_bucket.uploads_dr.id

  versioning_configuration {
    status = "Enabled"
  }
}

############################################
# Replication configuration on primary bucket
############################################

resource "aws_s3_bucket_replication_configuration" "uploads_replication" {
  bucket = aws_s3_bucket.uploads.id
  role   = aws_iam_role.s3_replication_role.arn

  rule {
    id     = "replicate-to-dr"
    status = "Enabled"

    delete_marker_replication {
      status = "Enabled"
    }

    filter {
      prefix = ""
    }

    destination {
      bucket        = aws_s3_bucket.uploads_dr.arn
      storage_class = "STANDARD"
    }
  }

  # Ensure versioning is enabled on both buckets before replication is applied
  depends_on = [
    aws_s3_bucket_versioning.uploads_versioning,
    aws_s3_bucket_versioning.uploads_dr_versioning
  ]
}
