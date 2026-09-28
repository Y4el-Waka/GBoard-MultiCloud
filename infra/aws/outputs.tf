output "aws_region" {
  description = "Deployment Region."
  value       = var.aws_region
}

output "instance_id" {
  description = "EC2 target used by Session Manager."
  value       = aws_instance.coreview.id
}

output "resolved_ami_id" {
  description = "AL2023 AMI resolved for the reviewed plan. Pin it through ami_id_override before apply."
  value       = local.resolved_ami_id
}

output "ecr_repository_url" {
  description = "Private ECR repository for the reviewed CoreView image."
  value       = aws_ecr_repository.coreview.repository_url
}

output "vpc_id" {
  description = "Dedicated laboratory VPC ID."
  value       = aws_vpc.lab.id
}

output "subnet_id" {
  description = "Single laboratory subnet ID."
  value       = aws_subnet.lab.id
}

output "availability_zone" {
  description = "Availability Zone selected for the single subnet."
  value       = aws_subnet.lab.availability_zone
}

output "ssm_port_forward_command" {
  description = "Template command for a local tunnel to the dashboard."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.coreview.id} --document-name AWS-StartPortForwardingSession --parameters portNumber=5067,localPortNumber=5067"
}
