"""Settings read from environment variables.

Everything that changes between laptop, CI and AWS lives here, so the code
itself never needs editing to run somewhere else.
"""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    app_version: str = field(default_factory=lambda: os.getenv("APP_VERSION", "v0.0.0-dev"))
    # Which colour this copy of the app is running as (blue or green). Shown in
    # /health so you can see which side of a deployment answered a request.
    deploy_color: str = field(default_factory=lambda: os.getenv("DEPLOY_COLOR", "local"))
    fault_mode: str = field(default_factory=lambda: os.getenv("FAULT_MODE", "none"))

    table_name: str = field(default_factory=lambda: os.getenv("TABLE_NAME", "pesaguard-payments"))
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    # Set only for local development (DynamoDB Local). Leave empty on AWS.
    dynamodb_endpoint: str | None = field(
        default_factory=lambda: os.getenv("DYNAMODB_ENDPOINT") or None
    )
    # Local development only: create the table on startup if it is missing.
    # On AWS the table is created by CDK, never by the app.
    auto_create_table: bool = field(
        default_factory=lambda: os.getenv("AUTO_CREATE_TABLE", "false").lower() == "true"
    )

    max_amount: int = field(default_factory=lambda: int(os.getenv("MAX_AMOUNT", "150000")))


def get_settings() -> Settings:
    return Settings()
