"""M2 client for the private D resource service. No proxies or redirects."""

import json
from http.client import HTTPConnection, HTTPSConnection
from http.client import HTTPException as HTTPProtocolError
from urllib.parse import quote, urlsplit

from fastapi import HTTPException

from campus_app import config


def acquire_classroom_hold(application_id: str, comment: str, credential: str) -> dict:
    service = urlsplit(config.RESOURCE_SERVICE_URL)
    if service.scheme not in {"http", "https"} or not service.hostname:
        raise HTTPException(503, "空间服务地址配置有误")
    connection_type = HTTPSConnection if service.scheme == "https" else HTTPConnection
    connection = connection_type(service.hostname, service.port, timeout=5)
    try:
        connection.request(
            "PUT",
            f"/internal/v1/classroom-holds/{quote(application_id, safe='')}",
            body=json.dumps({"comment": comment}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {credential}"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        if 400 <= response.status < 500 and isinstance(payload, dict):
            raise HTTPException(response.status, payload.get("detail", "终审被拒绝"))
        if (
            response.status == 200
            and isinstance(payload, dict)
            and payload.get("id") == application_id
            and payload.get("status") == "approved"
        ):
            return payload
    except (OSError, HTTPProtocolError, ValueError) as error:
        raise HTTPException(503, "未收到空间服务确认，请刷新申请状态或重试") from error
    finally:
        connection.close()
    raise HTTPException(503, "未收到空间服务确认，请刷新申请状态或重试")
