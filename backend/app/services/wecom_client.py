import httpx
from typing import Any


class WeComClient:
    """企业微信API客户端"""

    BASE_URL = "https://qyapi.weixin.qq.com/cgi-bin"

    def __init__(self, access_token: str):
        self.access_token = access_token

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        json_data: dict | None = None
    ) -> dict[str, Any]:
        """发送HTTP请求"""
        url = f"{self.BASE_URL}{endpoint}"

        # 添加access_token到参数
        if params is None:
            params = {}
        params["access_token"] = self.access_token

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
            )
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError as e:
                body_preview = response.text[:500]
                raise Exception(f"企业微信API返回非JSON响应: {body_preview}") from e

        # 检查企业微信API错误
        if result.get("errcode", 0) != 0:
            raise Exception(f"企业微信API错误: {result.get('errmsg', 'Unknown error')}")

        return result

    async def get_user_info(self, code: str) -> dict[str, Any]:
        """
        通过code获取用户身份
        https://developer.work.weixin.qq.com/document/path/91023
        """
        result = await self._request(
            method="GET",
            endpoint="/auth/getuserinfo",
            params={"code": code}
        )
        return {
            "user_id": result.get("userid") or result.get("UserId"),
            "device_id": result.get("DeviceId"),
            "external_userid": result.get("external_userid")
        }

    async def get_user_detail(self, user_id: str) -> dict[str, Any]:
        """
        获取用户详细信息
        https://developer.work.weixin.qq.com/document/path/90196
        """
        result = await self._request(
            method="GET",
            endpoint="/user/get",
            params={"userid": user_id}
        )
        return {
            "user_id": result.get("userid"),
            "name": result.get("name"),
            "department": result.get("department"),
            "position": result.get("position"),
            "mobile": result.get("mobile"),
            "email": result.get("email"),
            "avatar": result.get("avatar")
        }

    async def send_message(
        self,
        chat_id: str,
        content: str,
        msg_type: str = "text"
    ) -> dict[str, Any]:
        """
        发送应用消息
        https://developer.work.weixin.qq.com/document/path/90236
        """
        data = {
            "chatid": chat_id,
            "msgtype": msg_type,
        }

        if msg_type == "text":
            data["text"] = {"content": content}
        elif msg_type == "markdown":
            data["markdown"] = {"content": content}

        result = await self._request(
            method="POST",
            endpoint="/appchat/send",
            json_data=data
        )
        return {"success": True, "msg": "消息发送成功", "msgid": result.get("msgid")}

    async def get_group_chat(self, chat_id: str) -> dict[str, Any]:
        """
        获取群聊详情

        优先尝试"客户群"接口（externalcontact/groupchat/get），失败时降级为"应用群聊"接口（appchat/get）。
        """
        try:
            result = await self._request(
                method="POST",
                endpoint="/externalcontact/groupchat/get",
                json_data={"chat_id": chat_id, "need_name": 1},
            )

            group_chat = result.get("group_chat", {})
            return {
                "chat_id": group_chat.get("chat_id"),
                "name": group_chat.get("name"),
                "owner": group_chat.get("owner"),
                "create_time": group_chat.get("create_time"),
                "member_count": len(group_chat.get("member_list", [])),
                "members": group_chat.get("member_list", []),
                "chat_type": "externalcontact_groupchat",
            }
        except Exception as external_err:
            try:
                result = await self._request(
                    method="GET",
                    endpoint="/appchat/get",
                    params={"chatid": chat_id},
                )
                chat_info = result.get("chat_info", {})
                user_list = chat_info.get("userlist", []) or []
                return {
                    "chat_id": chat_info.get("chatid") or chat_id,
                    "name": chat_info.get("name"),
                    "owner": chat_info.get("owner"),
                    "create_time": None,
                    "member_count": len(user_list),
                    "members": user_list,
                    "chat_type": "appchat",
                }
            except Exception:
                raise external_err

    async def get_external_group_list(
        self,
        owner_userid_list: list[str] | None = None,
        limit: int = 100
    ) -> dict[str, Any]:
        """
        获取客户群列表
        https://developer.work.weixin.qq.com/document/path/92120

        Args:
            owner_userid_list: 群主userid列表，用于筛选
            limit: 每次返回的最大记录数

        Returns:
            包含群列表的字典
        """
        data: dict[str, Any] = {
            "status_filter": 0,
            "limit": limit
        }
        if owner_userid_list:
            data["owner_filter"] = {"userid_list": owner_userid_list}

        result = await self._request(
            method="POST",
            endpoint="/externalcontact/groupchat/list",
            json_data=data
        )
        return {
            "group_chat_list": result.get("group_chat_list", []),
            "next_cursor": result.get("next_cursor")
        }

    async def close(self):
        """关闭HTTP客户端"""
        return
