"""Container registry for PesaGuard images.

Tags are IMMUTABLE: once v1.2.0 is pushed it can never be overwritten. That
guarantees "v1.2.0" means the same image everywhere, which matters a lot when
you are working out which release caused a problem.
"""

import aws_cdk as cdk
from aws_cdk import aws_ecr as ecr
from constructs import Construct

REPOSITORY_NAME = "pesaguard"


class EcrStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.repository = ecr.Repository(
            self,
            "Repository",
            repository_name=REPOSITORY_NAME,
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep the 15 most recent images",
                    max_image_count=15,
                )
            ],
            removal_policy=cdk.RemovalPolicy.DESTROY,
            empty_on_delete=True,
        )

        cdk.CfnOutput(self, "RepositoryUri", value=self.repository.repository_uri)
