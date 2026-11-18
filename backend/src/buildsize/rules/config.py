from typing import Annotated

from pydantic import Field

from buildsize.domain.contracts import ByteCount, Contract, RunId


class RuleSettings(Contract):
    absolute_budget_bytes: ByteCount | None = None
    growth_bytes: ByteCount = 5 * 1024**2
    growth_basis_points: Annotated[int, Field(ge=0, le=1000000)] = 300
    fixed_baseline_id: RunId | None = None
