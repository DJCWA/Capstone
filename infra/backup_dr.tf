##############################################
# backup_dr.tf – AWS Backup + S3 Replication
##############################################

# -------------------------------
# AWS Backup for DynamoDB (DRaaS)
# -------------------------------

resource "aws_iam_role" "backup_role" {
  name = "${var.app_name}-backup-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "backup.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Project = var.app_name
    Purpose = "AWS Backup service role"
  }
}

resource "aws_iam_role_policy_attachment" "backup_role_policy" {
  role       = aws_iam_role.backup_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_backup_vault" "main" {
  name = "${var.app_name}-backup-vault"

  tags = {
    Project = var.app_name
  }
}

resource "aws_backup_plan" "daily" {
  name = "${var.app_name}-daily-backup-plan"

  rule {
    rule_name         = "daily-backup"
    target_vault_name = aws_backup_vault.main.name

    # Daily at 05:00 UTC – adjust if needed
    schedule = "cron(0 5 * * ? *)"

    lifecycle {
      delete_after = 30
    }
  }

  tags = {
    Project = var.app_name
  }
}

# Back up the DynamoDB scan results table (defined in dynamodb.tf as aws_dynamodb_table.scan_results)
resource "aws_backup_selection" "dynamodb_selection" {
  name         = "${var.app_name}-dynamodb-selection"
  plan_id      = aws_backup_plan.daily.id
  iam_role_arn = aws_iam_role.backup_role.arn

  resources = [
    aws_dynamodb_table.scan_results.arn
  ]
}

# --------------------------------------
# S3 Cross-Region Replication for Uploads
# --------------------------------------
# Uses:
#   - aws_s3_bucket.uploads (defined in s3_lambda.tf)
#   - aws_s3_bucket.uploads_dr (defined below in this file using the DR provider)
#   - aws_s3_bucket_versioning.uploads_versioning (defined in s3_lambda.tf)
#   - aws_s3_bucket_versioning.uploads_dr_versioning (defined below in this file)
#

# DR uploads bucket in the DR region
resource "aws_s3_bucket" "uploads_dr" {
  provider      = aws.dr
  bucket        = "${var.app_name}-uploads-dr"
  force_destroy = true

  tags = {
    Name        = "${var.app_name}-uploads-dr"
    Environment = "prod"
    Component   = "uploads-dr"
    Owner       = "group6"
  }
}

# Enable versioning on the DR bucket (required for replication)
resource "aws_s3_bucket_versioning" "uploads_dr_versioning" {
  provider = aws.dr
  bucket   = aws_s3_bucket.uploads_dr.id

  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "s3_replication_policy" {
  # Allow S3 to read replication configuration and list the source bucket
  statement {
    sid = "AllowReplicationSourceConfiguration"
    actions = [
      "s3:GetReplicationConfiguration",
      "s3:ListBucket"
    ]
    resources = [
      aws_s3_bucket.uploads.arn
    ]
  }

  # Allow access to object versions in source + destination
  statement {
    sid = "AllowReplicationObjectActions"
    actions = [
      "s3:GetObjectVersion",
      "s3:GetObjectVersionAcl",
      "s3:GetObjectVersionTagging",
      "s3:GetObjectVersionForReplication",
      "s3:GetObjectLegalHold",
      "s3:GetObjectRetention",
      "s3:ReplicateObject",
      "s3:ReplicateDelete",
      "s3:ReplicateTags",
      "s3:PutObjectLegalHold",
      "s3:PutObjectRetention",
      "s3:PutObjectAcl",
      "s3:PutObjectTagging"
    ]
    resources = [
      "${aws_s3_bucket.uploads.arn}/*",
      "${aws_s3_bucket.uploads_dr.arn}/*"
    ]
  }

  # Allow writes into the destination bucket
  statement {
    sid = "AllowDestinationWrite"
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

resource "aws_iam_role" "s3_replication_role" {
  name = "${var.app_name}-s3-replication-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "s3.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Project = var.app_name
    Purpose = "S3 Cross-Region Replication"
  }
}

resource "aws_iam_role_policy" "s3_replication_role_policy" {
  name   = "${var.app_name}-s3-replication-policy"
  role   = aws_iam_role.s3_replication_role.id
  policy = data.aws_iam_policy_document.s3_replication_policy.json
}

resource "aws_s3_bucket_replication_configuration" "uploads_replication" {
  bucket = aws_s3_bucket.uploads.id
  role   = aws_iam_role.s3_replication_role.arn

  rule {
    id     = "replicate-all-objects"
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

  # Ensure versioning is enabled on both buckets before applying replication
  depends_on = [
    aws_s3_bucket_versioning.uploads_versioning,
    aws_s3_bucket_versioning.uploads_dr_versioning,
  ]
}
