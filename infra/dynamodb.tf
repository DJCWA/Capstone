########################################
# DynamoDB table for file scan results
########################################

resource "aws_dynamodb_table" "scan_results" {
  name = "allen-capstone-scan-results"

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
    Project     = "allen-capstone"
    Environment = "dev"
  }
}

output "scan_results_table_name" {
  value = aws_dynamodb_table.scan_results.name
}

output "scan_results_table_arn" {
  value = aws_dynamodb_table.scan_results.arn
}
