resource "aws_backup_vault" "primary" {
  name = "${var.app_name}-backup-vault"
}

resource "aws_backup_plan" "plan" {
  name = "${var.app_name}-backup-plan"

  rule {
    rule_name         = "daily-backup"
    target_vault_name = aws_backup_vault.primary.name
    schedule          = "cron(0 5 * * ? *)" # 5 AM UTC daily
    lifecycle {
      delete_after = 30
    }

    copy_action {
      destination_vault_arn = aws_backup_vault.dr.arn
      lifecycle {
        delete_after = 90
      }
    }
  }
}

resource "aws_backup_vault" "dr" {
  name     = "${var.app_name}-backup-vault-dr"
  provider = aws.dr  # secondary provider for DR region
}

provider "aws" {
  alias  = "dr"
  region = "us-east-1" # example DR region
}

resource "aws_backup_selection" "rds_selection" {
  iam_role_arn = aws_iam_role.backup_role.arn
  name         = "rds-selection"
  plan_id      = aws_backup_plan.plan.id

  resources = [
    aws_db_instance.app_db.arn
  ]
}
