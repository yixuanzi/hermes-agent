"""Aegis backend API models."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, TypeAdapter, field_validator


UserStatus = Literal["enabled", "disabled"]


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    username: str
    email: str
    status: UserStatus
    create_time: str
    last_login: str | None = None
    is_admin: bool


class AuthLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class AuthLoginResponse(BaseModel):
    authenticated: bool
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class AuthRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    email: str = Field(min_length=5, max_length=320)


class AuthRegisterResponse(BaseModel):
    registered: bool
    status: UserStatus


class AuthSessionResponse(BaseModel):
    authenticated: bool
    user: UserResponse | None = None
    expires_in: int | None = None


class AuthLogoutResponse(BaseModel):
    logged_out: bool


class AuthPasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_password: str = Field(min_length=8, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class AuthPasswordChangeResponse(BaseModel):
    updated: bool


class UserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    email: str = Field(min_length=5, max_length=320)
    status: UserStatus


class UserListResponse(BaseModel):
    users: list[UserResponse]


class UserStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: UserStatus


class UserPasswordUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=8, max_length=256)


class UserPasswordUpdateResponse(BaseModel):
    updated: bool
    uid: str


class UserDeleteResponse(BaseModel):
    deleted: bool
    uid: str


class PromptTemplateRequest(BaseModel):
    """Payload used to create or update one user's reusable prompt."""

    model_config = ConfigDict(extra="forbid")

    tag: str = Field(min_length=1, max_length=64)
    desc: str = Field(default="", max_length=512)
    prompt: str = Field(min_length=1, max_length=20_000)

    @field_validator("tag", "prompt", mode="before")
    @classmethod
    def require_nonblank_value(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Value must not be blank.")
        return normalized

    @field_validator("desc", mode="before")
    @classmethod
    def normalize_description(cls, value: str | None) -> str:
        return str(value or "").strip()


class PromptTemplateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tag: str
    desc: str
    prompt: str
    create_time: str
    update_time: str


class PromptTemplateListResponse(BaseModel):
    templates: list[PromptTemplateResponse]


class PromptTemplateDeleteResponse(BaseModel):
    deleted: bool
    id: str


SystemInstructStatus = Literal["enabled", "disabled"]


class SystemInstructRequest(BaseModel):
    """Administrator-managed system instruction payload."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    describe: str = Field(default="", max_length=512)
    instruct: str = Field(min_length=1, max_length=50_000)
    status: SystemInstructStatus = "enabled"

    @field_validator("name", "instruct", mode="before")
    @classmethod
    def require_nonblank_value(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Value must not be blank.")
        return normalized

    @field_validator("describe", mode="before")
    @classmethod
    def normalize_description(cls, value: str | None) -> str:
        return str(value or "").strip()


class SystemInstructResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    describe: str
    instruct: str
    create_time: str
    update_time: str
    status: SystemInstructStatus


class SystemInstructListResponse(BaseModel):
    instructions: list[SystemInstructResponse] = Field(default_factory=list)


class SystemInstructDeleteResponse(BaseModel):
    deleted: bool
    id: str


ChatQuickCommandType = Literal["agent", "prompt", "instruct"]


class ChatQuickCommandResponse(BaseModel):
    """One composer shortcut available to the authenticated chat user."""

    model_config = ConfigDict(extra="forbid")

    type: ChatQuickCommandType
    name: str
    desc: str
    content: str


class ChatQuickCommandListResponse(BaseModel):
    commands: list[ChatQuickCommandResponse] = Field(default_factory=list)


class DrawerFileResponse(BaseModel):
    """One workspace file prepared for a chat drawer preview."""

    model_config = ConfigDict(extra="forbid")

    title: str
    type: str
    content: str


class UserManualSummary(BaseModel):
    """One published, read-only Markdown manual."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str


class UserManualListResponse(BaseModel):
    manuals: list[UserManualSummary] = Field(default_factory=list)
    default_manual_id: str | None = None


AppEntrySubscriptionStatus = Literal["active", "expiring"]
AppEntrySource = Literal["app", "subscription"]


class AppEntryResponse(BaseModel):
    """A safe, display-ready application entry returned to the Aegis UI."""

    model_config = ConfigDict(extra="forbid")

    subscription_id: str
    subscription_no: str
    subscription_status: AppEntrySubscriptionStatus
    effective_to: str
    product_service_code: str
    product_service_name: str
    entry_url: str | None = None
    entry_source: AppEntrySource | None = None


class AppEntryListResponse(BaseModel):
    organization_code: str
    entries: list[AppEntryResponse] = Field(default_factory=list)


class UserManualResponse(UserManualSummary):
    content: str


class A2AContextAgentResponse(BaseModel):
    """One active A2A registry entry rendered in the chat agent browser."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str | None = None
    status: str | None = None
    available: bool = False
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    error: str | None = None


class A2AGlobalRoutingRuleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    policy: str
    status: str


class A2AContextResponse(BaseModel):
    """Cached, structured form of the existing ``/a2a`` registry context."""

    model_config = ConfigDict(extra="forbid")

    agents: list[A2AContextAgentResponse] = Field(default_factory=list)
    global_routing: list[A2AGlobalRoutingRuleResponse] = Field(default_factory=list)
    refreshed_at: str | None = None
    stale: bool = False
    refresh_error: str | None = None


class HealthResponse(BaseModel):
    status: str
    pid: int


class SystemBootstrapResponse(BaseModel):
    embedded_chat: bool
    auth_scheme: str
    admin_setup_required: bool
    lark_sso_enabled: bool


class SystemRestartResponse(BaseModel):
    accepted: bool
    already_requested: bool
    service: str
    pid: int


AgentStatus = Literal["active", "idle", "offline"]
GlobalRoutingStatus = Literal["active", "inactive"]
AgentPolicyStatus = Literal["allow", "deny"]
DelegateAuditStatus = Literal["succ", "fail", "auth_denied"]
_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def normalize_agent_url(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Agent URL must be a string.")

    candidate = value.strip()
    if not candidate:
        raise ValueError("Agent URL must not be empty.")

    if not _URL_SCHEME_RE.match(candidate):
        if "/" not in candidate:
            raise ValueError("Agent URL must include a path when no scheme is provided.")
        candidate = f"http://{candidate}"

    return str(_HTTP_URL_ADAPTER.validate_python(candidate))


class AgentUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    description: str
    headers: dict[str, str]
    status: AgentStatus
    extcapabilities: list[str]

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return normalize_agent_url(value)


class AgentResponse(BaseModel):
    agent_id: str
    url: str
    description: str
    headers: dict[str, str]
    status: AgentStatus
    extcapabilities: list[str]

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return normalize_agent_url(value)


class AgentListResponse(BaseModel):
    agents: list[AgentResponse]


class OverviewAgentResponse(BaseModel):
    agent_id: str
    url: str
    description: str
    status: AgentStatus
    extcapabilities: list[str]

    @field_validator("url", mode="before")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return normalize_agent_url(value)


class OverviewAgentListResponse(BaseModel):
    agents: list[OverviewAgentResponse]


class OverviewStatusCountsResponse(BaseModel):
    succ: int = Field(ge=0)
    fail: int = Field(ge=0)
    auth_denied: int = Field(ge=0)


class OverviewStatsComparisonResponse(BaseModel):
    previous_delegation_total: int = Field(ge=0)
    delegation_volume_change_percent: float | None = None
    previous_success_rate: float | None = Field(default=None, ge=0, le=1)
    success_rate_change_percentage_points: float | None = None


class OverviewStatsResponse(BaseModel):
    window_start: str
    window_end: str
    executing_agent_count: int = Field(ge=0)
    source_platform_count: int = Field(ge=0)
    active_user_count: int = Field(ge=0)
    delegation_total: int = Field(ge=0)
    success_count: int = Field(ge=0)
    success_rate: float | None = Field(default=None, ge=0, le=1)
    status_counts: OverviewStatusCountsResponse
    comparison: OverviewStatsComparisonResponse


TopologyRuntimeStatus = Literal["active", "idle", "offline", "planned"]
TopologyStarKind = Literal["tool", "api", "data"]


class TopologyCenterLayoutResponse(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    radius: str


class TopologyAgentLayoutResponse(BaseModel):
    ring_position: int = Field(ge=0)
    angle_degrees: float = Field(ge=0, lt=360)
    radius: str


class TopologyRuntimeResponse(BaseModel):
    status: TopologyRuntimeStatus
    source: str


class TopologyCenterResponse(BaseModel):
    id: str
    layer: Literal["center"]
    name: str
    symbol: str
    role: str
    description: str
    capabilities: list[str]
    layout: TopologyCenterLayoutResponse


class TopologyStarNodeResponse(BaseModel):
    id: str
    layer: Literal["star_field"]
    kind: TopologyStarKind
    name: str
    integration: str
    purpose: str
    required: bool


class TopologyAgentResponse(BaseModel):
    id: str
    layer: Literal["agent_ring"]
    business_domain: str
    product_service_code: str
    name: str
    display_name: str
    marketing_name: str
    symbol: str
    cultural_origin: str
    business_fit: str
    role: str
    layout: TopologyAgentLayoutResponse
    star_nodes: list[TopologyStarNodeResponse] = Field(min_length=10, max_length=20)
    runtime: TopologyRuntimeResponse


class TopologyEdgeResponse(BaseModel):
    source: str
    target: str
    mode: Literal["orchestrates", "requires"]


class TopologyStarmappingResponse(BaseModel):
    schema_version: str
    updated_at: str
    center: TopologyCenterResponse
    agents: list[TopologyAgentResponse] = Field(min_length=7, max_length=7)
    edges: list[TopologyEdgeResponse]


class AgentDeleteResponse(BaseModel):
    deleted: bool
    agent_id: str


class GlobalRoutingRuleUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    policy: str
    status: GlobalRoutingStatus


class GlobalRoutingRuleResponse(BaseModel):
    id: str = Field(min_length=8, max_length=8)
    name: str
    policy: str
    status: GlobalRoutingStatus


class GlobalRoutingRuleListResponse(BaseModel):
    rules: list[GlobalRoutingRuleResponse]


class GlobalRoutingRuleDeleteResponse(BaseModel):
    deleted: bool
    id: str


class AgentPolicyUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank_id: int = Field(gt=0)
    platform: str = Field(default="*", max_length=128)
    user_id: str = Field(default="*", max_length=256)
    agent_name: str = Field(default="*", max_length=256)
    status: AgentPolicyStatus


class AgentPolicyResponse(BaseModel):
    rank_id: int = Field(gt=0)
    platform: str
    user_id: str
    agent_name: str
    status: AgentPolicyStatus


class AgentPolicyListResponse(BaseModel):
    policies: list[AgentPolicyResponse]


class AgentPolicyDeleteResponse(BaseModel):
    deleted: bool
    rank_id: int


class DelegateAuditResponse(BaseModel):
    id: str
    timestamp: str
    platform: str
    user_id: str
    user_name: str
    agent_name: str
    goal: str
    session_id: str
    is_loop: bool
    is_delegate_output: bool
    status: DelegateAuditStatus


class DelegateAuditListResponse(BaseModel):
    logs: list[DelegateAuditResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
