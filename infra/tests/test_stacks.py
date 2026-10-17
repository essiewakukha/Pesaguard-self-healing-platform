"""Infrastructure tests: assert the security and cost decisions are really in
the generated CloudFormation, so a later change cannot quietly undo them."""

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from stacks.data_stack import DataStack
from stacks.ecr_stack import EcrStack
from stacks.github_oidc_stack import GithubOidcStack
from stacks.network_stack import NetworkStack
from stacks.service_stack import ServiceStack

REPO = "essiewakukha/Pesaguard-self-healing-platform"


@pytest.fixture(scope="module")
def stacks():
    app = cdk.App()
    network = NetworkStack(app, "Network")
    data = DataStack(app, "Data")
    ecr = EcrStack(app, "Ecr")
    oidc = GithubOidcStack(app, "Oidc", github_repo=REPO, repository=ecr.repository)
    service = ServiceStack(
        app,
        "Service",
        vpc=network.vpc,
        table=data.table,
        repository=ecr.repository,
        image_tag="v1.0.0",
    )
    return {
        "network": Template.from_stack(network),
        "data": Template.from_stack(data),
        "ecr": Template.from_stack(ecr),
        "oidc": Template.from_stack(oidc),
        "service": Template.from_stack(service),
    }


# ---- network ------------------------------------------------------------------
def test_no_nat_gateway(stacks):
    stacks["network"].resource_count_is("AWS::EC2::NatGateway", 0)


def test_two_public_subnets(stacks):
    stacks["network"].resource_count_is("AWS::EC2::Subnet", 2)
    stacks["network"].has_resource_properties("AWS::EC2::Subnet", {"MapPublicIpOnLaunch": True})


def test_dynamodb_gateway_endpoint(stacks):
    stacks["network"].has_resource_properties(
        "AWS::EC2::VPCEndpoint", {"VpcEndpointType": "Gateway"}
    )


# ---- data ---------------------------------------------------------------------
def test_table_is_on_demand_with_backups(stacks):
    stacks["data"].has_resource_properties(
        "AWS::DynamoDB::GlobalTable",
        {
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}],
            "Replicas": [
                Match.object_like(
                    {"PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True}}
                )
            ],
        },
    )


def test_table_deleted_on_destroy_by_default(stacks):
    stacks["data"].has_resource("AWS::DynamoDB::GlobalTable", {"DeletionPolicy": "Delete"})


def test_table_kept_when_retain_requested():
    template = Template.from_stack(DataStack(cdk.App(), "Data", retain_data=True))
    template.has_resource("AWS::DynamoDB::GlobalTable", {"DeletionPolicy": "Retain"})


# ---- ecr ------------------------------------------------------------------------
def test_images_scanned_and_tags_immutable(stacks):
    stacks["ecr"].has_resource_properties(
        "AWS::ECR::Repository",
        {
            "ImageScanningConfiguration": {"ScanOnPush": True},
            "ImageTagMutability": "IMMUTABLE",
        },
    )


# ---- github oidc --------------------------------------------------------------
def test_deploy_role_trusts_only_main_and_tags_of_our_repo(stacks):
    stacks["oidc"].has_resource_properties(
        "AWS::IAM::Role",
        {
            "AssumeRolePolicyDocument": {
                "Statement": [
                    Match.object_like(
                        {
                            "Action": "sts:AssumeRoleWithWebIdentity",
                            "Condition": {
                                "StringEquals": {
                                    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                                },
                                "StringLike": {
                                    "token.actions.githubusercontent.com:sub": [
                                        f"repo:{REPO}:ref:refs/heads/main",
                                        f"repo:{REPO}:ref:refs/tags/v*",
                                    ]
                                },
                            },
                        }
                    )
                ]
            },
            "MaxSessionDuration": 3600,
        },
    )


def test_pull_requests_cannot_assume_deploy_role(stacks):
    trust = str(stacks["oidc"].find_resources("AWS::IAM::Role"))
    assert "pull_request" not in trust
    assert "repo:*" not in trust


def test_invalid_repo_rejected():
    app = cdk.App()
    ecr = EcrStack(app, "Ecr")
    with pytest.raises(ValueError):
        GithubOidcStack(app, "Oidc", github_repo="no-slash", repository=ecr.repository)


# ---- service ------------------------------------------------------------------
def test_service_uses_native_blue_green_with_rollback(stacks):
    stacks["service"].has_resource_properties(
        "AWS::ECS::Service",
        {
            "DesiredCount": 2,
            "LaunchType": "FARGATE",
            "DeploymentConfiguration": Match.object_like(
                {
                    "Strategy": "BLUE_GREEN",
                    "BakeTimeInMinutes": 3,
                    "DeploymentCircuitBreaker": {"Enable": True, "Rollback": True},
                    "MinimumHealthyPercent": 100,
                }
            ),
            "LoadBalancers": [
                Match.object_like(
                    {
                        "ContainerName": "api",
                        "ContainerPort": 8000,
                        "AdvancedConfiguration": Match.object_like(
                            {"AlternateTargetGroupArn": Match.any_value()}
                        ),
                    }
                )
            ],
        },
    )


def test_production_rule_sends_all_traffic_to_blue_initially(stacks):
    rules = stacks["service"].find_resources("AWS::ElasticLoadBalancingV2::ListenerRule")
    (rule,) = rules.values()
    groups = rule["Properties"]["Actions"][0]["ForwardConfig"]["TargetGroups"]
    assert sorted(g["Weight"] for g in groups) == [0, 100]


def test_target_groups_check_readiness(stacks):
    stacks["service"].resource_count_is("AWS::ElasticLoadBalancingV2::TargetGroup", 2)
    stacks["service"].has_resource_properties(
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        {"HealthCheckPath": "/ready", "TargetType": "ip", "Port": 8000},
    )


def test_tasks_only_reachable_from_load_balancer(stacks):
    # The only ingress rule on port 8000 comes from the ALB security group.
    stacks["service"].has_resource_properties(
        "AWS::EC2::SecurityGroupIngress",
        {"FromPort": 8000, "ToPort": 8000, "SourceSecurityGroupId": Match.any_value()},
    )
    for sg in stacks["service"].find_resources("AWS::EC2::SecurityGroup").values():
        for rule in sg["Properties"].get("SecurityGroupIngress", []):
            if rule.get("CidrIp") == "0.0.0.0/0":
                assert rule["FromPort"] == 80, "only the ALB port may be open to the internet"


def test_container_is_small_and_hardened(stacks):
    stacks["service"].has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {
            "Cpu": "256",
            "Memory": "512",
            "ContainerDefinitions": [
                Match.object_like(
                    {
                        "ReadonlyRootFilesystem": True,
                        "Environment": Match.array_with(
                            [{"Name": "APP_VERSION", "Value": "v1.0.0"}]
                        ),
                    }
                )
            ],
        },
    )


def test_logs_expire_after_a_week(stacks):
    stacks["service"].has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": 7})
