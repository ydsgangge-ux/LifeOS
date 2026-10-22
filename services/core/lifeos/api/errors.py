"""API 错误体与异常映射（第 12.1 节：错误体固定包含 code、message、details）。

前端按 code 判断行为（如 423 locked 跳回解锁页），不解析 message 文本。
"""

from __future__ import annotations

from typing import Any

import sqlcipher3
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..domain.errors import (
    DomainError,
    DraftOnlyError,
    InvalidInputError,
    NonEmptyRuleError,
    NotFoundError,
)
from ..privacy.errors import PolicyDeniedError, UnknownPurposeError
from ..security.errors import InvalidPassphraseError, SecurityError, VaultLockedError

# 稳定的错误码
CODE_UNAUTHORIZED = "unauthorized"
CODE_BAD_HOST = "bad_host"
CODE_FORBIDDEN = "forbidden"
CODE_FORBIDDEN_ORIGIN = "forbidden_origin"
CODE_LOCKED = "locked"
CODE_NOT_FOUND = "not_found"
CODE_INVALID_INPUT = "invalid_input"
CODE_NON_EMPTY_RULE = "non_empty_rule"
CODE_DRAFT_ONLY = "draft_only"
CODE_INVALID_PASSPHRASE = "invalid_passphrase"
CODE_PROFILE_ERROR = "profile_error"
CODE_RATE_LIMITED = "rate_limited"
CODE_VALIDATION_ERROR = "validation_error"
# 隐私网关（第 7.4 节）：策略越权与用途缺失要能和普通入参错误区分开
CODE_POLICY_DENIED = "policy_denied"
CODE_UNKNOWN_PURPOSE = "unknown_purpose"
# 运行方式不支持该操作（如进程内嵌运行时无法从界面退出，P6 W3.1）
CODE_UNSUPPORTED = "unsupported"


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": {} if details is None else details}


class ApiError(Exception):
    """路由层直接抛出的 API 错误。"""

    def __init__(
        self, status_code: int, code: str, message: str, details: Any = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = {} if details is None else details


def _json_safe_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """只保留可序列化的字段，避免 pydantic 上下文对象泄进响应体。"""
    out: list[dict[str, Any]] = []
    for err in exc.errors():
        out.append(
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
        )
    return out


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
        )

    # 这两个是 DomainError 的子类；Starlette 按 MRO 取最具体的一个，故排在下面也优先命中
    @app.exception_handler(PolicyDeniedError)
    async def _policy_denied(_: Request, exc: PolicyDeniedError) -> JSONResponse:
        return JSONResponse(status_code=403, content=error_body(CODE_POLICY_DENIED, str(exc)))

    @app.exception_handler(UnknownPurposeError)
    async def _unknown_purpose(_: Request, exc: UnknownPurposeError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content=error_body(CODE_UNKNOWN_PURPOSE, str(exc))
        )

    # DomainError 是 SecurityError 的子类，Starlette 按 MRO 查找，故会先命中这里
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        if isinstance(exc, NotFoundError):
            status, code = 404, CODE_NOT_FOUND
        elif isinstance(exc, NonEmptyRuleError):
            status, code = 400, CODE_NON_EMPTY_RULE
        elif isinstance(exc, DraftOnlyError):
            status, code = 400, CODE_DRAFT_ONLY
        elif isinstance(exc, InvalidInputError):
            status, code = 400, CODE_INVALID_INPUT
        else:
            status, code = 400, CODE_INVALID_INPUT
        return JSONResponse(status_code=status, content=error_body(code, str(exc)))

    @app.exception_handler(InvalidPassphraseError)
    async def _bad_passphrase(_: Request, exc: InvalidPassphraseError) -> JSONResponse:
        return JSONResponse(
            status_code=401, content=error_body(CODE_INVALID_PASSPHRASE, str(exc))
        )

    @app.exception_handler(VaultLockedError)
    async def _vault_locked(_: Request, exc: VaultLockedError) -> JSONResponse:
        return JSONResponse(status_code=423, content=error_body(CODE_LOCKED, str(exc)))

    @app.exception_handler(SecurityError)
    async def _security_error(_: Request, exc: SecurityError) -> JSONResponse:
        # 档案创建、改口令等业务性安全错误（含 ProfileError）
        return JSONResponse(
            status_code=400, content=error_body(CODE_PROFILE_ERROR, str(exc))
        )

    @app.exception_handler(sqlcipher3.IntegrityError)
    async def _integrity_error(_: Request, exc: sqlcipher3.IntegrityError) -> JSONResponse:
        # 多为 CHECK / 外键 / 触发器拒绝，属客户端输入问题（第 4.1 节枚举与约束）
        return JSONResponse(
            status_code=400,
            content=error_body(CODE_INVALID_INPUT, "数据完整性校验未通过", {"reason": str(exc)}),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                CODE_VALIDATION_ERROR, "请求参数不合法", {"errors": _json_safe_errors(exc)}
            ),
        )
