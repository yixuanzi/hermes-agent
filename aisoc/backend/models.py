"""AISOC backend API models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


UserStatus = Literal["enabled", "disabled"]


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    username: str
    display_name: str
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


class UserProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=40)


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


class HealthResponse(BaseModel):
    status: str
    pid: int


class SystemBootstrapResponse(BaseModel):
    auth_scheme: str
    admin_setup_required: bool


class SystemRestartResponse(BaseModel):
    accepted: bool
    already_requested: bool
    service: str
    pid: int


class CronJobCreate(BaseModel):
    name: str = ""
    prompt: str
    schedule: str
    deliver: str = "local"
    skills: list[str] = Field(default_factory=list)
    skill: str | None = None
    enabled_toolsets: list[str] | None = None
    model: str | None = None
    provider: str | None = None
    base_url: str | None = None
    script: str | None = None
    workdir: str | None = None
    no_agent: bool = False


class CronJobUpdate(BaseModel):
    updates: dict


class CronJobRawUpdate(BaseModel):
    job: dict


class SkillToggleRequest(BaseModel):
    name: str
    enabled: bool


class SkillHubInstallRequest(BaseModel):
    identifier: str


class MemoryWriteRequest(BaseModel):
    content: str


ChatQuickCommandType = Literal["agent", "prompt", "instruct"]


class ChatQuickCommandResponse(BaseModel):
    """One composer shortcut available in the chat module."""

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


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    auth: str = "none"
    bearer_token: str | None = None
    enabled: bool = True


class McpServerUpdate(BaseModel):
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    auth: str = "none"
    bearer_token: str | None = None
    enabled: bool = True


class McpServerEnabledUpdate(BaseModel):
    enabled: bool


class McpServerToolsUpdate(BaseModel):
    tools: list[str] | None = None
