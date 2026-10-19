"""Amazon Bedrock model factories for Strands and LangChain.

Both use the Bedrock Converse API. No sampling parameters (temperature/top_p) are passed:
current Claude models reject them, and thinking depth is left to the model's adaptive default.
"""

from __future__ import annotations

from ..config import Settings, get_settings


def strands_model(settings: Settings | None = None):
    from strands.models import BedrockModel

    s = settings or get_settings()
    return BedrockModel(model_id=s.bedrock_model_id, region_name=s.aws_region, max_tokens=s.max_tokens)


def langchain_model(settings: Settings | None = None):
    from langchain_aws import ChatBedrockConverse

    s = settings or get_settings()
    return ChatBedrockConverse(model_id=s.bedrock_model_id, region_name=s.aws_region, max_tokens=s.max_tokens)
