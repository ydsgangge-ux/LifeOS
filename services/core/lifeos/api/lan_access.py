"""手机访问端点：查看状态、临时开启、立即关闭。

开启后界面会拿到一个带会话令牌的地址，用二维码给手机扫。令牌仍然只放在 URL
fragment 里；关闭、锁定或 30 分钟到期都会回收端口。

三个端点都要求**已解锁**（而不只是有令牌）：锁定状态本来就不该把端口开到
局域网上，否则同一网络里的人能直接看到解锁页并试着猜口令。锁定后
``AppState.service()`` 也会顺手把端口收回。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .deps import get_state, require_service
from .errors import CODE_UNSUPPORTED, ApiError
from .lan import LanAccess
from .state import AppState

router = APIRouter(tags=["lan"], dependencies=[Depends(require_service)])


def _lan(state: AppState) -> LanAccess:
    if state.lan is None:
        raise ApiError(409, CODE_UNSUPPORTED, "当前运行方式不支持手机访问")
    return state.lan


@router.get("/system/lan")
def lan_status(state: AppState = Depends(get_state)) -> dict:
    return _lan(state).status()


@router.post("/system/lan")
def start_lan(state: AppState = Depends(get_state)) -> dict:
    """临时开一个局域网端口；已开着时直接返回当前地址。"""
    return _lan(state).start()


@router.delete("/system/lan")
def stop_lan(state: AppState = Depends(get_state)) -> dict:
    _lan(state).stop()
    return {"active": False}
