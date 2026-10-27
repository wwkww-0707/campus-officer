"""教务业务 API 客户端。

工具层通过 HTTP 调用**自建的 FastAPI 后端**，而不是直接读数据库。
这样做的好处：
1. 业务接口是真实可运行的 HTTP 服务，答辩时可以直接用浏览器打开演示；
2. 工具层与数据层解耦，Agent 看到的是"业务能力"而非"数据库表"；
3. 后端不可用时能给出**可读的失败提示**，而不是抛出难懂的堆栈。
"""

from __future__ import annotations

from typing import Any

import httpx

from core.config import get_settings

# 默认超时：本地服务应当很快，超时说明后端没起来
_TIMEOUT = httpx.Timeout(8.0, connect=3.0)


class BackendUnavailable(RuntimeError):
    """后端服务不可达。"""


class CampusApiClient:
    """自建教务后端的 HTTP 客户端。"""

    def __init__(self, base_url: str | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.campus_api_base_url).rstrip("/")

    # -- 底层 ---------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """发起请求并统一处理异常。"""
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                response = client.request(method, url, **kwargs)
        except httpx.ConnectError as exc:
            raise BackendUnavailable(
                f"无法连接教务业务后端 {self.base_url}。"
                f"请先启动后端：python backend/main.py（或直接运行 start.bat）"
            ) from exc
        except httpx.TimeoutException as exc:
            raise BackendUnavailable(
                f"连接教务业务后端 {self.base_url} 超时，请检查后端是否正常运行。"
            ) from exc

        if response.status_code >= 400:
            detail = response.text
            try:
                payload = response.json()
                detail = payload.get("detail", detail)
            except Exception:
                pass
            raise RuntimeError(f"后端返回 {response.status_code}：{detail}")
        return response.json()

    def health(self) -> bool:
        """健康检查：后端是否在线。"""
        try:
            data = self._request("GET", "/api/health")
            return data.get("status") == "ok"
        except Exception:
            return False

    # -- 业务接口 -----------------------------------------------------------

    def list_courses(
        self,
        department: str | None = None,
        category: str | None = None,
        min_credits: float | None = None,
        max_credits: float | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """查询课程目录。"""
        params = {
            k: v
            for k, v in {
                "department": department,
                "category": category,
                "min_credits": min_credits,
                "max_credits": max_credits,
                "keyword": keyword,
            }.items()
            if v not in (None, "")
        }
        return self._request("GET", "/api/courses", params=params)

    def get_enrollments(self, student_id: str) -> dict[str, Any]:
        """查询已选课程。"""
        return self._request("GET", f"/api/enrollments/{student_id}")

    def enroll(self, student_id: str, course_id: int) -> dict[str, Any]:
        """提交选课。"""
        return self._request(
            "POST", "/api/enrollments", json={"student_id": student_id, "course_id": course_id}
        )

    def check_conflicts(
        self, student_id: str, candidate_course_ids: list[int]
    ) -> dict[str, Any]:
        """计算时间冲突。"""
        return self._request(
            "POST",
            "/api/schedule/conflicts",
            json={
                "student_id": student_id,
                "candidate_course_ids": candidate_course_ids,
            },
        )

    def get_eligibility(self, student_id: str) -> dict[str, Any]:
        """奖助学金资格初筛。"""
        return self._request("GET", f"/api/eligibility/{student_id}")

    def get_profile(self, student_id: str) -> dict[str, Any]:
        """读取指定学号的档案（传 "current" 表示当前学生）。"""
        return self._request("GET", f"/api/profile/{student_id}")

    def get_current_profile(self) -> dict[str, Any]:
        """读取**当前学生**档案。

        界面与工具都应使用本方法而非写死学号——用户可能把学号改成自己的，
        写死会导致"改了专业却读不到"。
        """
        return self._request("GET", "/api/profile/current")

    def save_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        """保存学生档案（覆盖写入，用于绑定学校 / 修改画像）。"""
        return self._request("POST", "/api/profile", json=profile)

    def summary(self) -> dict[str, Any]:
        """数据总览。"""
        return self._request("GET", "/api/summary")

    def list_announcements(self) -> dict[str, Any]:
        """列出全部公告（学校新政策 / 活动）。"""
        return self._request("GET", "/api/announcements")

    def add_announcement(
        self, title: str, category: str = "其他", content: str = "", source: str = "",
        event_date: str = "", url: str = "",
    ) -> dict[str, Any]:
        """录入一条公告。url 为原文链接，界面据此渲染跳转。"""
        return self._request(
            "POST",
            "/api/announcements",
            json={
                "title": title, "category": category, "content": content,
                "source": source, "event_date": event_date, "url": url,
            },
        )

    def delete_announcement(self, ann_id: int) -> dict[str, Any]:
        """删除一条公告。"""
        return self._request("DELETE", f"/api/announcements/{ann_id}")

    def sync_sandau(self) -> dict[str, Any]:
        """从杉达学院官网抓取并同步校园信息到公告库。"""
        return self._request("POST", "/api/sync/sandau")


_client: CampusApiClient | None = None


def get_client() -> CampusApiClient:
    """获取客户端单例。"""
    global _client
    if _client is None:
        _client = CampusApiClient()
    return _client
