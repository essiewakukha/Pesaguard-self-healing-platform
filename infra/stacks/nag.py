"""Small helper for recording cdk-nag findings we accept on purpose.

Every acknowledgment carries a written reason, so a reviewer can see each
security trade-off and why it was made.
"""

import aws_cdk as cdk
from constructs import IConstruct


def acknowledge(scope: IConstruct, rules: dict[str, str]) -> None:
    """Acknowledge cdk-nag rules on a construct and everything inside it.

    rules: {"AwsSolutions-XYZ1": "why this is acceptable here", ...}
    """
    cdk.Validations.of(scope).acknowledge(
        *(cdk.Acknowledgment(id=rule_id, reason=reason) for rule_id, reason in rules.items())
    )
