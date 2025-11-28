################################
# Disaster Recovery (DR) resources
# - S3 cross-region replication for uploads bucket
# - DynamoDB DR handled via Global Table in dynamodb.tf
################################

# DR uploads bucket in secondary region
resource "aws_s3_bucket" "uploads_dr" {
  provider = aws.dr

  bucket        = "${var.app_name}-uploads-dr"
  force_destroy = true

  tags = {
    Name        = "${var.app_name}-uploads-dr"
    Environment = "prod"
    Role        = "dr"
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

# Optional: server-side encryption on DR bucket
resource "aws_s3_bucket_server_side_encryption_configuration" "uploads_dr_sse" {
  provider = aws.dr
  bucket   = aws_s3_bucket.uploads_dr.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# IAM role that S3 uses to replicate objects
resource "aws_iam_role" "s3_replication_role" {
  name = "${var.app_name}-s3-replication-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow",
      Principal = {
        Service = "s3.amazonaws.com"
      },
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "s3_replication_policy" {
  name = "${var.app_name}-s3-replication-policy"
  role = aws_iam_role.s3_replication_role.id

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "s3:GetReplicationConfiguration",
          "s3:ListBucket"
        ],
        Resource = [
          aws_s3_bucket.uploads.arn
        ]
      },
      {
        Effect = "Allow",
        Action = [
          "s3:GetObjectVersion",
          "s3:GetObjectVersionAcl",
          "s3:GetObjectVersionTagging"
        ],
        Resource = [
          "${aws_s3_bucket.uploads.arn}/*"
        ]
      },
      {
        Effect = "Allow",
        Action = [
          "s3:ReplicateObject",
          "s3:ReplicateDelete",
          "s3:ReplicateTags",
          "s3:ObjectOwnerOverrideToBucketOwner"
        ],
        Resource = [
          "${aws_s3_bucket.uploads_dr.arn}/*"
        ]
      }
    ]
  })
}

# Replication configuration on primary uploads bucket
resource "aws_s3_bucket_replication_configuration" "uploads_replication" {
  bucket = aws_s3_bucket.uploads.id
  role   = aws_iam_role.s3_replication_role.arn

  depends_on = [
    aws_s3_bucket_versioning.uploads_versioning,
    aws_s3_bucket_versioning.uploads_dr_versioning
  ]

  rule {
    id     = "replicate-uploads-to-dr"
    status = "Enabled"

    delete_marker_replication {
      status = "Disabled"
    }

    filter {
      prefix = ""
    }

    destination {
      bucket        = aws_s3_bucket.uploads_dr.arn
      storage_class = "STANDARD"
    }
  }
}
