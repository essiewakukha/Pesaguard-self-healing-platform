"""Network: a VPC with public subnets only.

Why no private subnets and NAT gateway? A NAT gateway bills by the hour even
when idle, which is the most common surprise cost on learning accounts. Our
tasks sit in public subnets with public IPs, but their security group only
accepts traffic from the load balancer, so nothing on the internet can reach
them directly. See docs/adr/0003-no-nat-gateway.md for the trade-off.
"""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from constructs import Construct

from .nag import acknowledge


class NetworkStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.20.0.0/16"),
            max_azs=2,  # two zones so the service survives one zone failing
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                )
            ],
        )

        # Free gateway endpoint: DynamoDB traffic stays on the AWS network
        # instead of going out through the internet gateway.
        self.vpc.add_gateway_endpoint(
            "DynamoDbEndpoint", service=ec2.GatewayVpcEndpointAwsService.DYNAMODB
        )

        acknowledge(
            self.vpc,
            {
                "AwsSolutions-VPC7": "VPC flow logs cost money per GB; enable them "
                "for investigations or in a production account.",
            },
        )
