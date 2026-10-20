"""Narrow, auditable cross-block prose groups proved by source structure."""
from typing import Literal
from pydantic import BaseModel, Field
from booktrans.domain.ir import SourceLocator

class CompositeFragmentMember(BaseModel):
    unit_uid: str
    display_id: str
    locator: SourceLocator
    source_text: str
    role: Literal["primary", "secondary"]
    container_kind: str

class CompositeFragmentGroup(BaseModel):
    group_id: str
    classification: Literal["PROVEN_COMPOSITE_PROSE", "POSSIBLE_COMPOSITE_PROSE", "NOT_COMPOSITE", "UNKNOWN"]
    primary_unit_uid: str | None = None
    members: list[CompositeFragmentMember] = Field(default_factory=list)
    source_combined_text: str = ""
    confidence_reason: str

