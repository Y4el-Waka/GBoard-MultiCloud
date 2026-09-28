# Phase 1.0B deployment runbook (not executed in Phase 1.0A)

This runbook is preparatory. Every AWS command requires an explicitly
authorized laboratory role. Stop if the caller identity, Region, plan or cost
does not match the reviewed values.

## 1. Review and validate

1. Confirm remote CI passed for the exact commit and download its evidence.
2. Re-run Python/JavaScript suites and the Docker smoke locally if the commit
   changed.
3. Configure ignored `backend.hcl` and `terraform.tfvars`.
4. Run `aws sts get-caller-identity` and compare the account to the local
   allow-list.
5. Run `terraform init`, `terraform validate` and `terraform plan`; never use
   `-auto-approve` for the first deployment.
6. Pin the planned `resolved_ami_id` as `ami_id_override`, run a second plan and
   obtain human review of resources, IAM and estimated cost.

## 2. Publish the reviewed image privately

Terraform creates the repository, so the first deployment is two controlled
steps: apply only the reviewed ECR/network/compute plan, then publish the exact
validated image. No GitHub workflow currently has AWS publish credentials.

After the repository exists, retrieve its URL without copying an account ID into
source:

```bash
repository_url="$(terraform output -raw ecr_repository_url)"
registry="${repository_url%%/*}"
image_tag="$(terraform console <<< 'var.image_tag' | tr -d '"')"
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin "$registry"
docker tag coreview-cloud-lab:0.5c "$repository_url:$image_tag"
docker push "$repository_url:$image_tag"
```

Verify the pushed digest matches the reviewed local image and ECR scan policy.
Do not reuse the immutable tag for another image.

## 3. Initialize runtime secrets and start

Wait until Systems Manager reports the instance online. Amazon Linux 2023 ships
with SSM Agent, and the bootstrap explicitly enables it. Start an interactive
session:

```bash
instance_id="$(terraform output -raw instance_id)"
aws ssm start-session --region us-east-1 --target "$instance_id"
```

Inside the authorized session:

```bash
sudo systemctl status amazon-ssm-agent --no-pager
sudo cloud-init status --wait
sudo /opt/coreview/bin/initialize-lab-secrets
sudo systemctl status coreview-lab --no-pager
sudo docker compose --env-file /opt/coreview/.env \
  --file /opt/coreview/compose.yaml ps
```

Enter a unique synthetic-only administrator password at the hidden prompt. The
bootstrap accepts at least 16 letters, digits, dots, underscores or hyphens;
this explicit alphabet prevents Compose or JSON interpolation. Do not put the
password in a command, SSM Run Command parameter, Terraform variable or ticket.

## 4. Open the dashboard tunnel

Install AWS CLI v2 and the Session Manager plugin on the laptop. Keep this
foreground session open:

```bash
instance_id="$(terraform output -raw instance_id)"
aws ssm start-session \
  --region us-east-1 \
  --target "$instance_id" \
  --document-name AWS-StartPortForwardingSession \
  --parameters 'portNumber=5067,localPortNumber=5067'
```

Then visit `http://127.0.0.1:5067`. AWS requires SSM Agent 2.3.672.0 or later
for port forwarding and the local Session Manager plugin; see
[Start a session](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html).

## 5. Acceptance

Run the synthetic smoke through the tunnel and record evidence for login,
13 servers, 1,000 sensors, zero collector errors, legacy history, metadata v2,
Ping/CPU/Memory/Traffic/SFP history, pause/resume and persistence after service
restart. Confirm the security group still has zero ingress, the real PRTG
provider is disabled, ports listen only on host loopback, no unexpected public
DNS/IPv4 resources exist and AWS Cost Explorer/Budget tags are visible.

Do not contact real PRTG during this acceptance.
