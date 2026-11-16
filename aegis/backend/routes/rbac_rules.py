"""Administrator APIs for the Aegis RBAC role rules file."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from aegis.backend.auth import require_admin_user, require_authenticated_user
from aegis.backend.config import AegisSettings
from aegis.backend.models import (
    RbacRegexTestRequest,
    RbacRegexTestResponse,
    RbacRule,
    RbacRuleRole,
    RbacRuleUpdateResponse,
    RbacRulesResponse,
)
from aegis.backend.services.rbac_rule_service import (
    RBAC_ROLES,
    RbacRuleConfigError,
    RbacRuleService,
)
from aegis.backend.services.user_service import UserService


def build_rbac_rules_router(
    settings: AegisSettings,
    user_service: UserService,
    rbac_rule_service: RbacRuleService,
) -> APIRouter:
    router = APIRouter(prefix="/api/rbac-rules", tags=["rbac-rules"])

    def _ensure_admin(request: Request) -> None:
        user, _payload = require_authenticated_user(request, settings, user_service)
        require_admin_user(user)

    def _config_error(exc: RbacRuleConfigError) -> HTTPException:
        return HTTPException(status_code=422, detail=str(exc))

    @router.get("", response_model=RbacRulesResponse)
    async def list_rbac_rules(request: Request) -> RbacRulesResponse:
        _ensure_admin(request)
        try:
            rules = rbac_rule_service.list_rules()
        except RbacRuleConfigError as exc:
            raise _config_error(exc) from exc
        return RbacRulesResponse(rules=rules)

    @router.put("/{role}", response_model=RbacRuleUpdateResponse)
    async def update_rbac_rule(
        role: str,
        body: RbacRule,
        request: Request,
    ) -> RbacRuleUpdateResponse:
        _ensure_admin(request)
        if role not in RBAC_ROLES:
            raise HTTPException(status_code=422, detail="Unsupported RBAC role.")
        try:
            saved = rbac_rule_service.update_rule(role, body)
        except RbacRuleConfigError as exc:
            raise _config_error(exc) from exc
        return RbacRuleUpdateResponse(
            role=role,
            rule=saved,
            restart_required=True,
        )

    @router.post("/test", response_model=RbacRegexTestResponse)
    async def test_rbac_pattern(
        body: RbacRegexTestRequest,
        request: Request,
    ) -> RbacRegexTestResponse:
        _ensure_admin(request)
        try:
            matched = rbac_rule_service.test_pattern(body.pattern, body.text)
        except RbacRuleConfigError as exc:
            raise _config_error(exc) from exc
        return RbacRegexTestResponse(matched=matched)

    return router
