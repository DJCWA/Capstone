########################
# DynamoDB table to track scan results / metadata
########################

resource "aws_dynamodb_table" "scan_results" {
  name         = "${var.app_name}-scan-results"
  billing_mode = "PAY_PER_REQUEST"

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

  tags = {
    Name        = "${var.app_name}-scan-results"
    Environment = "prod"
  }
}
