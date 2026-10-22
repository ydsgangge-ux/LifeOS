"""系统端点：健康检查与退出（第 12.2 节）。

/health 不需解锁、不需令牌：前端用它确认核心进程已就绪并判断是否已解锁。
/system/shutdown 需要会话令牌——它是界面内的「退出 LifeOS」（P6 W3.1）。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from .deps import get_state, require_token
from .errors import CODE_UNSUPPORTED, ApiError
from .state import AppState

router = APIRouter(tags=["system"])

VERSION = "0.2.0"


@router.get("/health")
def health(state: AppState = Depends(get_state)) -> dict:
    active = state.active
    return {
        "status": "ok",
        "version": VERSION,
        "unlocked": active is not None,
        "profile_id": active.profile_id if active else None,
        "alias": active.alias if active else None,
        "idle_lock_minutes": state.config.idle_lock_minutes,
    }


@router.post("/system/shutdown")
def shutdown(
    background: BackgroundTasks,
    state: AppState = Depends(get_state),
    _: None = Depends(require_token),
) -> dict:
    """界面内的「退出 LifeOS」（P6 W3.1）：仅接受本机会话令牌。

    真正的停服放在后台任务里执行——先把 200 响应发出去，用户才不会看到
    一个「点了退出、页面却卡住」的状态。
    """
    hook = state.shutdown_hook
    if hook is None:
        raise ApiError(409, CODE_UNSUPPORTED, "当前运行方式不支持从界面退出")
    background.add_task(hook)
    return {"shutting_down": True}
