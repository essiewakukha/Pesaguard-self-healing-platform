"""The running service: ECS Fargate tasks behind an Application Load Balancer.

Traffic path:  internet -> ALB (port 80) -> listener rule -> blue target group -> tasks

Deployments use ECS native blue/green: ECS starts the new version (green) in
the second target group, checks it is healthy, flips the listener rule to it,
keeps the old version (blue) around for the bake time, then removes blue.
If green fails, traffic never leaves blue. Phase 5 adds CloudWatch alarms and a
release-gate Lambda that can also trigger rollback during the bake time.
"""

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_logs as logs
from constructs import Construct

from .nag import acknowledge

CONTAINER_NAME = "api"
CONTAINER_PORT = 8000


class ServiceStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        table: dynamodb.ITableV2,
        repository: ecr.IRepository,
        image_tag: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---- security groups: internet -> ALB -> tasks, nothing else ---------
        alb_sg = ec2.SecurityGroup(
            self, "AlbSg", vpc=vpc, description="PesaGuard load balancer", allow_all_outbound=False
        )
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP from internet")

        task_sg = ec2.SecurityGroup(
            self,
            "TaskSg",
            vpc=vpc,
            description="PesaGuard tasks: only the ALB may connect",
            # Outbound is needed to pull the image from ECR, ship logs and
            # call DynamoDB.
            allow_all_outbound=True,
        )
        task_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(CONTAINER_PORT), "From ALB only")
        alb_sg.add_egress_rule(task_sg, ec2.Port.tcp(CONTAINER_PORT), "To tasks only")

        # ---- load balancer, listener and the two target groups ----------------
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            drop_invalid_header_fields=True,
        )

        def target_group(name: str) -> elbv2.ApplicationTargetGroup:
            return elbv2.ApplicationTargetGroup(
                self,
                name,
                vpc=vpc,
                port=CONTAINER_PORT,
                protocol=elbv2.ApplicationProtocol.HTTP,
                target_type=elbv2.TargetType.IP,
                # Readiness, not liveness: only send traffic to tasks that can
                # actually reach the database.
                health_check=elbv2.HealthCheck(
                    path="/ready",
                    healthy_http_codes="200",
                    interval=cdk.Duration.seconds(10),
                    timeout=cdk.Duration.seconds(5),
                    healthy_threshold_count=2,
                    unhealthy_threshold_count=3,
                ),
                deregistration_delay=cdk.Duration.seconds(15),
            )

        blue_tg = target_group("BlueTargetGroup")
        green_tg = target_group("GreenTargetGroup")

        listener = alb.add_listener(
            "Http",
            port=80,
            open=False,  # rules are in alb_sg above
            default_action=elbv2.ListenerAction.fixed_response(
                404, content_type="application/json", message_body='{"detail":"not found"}'
            ),
        )
        # ECS needs BOTH target groups in the production rule, with exactly one
        # receiving traffic. It flips the weights (100/0 -> 0/100) when it moves
        # traffic to green, and back again on rollback. CloudFormation leaves the
        # rule alone afterwards as long as this definition does not change.
        production_rule = elbv2.ApplicationListenerRule(
            self,
            "ProductionRule",
            listener=listener,
            priority=10,
            conditions=[elbv2.ListenerCondition.path_patterns(["/*"])],
            action=elbv2.ListenerAction.weighted_forward(
                [
                    elbv2.WeightedTargetGroup(target_group=blue_tg, weight=100),
                    elbv2.WeightedTargetGroup(target_group=green_tg, weight=0),
                ]
            ),
        )

        # ---- ECS cluster, task definition, container --------------------------
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc, cluster_name="pesaguard")

        task_def = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            cpu=256,  # smallest Fargate size: 0.25 vCPU
            memory_limit_mib=512,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        table.grant_read_write_data(task_def.task_role)

        log_group = logs.LogGroup(
            self,
            "Logs",
            log_group_name="/pesaguard/api",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        task_def.add_container(
            CONTAINER_NAME,
            image=ecs.ContainerImage.from_ecr_repository(repository, tag=image_tag),
            port_mappings=[ecs.PortMapping(container_port=CONTAINER_PORT)],
            environment={
                "APP_VERSION": image_tag,
                "DEPLOY_COLOR": "ecs",
                "TABLE_NAME": table.table_name,
                "AWS_REGION": self.region,
            },
            logging=ecs.LogDrivers.aws_logs(stream_prefix="api", log_group=log_group),
            readonly_root_filesystem=True,
            # Same liveness check as the Dockerfile, so ECS replaces a hung task.
            health_check=ecs.HealthCheck(
                command=[
                    "CMD",
                    "python",
                    "-c",
                    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen("
                    "'http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)",
                ],
                interval=cdk.Duration.seconds(10),
                timeout=cdk.Duration.seconds(3),
                retries=3,
                start_period=cdk.Duration.seconds(10),
            ),
        )

        # ---- the service, with ECS native blue/green ---------------------------
        service = ecs.FargateService(
            self,
            "Service",
            cluster=cluster,
            task_definition=task_def,
            service_name="pesaguard-api",
            desired_count=2,  # one task per zone
            assign_public_ip=True,  # needed without a NAT gateway; SG still blocks the internet
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[task_sg],
            min_healthy_percent=100,
            max_healthy_percent=200,
            health_check_grace_period=cdk.Duration.seconds(30),
            deployment_strategy=ecs.DeploymentStrategy.BLUE_GREEN,
            # How long blue stays alive after traffic moves to green, so a
            # rollback is instant. Phase 5 watches alarms during this window.
            bake_time=cdk.Duration.minutes(3),
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
        )

        target = service.load_balancer_target(
            container_name=CONTAINER_NAME,
            container_port=CONTAINER_PORT,
            alternate_target=ecs.AlternateTarget(
                "GreenTarget",
                alternate_target_group=green_tg,
                production_listener=ecs.ListenerRuleConfiguration.application_listener_rule(
                    production_rule
                ),
            ),
        )
        target.attach_to_application_target_group(blue_tg)

        # ---- outputs -------------------------------------------------------------
        cdk.CfnOutput(self, "ApiUrl", value=f"http://{alb.load_balancer_dns_name}")
        cdk.CfnOutput(self, "ImageTag", value=image_tag)
        cdk.CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        cdk.CfnOutput(self, "ServiceName", value=service.service_name)

        # ---- accepted findings, each with its reason ---------------------------
        acknowledge(
            self,
            {
                "AwsSolutions-ELB2": "ALB access logs need an S3 bucket and cost money; "
                "the app already writes one JSON log line per request to CloudWatch.",
                "AwsSolutions-EC23": "Public HTTP API: port 80 must be open to the internet. "
                "HTTPS needs a domain + ACM certificate (listed as a stretch goal).",
                "AwsSolutions-ECS4": "Container Insights costs extra; considered in Phase 4.",
                "AwsSolutions-ECS2": "The environment variables are plain configuration "
                "(version, table name, region). No secrets are passed this way.",
                "AwsSolutions-IAM5[Resource::*]": "Execution role: ecr:GetAuthorizationToken "
                "only supports resource '*'.",
                "AwsSolutions-IAM4[Policy::arn:<AWS::Partition>:iam::aws:policy/"
                "AmazonECSInfrastructureRolePolicyForLoadBalancers]": "AWS-managed policy "
                "that ECS blue/green needs to switch the load balancer between target groups.",
            },
        )
