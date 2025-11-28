################################
# DynamoDB table for scan results
################################

resource "aws_dynamodb_table" "scan_results" {
  name         = "${var.app_name}-scan-results"
  billing_mode = "PAY_PER_REQUEST"

  # Primary key: file_id + scan_timestamp
  hash_key  = "file_id"
  range_key = "scan_timestamp"

  attribute {
    name = "file_id"
    type = "S"
  }

  attribute {
    name = "scan_timestamp"
    type = "S"
  }

  # Point-in-time recovery = regional DR (restore to any second in last 35 days)
  point_in_time_recovery {
    enabled = true
  }

  # Global Table replica in the DR region for multi-region DR
  replica {
    region_name = var.dr_region
  }

  tags = {
    Name        = "${var.app_name}-scan-results"
    Environment = "prod"
  }
}
