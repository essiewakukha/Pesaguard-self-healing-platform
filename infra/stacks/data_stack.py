"""Data: the DynamoDB payments table used by the app.

The key layout (pk = "PAY#<id>" or "IDEM#<key>") is defined in
app/pesaguard/repository.py. Keep the two in sync.
"""

import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct

TABLE_NAME = "pesaguard-payments"


class DataStack(cdk.Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, retain_data: bool = False, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.table = dynamodb.TableV2(
            self,
            "PaymentsTable",
            table_name=TABLE_NAME,
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),  # pay per request, nothing when idle
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            deletion_protection=retain_data,
            # Learning project: destroy by default so `cdk destroy` leaves
            # nothing behind. Pass -c retainData=true to keep the data.
            removal_policy=cdk.RemovalPolicy.RETAIN if retain_data else cdk.RemovalPolicy.DESTROY,
        )

        cdk.CfnOutput(self, "TableName", value=self.table.table_name)
