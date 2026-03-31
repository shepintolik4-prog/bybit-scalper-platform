# Каркас: задайте vpc_id и дополните subnets, security groups, ALB, RDS по политике организации.
# Использование: terraform init && terraform plan -var="vpc_id=vpc-xxxxx"

data "aws_vpc" "main" {
  id = var.vpc_id
}

resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster"
}

# Далее: aws_ecs_task_definition, aws_ecs_service, aws_lb, aws_lb_target_group —
# см. infra/aws/README.md и пример task-definition.json.
