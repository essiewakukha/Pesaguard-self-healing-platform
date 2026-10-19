"""Release safety: the alarms that can roll back a deployment, and the release
gate Lambda that tests green before any customer reaches it.

Two independent checks, catching two different kinds of bad release:

  1. Release gate (before production traffic). ECS calls a Lambda at
     POST_TEST_TRAFFIC_SHIFT. It sends real payments to green through the test
     listener. Any failure -> FAILED -> rollback. Customers never see it.

  2. Alarms (during the canary and bake). Each target group has an error-rate
     alarm and a p99-latency alarm. If either fires while ECS is deploying,
     ECS rolls back. This catches releases that work but are too slow, or that
     only fail under real traffic.

Both target groups get alarms because they swap roles on every release:
the "green" group of this deployment is the "blue" group of the next one.
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

from .nag import acknowledge

RELEASE_GATE_DIR = Path(__file__).resolve().parents[2] / "release_gate"

# Thresholds. Kept here so the SLO document (docs/SLO.md) can point at them.
ERROR_RATE_THRESHOLD_PCT = 5  # % of requests answered with 5xx in one minute
P99_LATENCY_THRESHOLD_S = 1.0  # seconds
MIN_REQUESTS_FOR_ERROR_RATE = 5  # ignore minutes with almost no traffic


class ReleaseAlarms(Construct):
    """Error-rate and p99-latency alarms for each target group."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        target_groups: dict[str, elbv2.ApplicationTargetGroup],
        alarm_email: str | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        self.topic = sns.Topic(self, "Topic", display_name="PesaGuard alarms", enforce_ssl=True)
        if alarm_email:
            self.topic.add_subscription(subs.EmailSubscription(alarm_email))
        acknowledge(
            self.topic,
            {"AwsSolutions-SNS2": "Alarm notifications contain no sensitive data."},
        )

        self.alarms: list[cw.Alarm] = []
        minute = cdk.Duration.minutes(1)

        for colour, tg in target_groups.items():
            errors = tg.metrics.http_code_target(
                elbv2.HttpCodeTarget.TARGET_5XX_COUNT, period=minute, statistic="Sum"
            )
            requests = tg.metrics.request_count(period=minute, statistic="Sum")
            error_rate = cw.MathExpression(
                expression=(
                    f"IF(requests >= {MIN_REQUESTS_FOR_ERROR_RATE}, "
                    "100 * FILL(errors, 0) / requests, 0)"
                ),
                using_metrics={"errors": errors, "requests": requests},
                label=f"{colour} target group 5xx rate (%)",
                period=minute,
            )
            self.alarms.append(
                cw.Alarm(
                    self,
                    f"{colour.title()}ErrorRate",
                    alarm_name=f"pesaguard-{colour}-error-rate",
                    alarm_description=(
                        f"More than {ERROR_RATE_THRESHOLD_PCT}% of requests to the {colour} "
                        "target group failed in one minute. Rolls back an active deployment."
                    ),
                    metric=error_rate,
                    threshold=ERROR_RATE_THRESHOLD_PCT,
                    comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                    evaluation_periods=1,
                    datapoints_to_alarm=1,
                    treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                )
            )

            p99 = tg.metrics.target_response_time(period=minute, statistic="p99")
            self.alarms.append(
                cw.Alarm(
                    self,
                    f"{colour.title()}LatencyP99",
                    alarm_name=f"pesaguard-{colour}-latency-p99",
                    alarm_description=(
                        f"p99 latency of the {colour} target group above "
                        f"{P99_LATENCY_THRESHOLD_S}s for one minute. "
                        "Rolls back an active deployment."
                    ),
                    metric=p99,
                    threshold=P99_LATENCY_THRESHOLD_S,
                    comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                    evaluation_periods=1,
                    datapoints_to_alarm=1,
                    treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                )
            )

        for alarm in self.alarms:
            alarm.add_alarm_action(cw_actions.SnsAction(self.topic))
            alarm.add_ok_action(cw_actions.SnsAction(self.topic))

    @property
    def alarm_names(self) -> list[str]:
        return [a.alarm_name for a in self.alarms]


class ReleaseGate(Construct):
    """The Lambda that ECS calls before production traffic moves to green."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        test_url: str,
        test_header: str,
        test_token: str,
    ) -> None:
        super().__init__(scope, construct_id)

        log_group = logs.LogGroup(
            self,
            "Logs",
            log_group_name="/pesaguard/release-gate",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        self.function = lambda_.Function(
            self,
            "Function",
            description="PesaGuard release gate: test green before production traffic",
            runtime=lambda_.Runtime.PYTHON_3_14,
            handler="handler.handler",
            code=lambda_.Code.from_asset(
                str(RELEASE_GATE_DIR),
                exclude=["tests", "tests/*", "__pycache__", "*.pyc", ".pytest_cache", "*.toml"],
            ),
            timeout=cdk.Duration.minutes(2),
            memory_size=256,
            log_group=log_group,
            environment={
                "TEST_URL": test_url,
                "TEST_HEADER": test_header,
                "TEST_TOKEN": test_token,
                "GATE_CHECKS": "20",
            },
        )

        acknowledge(
            self,
            {
                "AwsSolutions-IAM4[Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/"
                "AWSLambdaBasicExecutionRole]": "Standard AWS-managed policy that only "
                "allows writing the function's own logs.",
            },
        )
