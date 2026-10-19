"""Lets GitHub Actions deploy to AWS with no stored access keys.

How it works: when a workflow runs, GitHub issues a short-lived signed token
saying "this is repo X, branch Y". AWS checks the signature against the OIDC
provider below and, if the repo and branch match the trust policy, hands back
temporary credentials for the deploy role. Nothing secret lives in GitHub.

Who may assume the role: only pushes to main and version tags (v*) of ONE repo.
Pull requests cannot, so a malicious PR can never deploy.
"""

import aws_cdk as cdk
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from constructs import Construct

from .nag import acknowledge

GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"


class GithubOidcStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        github_repo: str,
        repository: ecr.IRepository,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not github_repo or "/" not in github_repo:
            raise ValueError("githubRepo context must look like owner/repo")

        # An account can hold only ONE provider for this URL. If you already
        # created one by hand, pass -c existingOidcProviderArn=arn:... instead.
        existing_arn = self.node.try_get_context("existingOidcProviderArn")
        if existing_arn:
            provider_arn = existing_arn
        else:
            provider = iam.OidcProviderNative(
                self,
                "GithubProvider",
                url=GITHUB_OIDC_URL,
                client_ids=["sts.amazonaws.com"],
            )
            provider_arn = provider.oidc_provider_arn

        self.deploy_role = iam.Role(
            self,
            "DeployRole",
            role_name="pesaguard-github-deploy",
            description="Assumed by GitHub Actions to build, push and deploy PesaGuard",
            max_session_duration=cdk.Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(
                provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                    },
                    "StringLike": {
                        "token.actions.githubusercontent.com:sub": [
                            f"repo:{github_repo}:ref:refs/heads/main",
                            f"repo:{github_repo}:ref:refs/tags/v*",
                        ]
                    },
                },
            ),
        )

        # Push images to our one repository only, and check whether a tag exists.
        repository.grant_pull_push(self.deploy_role)
        repository.grant(self.deploy_role, "ecr:DescribeImages")

        # Read the service stack's outputs (the API address) for the job summary.
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudformation:DescribeStacks"],
                resources=[
                    f"arn:aws:cloudformation:{self.region}:{self.account}:stack/PesaGuardService/*"
                ],
            )
        )

        # Let the pipeline run `cdk deploy` by assuming the roles that
        # `cdk bootstrap` created. Those roles do the actual deployment, so
        # this role itself needs no broad permissions.
        self.deploy_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[
                    f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-*-role-{self.account}-*"
                ],
            )
        )

        acknowledge(
            self.deploy_role,
            {
                "AwsSolutions-IAM5[Resource::*]": "ecr:GetAuthorizationToken only "
                "supports resource '*'. All other ECR actions are scoped to our repository.",
                "AwsSolutions-IAM5[Resource::arn:aws:iam::<AWS::AccountId>:role/"
                "cdk-hnb659fds-*-role-<AWS::AccountId>-*]": "Limited to the roles created "
                "by `cdk bootstrap` in this account; they perform the actual deployment.",
                "AwsSolutions-IAM5[Resource::arn:aws:cloudformation:<AWS::Region>:"
                "<AWS::AccountId>:stack/PesaGuardService/*]": "Read-only DescribeStacks on the "
                "one service stack; the suffix after the stack name is a generated ID.",
            },
        )

        cdk.CfnOutput(
            self,
            "DeployRoleArn",
            value=self.deploy_role.role_arn,
            description="Save as the AWS_DEPLOY_ROLE_ARN variable in GitHub",
        )
