#!/usr/bin/env python3
"""PesaGuard infrastructure.

Stacks (deploy order is handled by CDK through their dependencies):
  PesaGuardNetwork     VPC with public subnets only, no NAT gateway
  PesaGuardData        DynamoDB payments table
  PesaGuardEcr         container registry, scan on push
  PesaGuardGithubOidc  lets GitHub Actions deploy without stored AWS keys
  PesaGuardService     ECS Fargate service behind an ALB, native blue/green

Context values (pass with -c key=value):
  imageTag      image tag in ECR to run, e.g. v1.0.0 (needed for PesaGuardService)
  githubRepo    owner/repo allowed to deploy (default in cdk.json)
  retainData    "true" keeps the DynamoDB table on cdk destroy (default false)
"""

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from stacks.data_stack import DataStack
from stacks.ecr_stack import EcrStack
from stacks.github_oidc_stack import GithubOidcStack
from stacks.network_stack import NetworkStack
from stacks.service_stack import ServiceStack

app = cdk.App()

image_tag = app.node.try_get_context("imageTag") or "not-set"
github_repo = app.node.try_get_context("githubRepo")
retain_data = str(app.node.try_get_context("retainData")).lower() == "true"

network = NetworkStack(app, "PesaGuardNetwork")
data = DataStack(app, "PesaGuardData", retain_data=retain_data)
ecr = EcrStack(app, "PesaGuardEcr")
GithubOidcStack(app, "PesaGuardGithubOidc", github_repo=github_repo, repository=ecr.repository)
ServiceStack(
    app,
    "PesaGuardService",
    vpc=network.vpc,
    table=data.table,
    repository=ecr.repository,
    image_tag=image_tag,
)

cdk.Tags.of(app).add("project", "pesaguard")
cdk.Tags.of(app).add("managed-by", "cdk")

# Security and best-practice checks on every synth. Findings we accept on
# purpose are suppressed in each stack, with the reason written next to them.
cdk.Validations.of(app).add_plugins(AwsSolutionsChecks(app))

app.synth()
