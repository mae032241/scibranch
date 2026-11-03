"""Minimal loopback HTTP server for direct human decisions."""

from __future__ import annotations

import html
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
from contextlib import ExitStack, nullcontext
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from typing import TYPE_CHECKING

from pydantic import ValidationError
from ..service.executions import ExecutionApprovalError, ExecutionBudgetNotApplicable
from ..schema.approval import LocalIdentityRef
from ..service.approvals import (
    ApprovalAccessDenied,
    BudgetExtensionAccountingError,
    ApprovalError,
    ApprovalExpired,
    ApprovalService,
)
from ..service.scheduler_bindings import (
    SchedulerBindingError,
    SchedulerBindingService,
    SchedulerInstanceNotFound,
    SchedulerInstanceConflict,
)
from ..service.instance_management import (
    InstanceManagementCapabilityError,
    issue_instance_management_capability,
    verify_instance_management_capability,
)
from ..service.maintenance import StateMaintenanceLock
from ..service.instance_maintenance import InstanceMaintenanceBusy, InstanceMaintenanceUnavailable
from ..storage import CASIntegrityError
from .render import ReviewContext, render_review
from .access import BrowserAccessDenied, BrowserAccessService, access_cookie, management_cookie, management_token
from .navigation import navigation
from .home_render import session_controls, instance_directory, confirm_binding, access_page, client_directory, create_form, page as management_page

if TYPE_CHECKING:
    from .read_model import InstanceReadModel


MAX_POST_BYTES = 64 * 1024
SSE_SECONDS = 25
SSE_POLL_SECONDS = 1
STATIC_ROOT = Path(__file__).with_name("static")
LOGGER = logging.getLogger(__name__)
_RETIRED_INSTANCE_APPROVAL_KINDS = (
    "instance_creation",
    "research_instance_registration",
    "session_binding",
    "research_session_binding",
)


class ApprovalUI:
    def __init__(
        self,
        service: ApprovalService,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        local_identity: LocalIdentityRef | None = None,
        bindings: SchedulerBindingService | None = None,
        instance_management_secret: bytes | None = None,
        maintenance: StateMaintenanceLock | None = None,
        read_model: InstanceReadModel | None = None,
        trajectory_store=None,
        instance_archive=None,
    ) -> None:
        try:
            address = ipaddress.ip_address(host)
        except ValueError as error:
            raise ValueError("approval UI host must be a literal loopback address") from error
        if not address.is_loopback:
            raise ValueError("approval UI may only bind a loopback address")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("approval UI port is invalid")
        self.service = service
        self.host = host
        self.port = port
        self.identity = local_identity or LocalIdentityRef(
            identity_id="local_user",
            display_name="Local user",
        )
        self.bindings = bindings
        self.instance_management_secret = instance_management_secret
        self.maintenance = maintenance
        self.session_id = f"ui_{secrets.token_hex(16)}"
        self.read_model = read_model
        self.trajectory_store = trajectory_store
        self.instance_archive = instance_archive
        self.trajectory = None
        if read_model is not None and trajectory_store is not None:
            from .trajectory import TrajectoryObserver
            self.trajectory = TrajectoryObserver(read_model, trajectory_store, maintenance=maintenance,
                writable=self._cache_writable)
        self.management = None
        if instance_archive is not None:
            from .management import WorkbenchManagement
            self.management = WorkbenchManagement(instance_archive, trajectory_store, observer=self.trajectory)
        self.browser_access = (BrowserAccessService(secret=instance_management_secret,
            ui_session_id=self.session_id) if instance_management_secret is not None else None)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stream_slots = threading.BoundedSemaphore(8)
        self._stream_stop = threading.Event()

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("approval UI is not running")
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self._server.server_port}"

    def start(self) -> str:
        if self._server is not None:
            return self.base_url
        ui = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parts = [part for part in urlparse(self.path).path.split("/") if part]
                try:
                    if len(parts) == 4 and parts[:2] == ["api", "instances"] and parts[3] == "events":
                        ui._handle_events(self, parts[2])
                        return
                    with ExitStack() as resources:
                        self._response_resources = resources
                        self._defer_reply = True
                        self._pending_reply = None
                        with ui._shared_maintenance():
                            ui._handle_get(self)
                        # HTTP writes and slow clients never retain a control lock.
                        self._defer_reply = False
                        if self._pending_reply is not None:
                            self._pending_reply()
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                except Exception:
                    LOGGER.exception("workbench HTTP read failed")
                    if getattr(self, "_defer_reply", False):
                        self._defer_reply = False
                        ui._error(self, HTTPStatus.SERVICE_UNAVAILABLE)

            def do_POST(self) -> None:
                try:
                    ui._handle_post(self)
                except (InstanceMaintenanceBusy, InstanceMaintenanceUnavailable) as error:
                    ui._error(self, HTTPStatus.CONFLICT, str(error))
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                except Exception:
                    LOGGER.exception("workbench HTTP write failed")
                    ui._error(self, HTTPStatus.SERVICE_UNAVAILABLE)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        server_type = ThreadingHTTPServer
        if ":" in self.host:
            class IPv6HTTPServer(ThreadingHTTPServer):
                address_family = socket.AF_INET6

            server_type = IPv6HTTPServer
        self._server = server_type((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="artifact-approval-ui",
            daemon=True,
        )
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        self._stream_stop.set()
        if self.trajectory is not None:
            self.trajectory.stop()
        if self.management is not None:
            self.management.close()
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        if not self._valid_host(handler):
            return self._error(handler, HTTPStatus.FORBIDDEN)
        parsed = urlparse(handler.path)
        if parsed.path == "/static/style.css":
            return self._static(handler, "style.css", "text/css; charset=utf-8")
        if parsed.path == "/static/app.js":
            return self._static(handler, "app.js", "text/javascript; charset=utf-8")
        if parsed.path == "/static/agent-settings.js":
            return self._static(handler, "agent-settings.js", "text/javascript; charset=utf-8")
        if parsed.path == "/static/workbench.js":
            return self._static(handler, "workbench.js", "text/javascript; charset=utf-8")
        if parsed.path == "/static/home.js":
            return self._static(handler, "home.js", "text/javascript; charset=utf-8")
        parts = [part for part in parsed.path.split("/") if part]
        try:
            query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True, max_num_fields=16)
        except ValueError:
            return self._error(handler, HTTPStatus.BAD_REQUEST)
        token = _one(query, "token")
        try:
            if parsed.path in {"/", "/instances", "/instances/manage"}:
                return self._handle_home(handler, query, directory=parsed.path == "/instances/manage",
                    session_key="" if parsed.path == "/instances" and "capability" in query else None)
            if parsed.path == "/sessions":
                return self._handle_clients(handler, query)
            if len(parts) == 2 and parts[0] == "sessions":
                return self._handle_home(handler, query, session_key=parts[1])
            if len(parts) == 3 and parts[0] == "instance" and parts[2] == "settings":
                return self._handle_agent_settings_get(handler, parts[1], query)
            if len(parts) == 3 and parts[0] == "instance" and parts[2] == "manage":
                return self._handle_management_get(handler, parts[1], query)
            if len(parts) >= 3 and parts[:2] == ["api", "instances"]:
                return self._handle_instance_api(handler, parts, query)
            if len(parts) == 4 and parts[0] in {"review", "instance"} and parts[2] == "evidence":
                return self._handle_evidence(handler, parts, query)
            if ((len(parts) == 4 and parts[0] == "instance" and parts[2] in {"nodes", "diagnostics"})
                    or (len(parts) == 3 and parts[0] == "instance" and parts[2] == "results")):
                return self._handle_workbench(handler, parts, query)
            if len(parts) == 2 and parts[0] == "instance":
                if self.bindings is None:
                    return self._error(handler, HTTPStatus.NOT_FOUND)
                instance = self.bindings.get_instance(instance_id=parts[1])
                if self.read_model is not None and self.browser_access is not None:
                    try:
                        self._browser_grant(handler, parts[1])
                    except BrowserAccessDenied:
                        if "text/html" in handler.headers.get("Accept", ""):
                            return self._respond(handler, HTTPStatus.OK,
                                access_page(instance, csrf=self.session_id), "text/html; charset=utf-8")
                        pass  # Preserve the old metadata page; no new payload access.
                    else:
                        return self._handle_workbench(handler, parts, query)
                approval_ids = self.bindings.instance_history_approval_ids(
                    instance=instance.instance_id
                )
                history = []
                for approval_id in approval_ids:
                    try:
                        history.append(self.service.request_summary(approval_id))
                    except ApprovalError:
                        continue
                history.sort(key=lambda item: item.created_at, reverse=True)
                return self._respond(
                    handler,
                    HTTPStatus.OK,
                    _render_instance_page(instance, tuple(history)),
                    "text/html; charset=utf-8",
                )
            if len(parts) == 2 and parts[0] == "review" and token:
                review = self._approval_service(parts[1]).review(parts[1], access_token=token)
                return self._respond(
                    handler,
                    HTTPStatus.OK,
                    self._render_review_page(review, token),
                    "text/html; charset=utf-8",
                )
            if len(parts) == 2 and parts[0] == "history" and token:
                review = self._approval_service(parts[1]).review(parts[1], access_token=token)
                if review.status == "pending":
                    return self._error(handler, HTTPStatus.NOT_FOUND)
                return self._respond(
                    handler,
                    HTTPStatus.OK,
                    self._render_review_page(review, token),
                    "text/html; charset=utf-8",
                )
            if len(parts) == 2 and parts[0] == "request" and token:
                service = self._approval_service(parts[1])
                review = service.review(parts[1], access_token=token)
                return self._send_original(handler, review.request_ref, artifacts=service.artifacts)
            if len(parts) == 3 and parts[0] == "subject" and token:
                review = self._approval_service(parts[1]).review(parts[1], access_token=token)
                index = int(parts[2])
                if not 0 <= index < len(review.subjects):
                    raise IndexError("approval subject index is out of range")
                _, content = review.subjects[index]
                return self._respond(
                    handler,
                    HTTPStatus.OK,
                    content,
                    "application/octet-stream",
                    extra_headers={
                        "Content-Disposition": f"attachment; filename=subject-{index}.bin"
                    },
                )
        except BrowserAccessDenied:
            if (len(parts) >= 2 and parts[0] == "instance" and self.bindings is not None
                    and "text/html" in handler.headers.get("Accept", "")):
                try:
                    instance = self.bindings.get_instance(instance_id=parts[1])
                except SchedulerInstanceNotFound:
                    return self._error(handler, HTTPStatus.NOT_FOUND)
                destination = parts[2] if len(parts) == 3 and parts[2] in {"settings", "manage"} else ""
                return self._respond(handler, HTTPStatus.FORBIDDEN,
                    access_page(instance, csrf=self.session_id, destination=destination), "text/html; charset=utf-8")
            return self._error(handler, HTTPStatus.FORBIDDEN)
        except (
            ApprovalAccessDenied,
            PermissionError,
            InstanceManagementCapabilityError,
            ValueError,
            IndexError,
        ):
            return self._error(handler, HTTPStatus.FORBIDDEN)
        except SchedulerInstanceNotFound:
            return self._error(handler, HTTPStatus.NOT_FOUND)
        except ApprovalExpired:
            return self._error(handler, HTTPStatus.GONE)
        except (ApprovalError, CASIntegrityError):
            return self._error(handler, HTTPStatus.CONFLICT)
        self._error(handler, HTTPStatus.NOT_FOUND)

    def _handle_events(self, handler, instance_id):
        if not self._valid_host(handler):
            return self._error(handler, HTTPStatus.FORBIDDEN)
        if self.trajectory is None or self.browser_access is None:
            return self._error(handler, HTTPStatus.SERVICE_UNAVAILABLE)
        try:
            query = parse_qs(urlparse(handler.path).query, strict_parsing=True, max_num_fields=4)
            after = handler.headers.get("Last-Event-ID") or _one(query, "after")
            if after is not None and len(after) > 100:
                raise ValueError("invalid event cursor")
            with self._shared_maintenance():
                self._browser_grant(handler, instance_id)
                if not self._cache_writable(instance_id):
                    return self._error(handler, HTTPStatus.CONFLICT, "实例维护或已归档，请读取静态历史页面。")
                self.bindings.get_instance(instance_id=instance_id)
                initial = self.trajectory_store.events(instance_id, after=after)
        except PermissionError:
            return self._error(handler, HTTPStatus.FORBIDDEN)
        except ValueError:
            return self._error(handler, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            LOGGER.warning("UI event read unavailable: %s", type(error).__name__)
            return self._error(handler, HTTPStatus.SERVICE_UNAVAILABLE)
        if not self._stream_slots.acquire(blocking=False):
            return self._respond(handler, HTTPStatus.TOO_MANY_REQUESTS, b"event connection limit reached", "text/plain; charset=utf-8",
                                 extra_headers={"Retry-After": "10"})
        try:
            handler.connection.settimeout(3)
            handler.send_response(HTTPStatus.OK)
            for key, value in {"Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store",
                               "X-Content-Type-Options": "nosniff", "Connection": "close",
                               "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"}.items():
                handler.send_header(key, value)
            handler.end_headers()
            with self.trajectory.subscribe(instance_id):
                deadline = time.monotonic() + SSE_SECONDS
                value = initial
                while not self._stream_stop.is_set():
                    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    handler.wfile.write(("event: nodes\nid: " + value["cursor"] + "\ndata: " + raw + "\n\n").encode())
                    handler.wfile.flush()
                    if self._stream_stop.wait(min(SSE_POLL_SECONDS, max(0, deadline - time.monotonic()))) or time.monotonic() >= deadline:
                        break
                    # Release both SQLite and maintenance locks before socket writes/wait.
                    with self._shared_maintenance():
                        if not self._cache_writable(instance_id):
                            break
                        value = self.trajectory_store.events(instance_id, after=value["cursor"])
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception as error:
            LOGGER.warning("UI event stream failed: %s", type(error).__name__)
            try:
                handler.wfile.write(("event: view_error\ndata: " + json.dumps({"error": "observation_unavailable", "error_type": type(error).__name__}) + "\n\n").encode())
            except OSError:
                pass
        finally:
            self._stream_slots.release()
            handler.close_connection = True

    def _handle_home(self, handler, query, *, directory=False, session_key=None):
        token = _one(query, "capability")
        capability = None
        if "capability" in query:
            capability = self._instance_capability(token)
        else:
            token = management_token(handler.headers.get("Cookie"))
            if token:
                try:
                    capability = self._instance_capability(token)
                except InstanceManagementCapabilityError:
                    token = None
        if session_key is not None:
            if capability is None or (session_key and capability.session_key != session_key):
                return self._error(handler, HTTPStatus.FORBIDDEN, "请从科研会话列表重新进入对应会话。")
        instances = self.bindings.list_instances() if self.bindings and (directory or session_key is not None) else ()
        offset = int(_one(query, "offset") or "0")
        if offset < 0:
            raise ValueError("invalid instance page")
        visible = instances[offset:offset+30] if directory else instances
        rows = self._home_rows(handler, visible, capability, read_status=directory)
        if directory:
            body = instance_directory(rows, token=token, csrf=self.session_id, offset=offset, total=len(instances),
                workbench_available=self.read_model is not None, maintenance_available=self.management is not None,
                local_access=self.browser_access is not None)
        elif session_key is not None:
            current = None
            if capability and self.bindings:
                current_id = self.bindings.session_instance(session_key=capability.session_key)
                current = self.bindings.get_instance(instance_id=current_id) if current_id else None
            controls = session_controls(rows, token=token, csrf=self.session_id, current=current,
                expires_at=capability.expires_at, workbench_available=self.read_model is not None)
            body = management_page("科研会话 · " + capability.session_key[-8:],
                "<p><a href='/sessions'>返回会话列表</a></p>" + controls)
        else:
            controls = ("<section id='create-instance'>" + create_form(csrf=self.session_id) + "</section>"
                if self.browser_access is not None else "")
            body = _render_dashboard(self.service.list_requests(status="pending", limit=100,
                excluded_kinds=_RETIRED_INSTANCE_APPROVAL_KINDS), context_for=self._review_context,
                csrf_token=self.session_id, home_controls=controls)
        headers = {}
        if capability and "capability" in query:
            expiry = datetime.fromisoformat(capability.expires_at.replace("Z", "+00:00"))
            headers["Set-Cookie"] = management_cookie(token, max_age=int((expiry-datetime.now(timezone.utc)).total_seconds()))
        return self._respond(handler, HTTPStatus.OK, body, "text/html; charset=utf-8", extra_headers=headers)

    def _handle_clients(self, handler, query):
        if self.bindings is None or self.instance_management_secret is None:
            return self._error(handler, HTTPStatus.NOT_FOUND)
        offset = int(_one(query, "offset") or "0")
        if offset < 0:
            raise ValueError("invalid client page")
        rows = self.bindings.clients(offset=offset)
        links = {row["session_key"]: "/sessions/" + row["session_key"] + "?" + urlencode({"capability":
            issue_instance_management_capability(session_key=row["session_key"], secret=self.instance_management_secret)}) for row in rows}
        cleared = _one(query, "cleared")
        cleared = max(0, int(cleared)) if cleared is not None else None
        return self._respond(handler, HTTPStatus.OK, client_directory(rows, csrf=self.session_id, links=links, offset=offset, cleared=cleared),
                             "text/html; charset=utf-8")

    def _home_rows(self, handler, instances, capability, *, read_status):
        from .presentation_render import human_value
        rows = []
        for instance in instances:
            binding = self.bindings.session_binding_snapshot(
                session_key=capability.session_key if capability else self.session_id, instance_id=instance.instance_id)
            label = ("当前会话已绑定" if binding["target_is_current"] else
                ("已被其他会话绑定" if capability else "已有会话绑定") if binding["target_bound"] else "未绑定")
            can_read = can_manage = False
            try:
                grant = self._browser_grant(handler, instance.instance_id)
                can_read, can_manage = True, "maintenance" in grant.scopes
            except BrowserAccessDenied:
                pass
            storage = self._storage_status(instance.instance_id).get("storage_state", "unknown")
            status = "详细进展需进入实例查看"
            if read_status and (capability or can_read) and self.read_model is not None:
                try:
                    model = self._model(instance.instance_id)
                    runs = model.runs
                    active = runs.active_ids(instance_id=instance.instance_id, limit=31)
                    external = model.executions.active_ids(instance_id=instance.instance_id,
                        scheduler_database_path=model.bindings.database_path, limit=31)
                    recent = runs.recent_ids(instance_id=instance.instance_id, limit=1)
                    status = "已记录的排队 / 运行任务：" + ("至少31" if len(active) == 31 else str(len(active)))
                    status += "；未结束的外部执行：" + ("至少31" if len(external) == 31 else str(len(external)))
                    if recent:
                        last = runs.status(recent[0])
                        status += "；最近任务：" + human_value(last.state)
                    else:
                        status += "；尚无任务记录"
                except Exception:
                    LOGGER.exception("instance directory status unavailable")
                    status = "任务状态暂未读到，请进入实例核对"
            storage_label = {"active":"活动资料", "restored":"已恢复", "archived":"已归档", "archiving":"归档中",
                "restoring":"恢复中", "unknown":"存储状态未确定"}.get(storage, "维护中")
            rows.append({"instance":instance, "binding":binding, "binding_label":label,
                "storage_state":storage, "storage_label":storage_label, "status":status,
                "can_read":can_read, "can_manage":can_manage})
        return rows

    def _binding_activity(self, instance_ids):
        messages = []
        for instance_id in dict.fromkeys(value for value in instance_ids if value):
            instance = self.bindings.get_instance(instance_id=instance_id)
            try:
                model = self._model(instance_id)
                active = model.runs.active_ids(instance_id=instance_id, limit=31)
                external = model.executions.active_ids(instance_id=instance_id,
                    scheduler_database_path=model.bindings.database_path, limit=31)
                messages.append(instance.title + "：排队 / 运行任务 " + ("至少31" if len(active) == 31 else str(len(active)))
                    + "，未结束的外部执行 " + ("至少31" if len(external) == 31 else str(len(external))))
            except Exception:
                LOGGER.exception("binding confirmation task status unavailable")
                messages.append(instance.title + "：任务状态未读取，不能据此认为空闲")
        return "；".join(messages) or "暂无既有实例；尚无任务记录"

    def _workbench_presentation(self, instance_id, key):
        from .presentation import build_presentation
        try:
            context = self._model(instance_id).node_context(instance_id, key)
            result = build_presentation(tuple(context["artifacts"]),
                focus_artifact_ids=context.get("focus_artifact_ids", []),
                task_artifact_ids=context.get("task_artifact_ids", []))
            result["gaps"].extend(context["gaps"])
            model = self._model(instance_id)
            if model.presentation_summaries is not None:
                copies = {}
                for artifact in context["artifacts"]:
                    ref = model.artifacts.get_by_id(artifact["artifact_id"]).ref
                    copy = model.presentation_summaries.read(instance_id, ref)
                    if copy and copy["state"] == "ready":
                        copies[artifact["artifact_id"]] = {item["source"]: item for item in copy["items"]}
                for row in result.get("parameters", []):
                    source = row.get("field_sources", {}).get("description", {})
                    row["display_copy"] = copies.get(source.get("artifact_id"), {}).get(source.get("json_pointer"))
            return result
        except Exception as error:
            LOGGER.warning("node presentation unavailable: %s", type(error).__name__)
            return {"gaps": [{"code": "node_presentation_unavailable", "error_type": type(error).__name__}]}

    def _workbench_objectives(self, model, instance_id, view):
        records = []
        for reference in view.get("objective_refs", [])[:2]:
            try:
                records.append(model.artifact(instance_id, reference["artifact_id"], pointer="/statement"))
            except Exception as error:
                LOGGER.warning("objective display unavailable: %s", type(error).__name__)
        view["objective_records"] = records

    def _cache_observations(self, instance_id, nodes, *, node_key=None):
        if self.trajectory_store is None:
            return {"coverage": "cache_unavailable", "items": []}
        try:
            if not self._cache_writable(instance_id):
                store = self._view_store(instance_id)
                return (store.observations(instance_id, node_key=node_key) if store is not None
                        else {"coverage": "archive_observations_unavailable", "items": []})
            self.trajectory_store.observe(instance_id, nodes)
            return self.trajectory_store.observations(instance_id, node_key=node_key)
        except Exception as error:
            LOGGER.warning("UI cache unavailable: %s", type(error).__name__)
            return {"coverage": "cache_unavailable", "error_type": type(error).__name__, "items": []}

    def _handle_workbench(self, handler, parts, query):
        if self.read_model is None or self.browser_access is None:
            return self._error(handler, HTTPStatus.NOT_FOUND)
        from .workbench_render import render_workbench, render_node, render_diagnostic, render_scientific_result
        instance_id = parts[1]
        access = self._browser_grant(handler, instance_id)
        model = self._model(instance_id)
        summary_unavailable_reason = self._summary_unavailable_reason(instance_id, access)
        can_retry_summary = summary_unavailable_reason is None
        if len(parts) == 3 and parts[2] == "results":
            view = model.scientific_result(instance_id)
            view["storage"] = self._storage_status(instance_id)
            body = render_scientific_result(view, csrf_token=self.session_id)
        elif len(parts) == 2:
            try:
                page = int(_one(query, "page") or "1")
                if page < 1:
                    raise ValueError("page must be positive")
            except ValueError:
                return self._error(handler, HTTPStatus.BAD_REQUEST, "轨迹页码必须是正整数。")
            view = model.overview(instance_id)
            view["trajectory"] = model.trajectory(instance_id, page=page)
            view["storage"] = self._storage_status(instance_id)
            store = self._view_store(instance_id)
            try:
                view["preferences"] = store.preferences(instance_id) if store is not None else {}
            except Exception:
                LOGGER.exception("UI preferences unavailable")
                view["preferences"] = {}
            cursor = _one(query, "cursor")
            if cursor:
                view["nodes"] = model.nodes(instance_id, cursor=cursor)
            selected_key = _one(query, "node")
            view["navigation_query"] = {key: value for key, value in {"node": selected_key, "cursor": cursor}.items() if value}
            display = (model.node_metadata(instance_id, selected_key) if selected_key
                       else view.get("selected_node") or view.get("recent_node"))
            if display:
                if not selected_key:
                    display = model.node_metadata(instance_id, display["key"])
                display["is_current_scientific_result"] = model.is_current_scientific_result_node(instance_id, display["key"])
                view["display_node"] = {**display, "sealed_stages": model.stage_deliveries(instance_id, display["key"])}
                view["presentation"] = self._workbench_presentation(instance_id, display["key"])
            self._workbench_objectives(model, instance_id, view)
            view["observations"] = self._cache_observations(instance_id, view["nodes"]["items"])
            body = render_workbench(view, browse_base=f"/instance/{quote(instance_id, safe='')}",
                csrf_token=self.session_id, can_manage="maintenance" in access.scopes,
                can_retry_summary=can_retry_summary, summary_unavailable_reason=summary_unavailable_reason)
        elif parts[2] == "nodes":
            key = unquote(parts[3])
            view = model.node(instance_id, key,
                diagnostic_after=int(_one(query, "diagnostic_after") or "0"),
                diagnostic_limit=int(_one(query, "diagnostic_limit") or "50"),
                stage_offset=int(_one(query, "stage_offset") or "0"),
                stage_limit=int(_one(query, "stage_limit") or "4"),
                stage_reference=_one(query, "stage_reference"),
                stage_text_offset=int(_one(query, "stage_text_offset") or "0"),
                child_offset=int(_one(query, "child_offset") or "0"))
            view["storage"] = self._storage_status(instance_id)
            self._workbench_objectives(model, instance_id, view)
            view["observations"] = self._cache_observations(instance_id,
                [model.node_metadata(instance_id, key)], node_key=key)
            if view.get("kind") == "approval":
                binding = model.bindings.get_binding(instance=instance_id, namespace="approval", name=key.split(":", 1)[1])
                view["review_url"] = model.approvals.status(binding.object_id).review_path
                from .approval_focus import execution_budget
                view["approval_authorization"] = execution_budget(model, binding.object_id)
            view["is_current_scientific_result"] = model.is_current_scientific_result_node(instance_id, key)
            body = render_node(view, instance_id=instance_id,
                csrf_token=self.session_id, can_retry_summary=can_retry_summary,
                summary_unavailable_reason=summary_unavailable_reason,
                presentation=None if view.get("stage_reference") else self._workbench_presentation(instance_id, key))
        else:
            key, reference = _one(query, "node_key"), unquote(parts[3])
            value = model.diagnostics(instance_id, reference, node_key=key,
                offset=int(_one(query, "offset") or "0"), section=_one(query, "section") or "summary")
            body = render_diagnostic(value, instance_id=instance_id, node_key=key, reference=reference)
        return self._respond(handler, HTTPStatus.OK, body, "text/html; charset=utf-8")

    def _render_review_page(self, review, token):
        # Archived approvals are already read-only and their request may exist
        # only in the archive, not in the active Artifact registry.
        read_only = (review.request.kind in _RETIRED_INSTANCE_APPROVAL_KINDS
            or self._approval_frozen(review.request.approval_id))
        presentation, evidence_href, image_href = None, None, None
        authorization = None
        if self.read_model is not None and self.bindings is not None:
            try:
                owner = self._approval_owner(review.request.approval_id)
                if owner is not None:
                    from .approval_focus import execution_budget
                    authorization = execution_budget(self._model(owner[0].instance_id), review.request.approval_id)
                    from .presentation import build_presentation
                    context = self._model(owner[0].instance_id).approval_context(owner[0].instance_id, review, presentation=True)
                    presentation = build_presentation(tuple(context["artifacts"]),
                        focus_artifact_ids=[ref.artifact_id for ref in review.request.subject_refs])
                    presentation["gaps"].extend(context["gaps"])
                    base = "/review/" + quote(review.request.approval_id, safe="") + "/evidence/"
                    evidence_href = lambda artifact_id, pointer: base + quote(artifact_id, safe="") + "?" + urlencode({"token": token, "pointer": pointer})
                    image_href = lambda artifact_id: base + quote(artifact_id, safe="") + "?" + urlencode({"token": token, "format": "image"})
            except Exception as error:
                # Presentation cannot change an immutable request or its decision path.
                LOGGER.warning("approval presentation unavailable: %s", type(error).__name__)
                presentation = {"gaps": [{"code": "approval_presentation_unavailable", "error_type": type(error).__name__}]}
        return render_review(review, access_token=token, identity=self.identity,
            context=self._review_context(review.request.approval_id),
            read_only=read_only,
            presentation=presentation, evidence_href=evidence_href, image_href=image_href, authorization=authorization)

    def _handle_evidence(self, handler, parts, query):
        if self.read_model is None:
            return self._error(handler, HTTPStatus.NOT_FOUND)
        from .evidence import MAX_IMAGE_BYTES, preview_type, render_evidence
        artifact_id = unquote(parts[3])
        review = None
        if parts[0] == "review":
            token = _one(query, "token")
            if not token or self.bindings is None:
                return self._error(handler, HTTPStatus.FORBIDDEN)
            review = self._approval_service(parts[1]).review(parts[1], access_token=token)
            owner = self._approval_owner(parts[1])
            if owner is None:
                return self._error(handler, HTTPStatus.NOT_FOUND)
            instance_id = owner[0].instance_id
        else:
            instance_id = parts[1]
            if self.browser_access is None:
                return self._error(handler, HTTPStatus.FORBIDDEN)
            self._browser_grant(handler, instance_id)
        model = self._model(instance_id)
        try:
            ref = (model.approval_artifact_reference(instance_id, review, artifact_id)
                   if review else model.artifact_reference(instance_id, artifact_id))
            mode = _one(query, "format")
            if mode == "download":
                return self._send_original(handler, ref, artifacts=model.artifacts)
            if mode == "image":
                envelope = model.artifacts.catalog(ref)
                if envelope.size_bytes > MAX_IMAGE_BYTES or envelope.content_encoding != "identity":
                    return self._error(handler, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                raw = model.artifacts.read(ref)
                media = preview_type(raw, envelope.media_type)
                if media is None:
                    return self._error(handler, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                return self._respond(handler, HTTPStatus.OK, raw, media)
            if mode is not None:
                return self._error(handler, HTTPStatus.BAD_REQUEST)
            if _one(query, "view") == "parameters":
                from .presentation import build_parameter_page
                from .evidence import render_parameter_page
                context = model.parameter_context(instance_id, artifact_id, review=review)
                after = int(_one(query, "after") or "0")
                if after < 0:
                    return self._error(handler, HTTPStatus.BAD_REQUEST)
                original = context["artifact"]
                page = (build_parameter_page(original, context["dependencies"], after=after)
                        if original else {"parameters": [], "parameter_page": {"total": None}, "gaps": []})
                page["gaps"].extend(context["gaps"])
                base = "/".join(parts[:3])
                base = "/" + base + "/"
                def evidence_href(identity, pointer):
                    values = {"pointer": pointer}
                    if review:
                        values["token"] = token
                    return base + quote(identity, safe="") + "?" + urlencode(values)
                parent = "/review/" + quote(parts[1], safe="") + "?" + urlencode({"token": token}) if review else None
                return self._respond(handler, HTTPStatus.OK, render_parameter_page(page, href=handler.path,
                    instance_id=instance_id, evidence_href=evidence_href, parent_href=parent), "text/html; charset=utf-8")
            arguments = {"pointer": _one(query, "pointer"),
                "child_after": int(_one(query, "child_after") or "0"),
                "child_limit": int(_one(query, "child_limit") or "30")}
            view = (model.approval_artifact(instance_id, review, artifact_id, **arguments)
                    if review else model.artifact(instance_id, artifact_id, **arguments))
            parent = "/review/" + quote(parts[1], safe="") + "?" + urlencode({"token": token}) if review else None
            return self._respond(handler, HTTPStatus.OK, render_evidence(view, href=handler.path,
                instance_id=instance_id, parent_href=parent), "text/html; charset=utf-8")
        except PermissionError:
            return self._error(handler, HTTPStatus.FORBIDDEN)

    def _send_original(self, handler, ref, *, artifacts=None):
        artifacts = artifacts or self.service.artifacts
        envelope = artifacts.catalog(ref)
        if getattr(handler, "_defer_reply", False):
            source = handler._response_resources.enter_context(artifacts.open_original(ref))
            handler._pending_reply = lambda: self._write_original(handler, envelope, source)
            return
        with artifacts.open_original(ref) as source:
            self._write_original(handler, envelope, source)

    def _write_original(self, handler, envelope, source):
        handler.send_response(HTTPStatus.OK)
        for key, value in {
                "Content-Type": "application/octet-stream", "Content-Length": str(envelope.size_bytes),
                "Content-Disposition": "attachment; filename=" + ("curve.csv" if envelope.media_type.split(";", 1)[0] == "text/csv" else "original.bin"), "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin",
        }.items():
            handler.send_header(key, value)
        handler.end_headers()
        handler.connection.settimeout(10)
        while block := source.read(1024 * 1024):
            handler.wfile.write(block)

    def _review_context(self, approval_id: str) -> ReviewContext | None:
        if self.bindings is None:
            return None
        try:
            owner = self._approval_owner(approval_id)
        except SchedulerBindingError:
            return None
        if owner is None:
            return None
        instance, binding = owner
        return ReviewContext(
            instance_name=instance.name,
            instance_title=instance.title,
            instance_objective=instance.objective,
            approval_name=binding.name,
            approval_logical_name=binding.logical_name,
            approval_revision=binding.revision,
            instance_id=instance.instance_id,
        )

    def _handle_instance_api(self, handler, parts, query) -> None:
        if self.read_model is None or self.browser_access is None:
            return self._json_response(handler, HTTPStatus.NOT_FOUND, {"error": "workbench_unavailable"})
        from .read_model import ReadModelNotFound
        instance_id = parts[2]
        try:
            if len(parts) == 4 and parts[3] == "maintenance":
                return self._handle_management_get(handler, instance_id, query, as_json=True)
            self._browser_grant(handler, instance_id)
            model = self._model(instance_id)
            if len(parts) == 4 and parts[3] == "overview":
                result = model.overview(instance_id)
                result["storage"] = self._storage_status(instance_id)
            elif len(parts) == 4 and parts[3] == "nodes":
                result = model.nodes(instance_id, cursor=_one(query, "cursor"),
                    limit=int(_one(query, "limit") or "30"))
            elif len(parts) == 5 and parts[3] == "nodes":
                if _one(query, "view") == "metadata":
                    result = model.node_metadata(instance_id, unquote(parts[4]), stage_reference=_one(query, "stage_reference"))
                else:
                    result = model.node(instance_id, unquote(parts[4]),
                        diagnostic_after=int(_one(query, "diagnostic_after") or "0"),
                        diagnostic_limit=int(_one(query, "diagnostic_limit") or "50"),
                        stage_offset=int(_one(query, "stage_offset") or "0"),
                        stage_limit=int(_one(query, "stage_limit") or "4"),
                        stage_reference=_one(query, "stage_reference"),
                        stage_text_offset=int(_one(query, "stage_text_offset") or "0"),
                        child_offset=int(_one(query, "child_offset") or "0"))
            elif len(parts) == 5 and parts[3] == "evidence":
                result = model.artifact(instance_id, unquote(parts[4]),
                    pointer=_one(query, "pointer"),
                    child_after=int(_one(query, "child_after") or "0"),
                    child_limit=int(_one(query, "child_limit") or "30"))
            elif len(parts) == 5 and parts[3] == "diagnostics":
                result = model.diagnostics(instance_id, unquote(parts[4]),
                    node_key=_one(query, "node_key"), offset=int(_one(query, "offset") or "0"),
                    max_bytes=int(_one(query, "max_bytes") or "16384"),
                    section=_one(query, "section") or "summary")
            elif len(parts) == 4 and parts[3] == "observations" and self.trajectory_store is not None:
                before = _one(query, "before")
                store = self._view_store(instance_id)
                result = store.observations(instance_id,
                    node_key=_one(query, "node_key"), before=int(before) if before else None,
                    limit=int(_one(query, "limit") or "30"))
            else:
                return self._json_response(handler, HTTPStatus.NOT_FOUND, {"error": "unknown_view"})
        except PermissionError as error:
            return self._json_response(handler, HTTPStatus.FORBIDDEN,
                {"error": "access_denied", "message": str(error)})
        except (ReadModelNotFound, SchedulerInstanceNotFound) as error:
            return self._json_response(handler, HTTPStatus.NOT_FOUND,
                {"error": "record_unavailable", "message": str(error)})
        except ValueError as error:
            return self._json_response(handler, HTTPStatus.BAD_REQUEST,
                {"error": "invalid_query", "message": str(error)})
        return self._json_response(handler, HTTPStatus.OK, result)

    def _json_response(self, handler, status, value) -> None:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
            separators=(",", ":")).encode("utf-8")
        self._respond(handler, status, raw, "application/json; charset=utf-8")

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        if not self._valid_host(handler):
            return self._error(handler, HTTPStatus.FORBIDDEN)
        parsed = urlparse(handler.path)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 4 and parts[0] == "instance" and parts[2] == "display-summary" and parts[3] in {"retry", "generate"}:
            return self._handle_summary_retry(handler, parts[1], generate=parts[3] == "generate")
        if len(parts) == 4 and parts[0] == "instance" and parts[2] == "manage":
            return self._handle_management_post(handler, parts[1], parts[3])
        if len(parts) == 2 and parts[0] == "instances" and parts[1] in {
            "create",
            "create-unbound",
            "client-state",
            "clients-clear",
            "select",
            "access",
        }:
            return self._handle_instance_post(handler, action=parts[1])
        if len(parts) == 3 and parts[0] == "review" and parts[2] == "refresh-access":
            return self._handle_access_refresh(handler, approval_id=parts[1])
        if len(parts) != 3 or parts[0] != "review" or parts[2] != "decision":
            return self._error(handler, HTTPStatus.NOT_FOUND)
        with self._shared_maintenance():
            origin = handler.headers.get("Origin")
            if not self._valid_origin(origin):
                return self._error(handler, HTTPStatus.FORBIDDEN)
            if handler.headers.get("Content-Type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
                return self._error(handler, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            try:
                length = int(handler.headers.get("Content-Length", "-1"))
            except ValueError:
                return self._error(handler, HTTPStatus.BAD_REQUEST)
            if not 0 <= length <= MAX_POST_BYTES:
                return self._error(handler, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            try:
                form = parse_qs(
                    handler.rfile.read(length).decode("utf-8", errors="strict"),
                    strict_parsing=True,
                    keep_blank_values=True,
                )
                token = _required(form, "token")
                csrf = _required(form, "csrf")
                nonce = _required(form, "nonce")
                selected = _required(form, "selected_option")
                rationale = _required(form, "rationale", allow_blank=True)
                from decimal import Decimal, DecimalException
                try:
                    hours = Decimal(_one(form, 'additional_wall_time_hours') or '0')
                    seconds = hours * 3600
                    if not seconds.is_finite() or seconds < 0 or seconds > 2**53-1 or seconds != seconds.to_integral_value():
                        raise ValueError('追加时长必须是非负的整秒数')
                    additional_seconds = int(seconds)
                except DecimalException as error:
                    raise ValueError('追加时长无效') from error
                if _required(form, "confirm") != "confirm":
                    raise ValueError("confirmation mismatch")
            except (UnicodeDecodeError, ValueError):
                return self._error(handler, HTTPStatus.BAD_REQUEST, '审批表单格式或字段无效。')
            try:
                if self._approval_frozen(parts[1]):
                    return self._error(handler, HTTPStatus.CONFLICT, "实例正在维护或已经归档；此审批只可读取。")
                review = self.service.review(parts[1], access_token=token)
                if review.request.kind in _RETIRED_INSTANCE_APPROVAL_KINDS:
                    return self._error(
                        handler,
                        HTTPStatus.CONFLICT,
                        "该实例管理审批已经停用，只能读取历史内容。",
                    )
                with self._approval_write_guard(parts[1]):
                    executions = None
                    if additional_seconds:
                        owner = self._approval_owner(parts[1])
                        if owner is None or self.read_model is None:
                            raise ApprovalError('当前审批没有可调整的累计执行预算')
                        executions = self._model(owner[0].instance_id).executions
                    decide = executions.record_budget_extension_decision if executions is not None else self.service.record_ui_decision
                    decision_ref = decide(
                        approval_id=parts[1],
                        access_token=token,
                        csrf_token=csrf,
                        decision_nonce=nonce,
                        selected_option=selected,
                        rationale=rationale,
                        decided_by=self.identity,
                        ui_session_id=self.session_id,
                        additional_wall_time_seconds=additional_seconds,
                    )
            except BudgetExtensionAccountingError as error:
                return self._error(handler, HTTPStatus.CONFLICT, str(error))
            except ApprovalAccessDenied:
                return self._error(handler, HTTPStatus.FORBIDDEN)
            except ExecutionBudgetNotApplicable:
                return self._error(handler, HTTPStatus.CONFLICT, '此执行未配置累计耗时预算，不支持追加耗时；可按原审批选项处理。')
            except ExecutionApprovalError:
                return self._error(handler, HTTPStatus.CONFLICT, '执行授权与预算记录不一致，请修复相关控制记录后重试。')
            except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
                LOGGER.exception('stored approval or budget record is invalid')
                return self._error(handler, HTTPStatus.CONFLICT, '已有审批或预算记录无法校验，请修复记录后重试。')
            except ApprovalExpired:
                return self._error(handler, HTTPStatus.GONE)
            except (ApprovalError, CASIntegrityError):
                return self._error(handler, HTTPStatus.CONFLICT)
        location = f"/review/{parts[1]}?token={token}"
        self._respond(
            handler,
            HTTPStatus.SEE_OTHER,
            b"",
            "text/plain; charset=utf-8",
            extra_headers={
                "Location": location,
                "X-Decision-Artifact": decision_ref.artifact_id,
            },
        )

    def _handle_instance_post(
        self, handler: BaseHTTPRequestHandler, *, action: str
    ) -> None:
        if not self._valid_host(handler):
            return self._error(handler, HTTPStatus.FORBIDDEN)
        if self.bindings is None:
            return self._error(handler, HTTPStatus.NOT_FOUND)
        origin = handler.headers.get("Origin")
        if not self._valid_origin(origin):
            return self._error(handler, HTTPStatus.FORBIDDEN)
        if (
            handler.headers.get("Content-Type", "").split(";", 1)[0]
            != "application/x-www-form-urlencoded"
        ):
            return self._error(handler, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        try:
            length = int(handler.headers.get("Content-Length", "-1"))
        except ValueError:
            return self._error(handler, HTTPStatus.BAD_REQUEST)
        if not 0 <= length <= MAX_POST_BYTES:
            return self._error(handler, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        try:
            form = parse_qs(
                handler.rfile.read(length).decode("utf-8", errors="strict"),
                strict_parsing=True,
                keep_blank_values=True,
            )
            if not secrets.compare_digest(
                _required(form, "csrf"), self.session_id
            ):
                raise ValueError("CSRF mismatch")
            # Local browser access is a human, same-origin form action. Only
            # changing a research conversation's binding needs its capability.
            if action in {"create", "select"}:
                capability_token = _required(form, "capability")
                capability = self._instance_capability(capability_token)
            elif self.browser_access is None:
                raise ValueError("local instance management is unavailable")
            maintenance_access = False
            with self._shared_maintenance():
                if action == "clients-clear":
                    cleared = self.bindings.clear_offline_clients(can_clear_instance=self._cache_writable)
                    return self._respond(handler, HTTPStatus.SEE_OTHER, b"", "text/plain; charset=utf-8",
                                         extra_headers={"Location": "/sessions?" + urlencode({"cleared": cleared})})
                if action == "client-state":
                    key = _required(form, "session_key")
                    enabled, expected = _required(form, "enabled"), _required(form, "expected_enabled")
                    if enabled not in {"0", "1"} or expected not in {"0", "1"}:
                        raise ValueError("invalid client state")
                    bound = self.bindings.session_instance(session_key=key)
                    if enabled == "1" and bound:
                        self.bindings.require_active_instance(instance_id=bound)
                        if not self._cache_writable(bound):
                            raise SchedulerInstanceConflict("实例正在维护，暂不能恢复调度。")
                    self.bindings.set_client_enabled(session_key=key, enabled=enabled=="1", expected=expected=="1")
                    return self._respond(handler, HTTPStatus.SEE_OTHER, b"", "text/plain; charset=utf-8",
                                         extra_headers={"Location": "/sessions"})
                elif action == "access":
                    if self.read_model is None or self.browser_access is None:
                        raise ValueError("instance workbench is unavailable")
                    instance = self.bindings.get_instance(instance_id=_required(form, "instance_id"))
                    scope = _required(form, "scope")
                    if scope not in {"read", "maintenance"}:
                        raise ValueError("unknown instance browser scope")
                    maintenance_access = scope == "maintenance"
                elif action == "create-unbound":
                    instance = self.bindings.create_instance(**{
                        key: _required(form, key) for key in ("name", "title", "objective")})
                else:
                    values = {key: _required(form, key) for key in
                        (("name", "title", "objective") if action == "create" else ("name",))}
                    target = self.bindings.select_instance(name=values["name"]) if action == "select" else None
                    snapshot = self.bindings.session_binding_snapshot(session_key=capability.session_key,
                        instance_id=target.instance_id if target else None)
                    current_id = snapshot["current_instance_id"]
                    expected = _one(form, "expected_binding")
                    confirmed = _one(form, "confirm_binding") == "yes"
                    if confirmed and expected != snapshot["fingerprint"]:
                        raise SchedulerInstanceConflict("会话绑定已变化，请返回首页重新核对后再确认。")
                    requires_confirmation = not snapshot["target_is_current"] and (current_id is not None or snapshot["target_bound"])
                    if requires_confirmation and not confirmed:
                        current_title = self.bindings.get_instance(instance_id=current_id).title if current_id else "尚未绑定"
                        return self._respond(handler, HTTPStatus.OK, confirm_binding(action=action, values=values,
                            token=capability_token, csrf=self.session_id, snapshot=snapshot, current_title=current_title,
                            target_title=target.title if target else values["title"],
                            activity=self._binding_activity((current_id, target.instance_id if target else None))),
                            "text/html; charset=utf-8")
                    if snapshot["target_is_current"]:
                        instance = target  # Browsing the current binding does not renew or rewrite it.
                    elif action == "create":
                        instance = self.bindings.create_instance_and_bind_session(session_key=capability.session_key,
                            expected_binding=expected if confirmed else snapshot["fingerprint"], **values)
                    else:
                        instance = self.bindings.bind_session_by_name(session_key=capability.session_key,
                            expected_binding=expected if confirmed else snapshot["fingerprint"], **values)
        except (
            UnicodeDecodeError,
            InstanceManagementCapabilityError,
            ValueError,
        ):
            return self._error(handler, HTTPStatus.FORBIDDEN)
        except SchedulerInstanceNotFound:
            return self._error(handler, HTTPStatus.NOT_FOUND)
        except SchedulerBindingError as error:
            return self._error(handler, HTTPStatus.CONFLICT, str(error))
        response_headers = {"Location": f"/instance/{instance.instance_id}"}
        if action == "access" and maintenance_access and _one(form, "destination") in {"manage", "settings"}:
            response_headers["Location"] += "/" + _one(form, "destination")
        if self.browser_access is not None:
            response_headers["Set-Cookie"] = access_cookie(instance.instance_id,
                self.browser_access.issue(instance.instance_id, maintenance=maintenance_access))
        self._respond(
            handler,
            HTTPStatus.SEE_OTHER,
            b"",
            "text/plain; charset=utf-8",
            extra_headers=response_headers,
        )

    def _handle_access_refresh(
        self, handler: BaseHTTPRequestHandler, *, approval_id: str
    ) -> None:
        origin = handler.headers.get("Origin")
        if not self._valid_origin(origin):
            return self._error(handler, HTTPStatus.FORBIDDEN)
        if (
            handler.headers.get("Content-Type", "").split(";", 1)[0]
            != "application/x-www-form-urlencoded"
        ):
            return self._error(handler, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        try:
            length = int(handler.headers.get("Content-Length", "-1"))
        except ValueError:
            return self._error(handler, HTTPStatus.BAD_REQUEST)
        if not 0 <= length <= MAX_POST_BYTES:
            return self._error(handler, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        try:
            form = parse_qs(
                handler.rfile.read(length).decode("utf-8", errors="strict"),
                strict_parsing=True,
            )
            if not secrets.compare_digest(
                _required(form, "csrf"), self.session_id
            ):
                raise ValueError("CSRF mismatch")
            with self._shared_maintenance():
                if self._approval_frozen(approval_id):
                    return self._error(handler, HTTPStatus.CONFLICT, "实例正在维护或已经归档；不可刷新审批。")
                with self._approval_write_guard(approval_id):
                    launch = self.service.refresh_access(approval_id)
        except (UnicodeDecodeError, ValueError, ApprovalAccessDenied):
            return self._error(handler, HTTPStatus.FORBIDDEN)
        except ApprovalExpired:
            return self._error(handler, HTTPStatus.GONE)
        except ApprovalError:
            return self._error(handler, HTTPStatus.CONFLICT)
        self._respond(
            handler,
            HTTPStatus.SEE_OTHER,
            b"",
            "text/plain; charset=utf-8",
            extra_headers={"Location": launch.review_path},
        )

    def _instance_capability(self, token: str | None):
        if self.instance_management_secret is None or token is None:
            raise InstanceManagementCapabilityError(
                "instance-management capability is unavailable"
            )
        return verify_instance_management_capability(
            token,
            secret=self.instance_management_secret,
        )

    def _storage_status(self, instance_id):
        return (self.instance_archive.status(instance_id) if self.instance_archive is not None
                else {"storage_state": "active"})

    def _browser_grant(self, handler, instance_id, *, scope="read"):
        if self.browser_access is None:
            raise BrowserAccessDenied("实例浏览入口不可用。")
        epoch = self._storage_status(instance_id).get("browser_epoch")
        issued_after = datetime.fromtimestamp(epoch, timezone.utc) if epoch else None
        return self.browser_access.from_cookie(handler.headers.get("Cookie"), instance_id=instance_id,
            scope=scope, issued_after=issued_after)

    def _model(self, instance_id):
        if self.instance_archive is not None and self._storage_status(instance_id).get("archived_readable"):
            return self.instance_archive.archived_model(instance_id)
        return self.read_model

    def _view_store(self, instance_id):
        if self.instance_archive is not None and self._storage_status(instance_id).get("archived_readable"):
            from .trajectory import TrajectoryStore
            path = self.instance_archive.archived_views_path(instance_id)
            return TrajectoryStore(path, read_only=True) if path is not None else None
        return self.trajectory_store

    def _can_retry_summary(self, instance_id, access):
        return self._summary_unavailable_reason(instance_id, access) is None

    def _summary_unavailable_reason(self, instance_id, access):
        if self.read_model is None or self.read_model.presentation_summaries is None:
            return "service_unavailable"
        if not self.read_model.runs.agent_settings.presentation.enabled:
            return "service_disabled"
        if not self.bindings.agent_settings(instance_id)["settings"].get("presentation", {}).get("enabled", True):
            return "instance_disabled"
        if not self._cache_writable(instance_id):
            return "read_only"
        if "maintenance" not in access.scopes:
            return "access_required"
        return None

    def _handle_summary_retry(self, handler, instance_id, *, generate=False):
        from .read_model import ReadModelNotFound
        if not self._valid_origin(handler.headers.get("Origin")):
            return self._error(handler, HTTPStatus.FORBIDDEN)
        if handler.headers.get("Content-Type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
            return self._error(handler, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        try:
            length = int(handler.headers.get("Content-Length", "-1"))
            if not 0 <= length <= MAX_POST_BYTES:
                return self._error(handler, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            handler.connection.settimeout(10)
            form = parse_qs(handler.rfile.read(length).decode("utf-8"), strict_parsing=True, max_num_fields=5)
            if not secrets.compare_digest(_required(form, "csrf"), self.session_id):
                raise BrowserAccessDenied("CSRF mismatch")
            with self._shared_maintenance():
                access = self._browser_grant(handler, instance_id, scope="maintenance")
                if not self._can_retry_summary(instance_id, access):
                    raise ValueError("展示整理已停用，或实例当前不可写。")
                guard = self.instance_archive.gate.guard(instance_id) if self.instance_archive else nullcontext()
                with guard:
                    key = _required(form, "node_key")
                    self.read_model.node_metadata(instance_id, key)
                    stage_reference = _one(form, "stage_reference")
                    run, ref, _ = self.read_model.display_source(instance_id, key, stage_reference)
                    existing = self.read_model.presentation_summaries.read(instance_id, ref)
                    if generate:
                        from ..service.presentation_summaries import enqueue
                        if existing is None and enqueue(self.read_model.runs, run, ref) is None:
                            raise ValueError("展示整理配置不可用。")
                    else:
                        if existing is None or (existing.get("retry") or {}).get("cache_key") != _required(form, "cache_key"):
                            # Queued/running duplicate POSTs remain harmless, but
                            # cannot be used to select another stage's cache job.
                            if existing is None or existing.get("state") not in {"queued", "running"}:
                                raise ValueError("导读版本已变化，请刷新当前阶段。")
                            return self._respond(handler, HTTPStatus.SEE_OTHER, b"", "text/plain; charset=utf-8",
                                extra_headers={"Location": f"/instance/{quote(instance_id, safe='')}/nodes/{quote(key, safe='')}"
                                    + ("?" + urlencode({"stage_reference": stage_reference}) if stage_reference else "")})
                        self.read_model.presentation_summaries.retry(instance_id,
                            _required(form, "cache_key"), int(_required(form, "generation")))
        except PermissionError as error:
            return self._error(handler, HTTPStatus.FORBIDDEN, str(error))
        except (ValueError, UnicodeError, ReadModelNotFound, SchedulerInstanceNotFound) as error:
            return self._error(handler, HTTPStatus.CONFLICT, str(error))
        return self._respond(handler, HTTPStatus.SEE_OTHER, b"", "text/plain; charset=utf-8",
            extra_headers={"Location": f"/instance/{quote(instance_id, safe='')}/nodes/{quote(key, safe='')}"
                + ("?" + urlencode({"stage_reference": stage_reference}) if stage_reference else "")})

    def _cache_writable(self, instance_id):
        if self.instance_archive is None:
            return True
        return (self._storage_status(instance_id).get("storage_state") in {"active", "restored"}
                and self.instance_archive.gate.status(instance_id) is None)

    def _approval_owner(self, approval_id):
        owner = self.bindings.find_owner(namespace="approval", object_id=approval_id) if self.bindings is not None else None
        if owner is None and self.instance_archive is not None:
            instance_id = self.instance_archive.owner("approval", approval_id)
            if instance_id is not None:
                model = self._model(instance_id)
                owner = model.bindings.find_owner(namespace="approval", object_id=approval_id)
        return owner

    def _approval_service(self, approval_id):
        owner = self._approval_owner(approval_id)
        return self._model(owner[0].instance_id).approvals if owner is not None and self.read_model is not None else self.service

    def _approval_frozen(self, approval_id):
        owner = self._approval_owner(approval_id)
        return owner is not None and not self._cache_writable(owner[0].instance_id)

    def _approval_write_guard(self, approval_id):
        owner = self._approval_owner(approval_id)
        if self.instance_archive is None or owner is None:
            return nullcontext()
        return self.instance_archive.gate.guard(owner[0].instance_id)

    def _agent_settings_html(self, instance, *, can_manage, selected="", standalone=False):
        if self.read_model is None or self.read_model.runs is None:
            return ""
        from .agent_settings import render_settings
        record = self.bindings.agent_settings(instance.instance_id)
        storage = self._storage_status(instance.instance_id).get("storage_state")
        writable = can_manage and instance.state == "active" and self._cache_writable(instance.instance_id)
        if storage == "archived":
            readonly_reason = "实例已归档，设置只读；恢复实例后才可修改。"
        elif storage in {"archiving", "restoring"} or not self._cache_writable(instance.instance_id):
            readonly_reason = "实例正在维护，暂时不能修改设置。"
        elif instance.state != "active":
            readonly_reason = "实例已关闭，设置只读。"
        else:
            readonly_reason = "当前浏览授权仅允许查看；请从实例管理中的“Agent 设置”入口取得修改权限。"
        return render_settings(instance, record, catalog=self.read_model.operation_catalog,
            global_settings=self.read_model.runs.agent_settings, csrf=self.session_id,
            writable=writable, readonly_reason=readonly_reason,
            selected=selected, standalone=standalone)

    def _handle_agent_settings_get(self, handler, instance_id, query):
        grant = self._browser_grant(handler, instance_id)
        instance = self.bindings.get_instance(instance_id=instance_id)
        body = self._agent_settings_html(instance, can_manage="maintenance" in grant.scopes,
            selected=_one(query, "operation") or "", standalone=True)
        if not body:
            return self._error(handler, HTTPStatus.NOT_FOUND)
        return self._respond(handler, HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8")

    def _handle_management_get(self, handler, instance_id, query, *, as_json=False):
        if self.management is None:
            return self._error(handler, HTTPStatus.NOT_FOUND)
        self._browser_grant(handler, instance_id, scope="maintenance")
        instance = self.bindings.get_instance(instance_id=instance_id)
        status = self.management.status(instance_id)
        action = _one(query, "view" if as_json else "preview")
        preview = self.management.preview(instance_id, action) if action else None
        if as_json:
            from .view_models import select_json
            after, limit = int(_one(query, "after") or "0"), int(_one(query, "limit") or "30")
            if after < 0 or not 1 <= limit <= 100:
                raise ValueError("invalid maintenance page")
            return self._json_response(handler, HTTPStatus.OK, select_json(preview or status,
                _one(query, "pointer") or "", after=after, limit=limit))
        from .management_render import render_management
        prefs = None
        store = self._view_store(instance_id)
        if store is not None:
            try:
                prefs = store.preferences(instance_id)
            except Exception:
                LOGGER.exception("instance display preferences unavailable")
        body = render_management(instance, status=status,
            archive_preview=preview if action == "archive" else None,
            restore_preview=preview if action == "restore" else None,
            cleanup_preview=preview if action == "cleanup" else None,
            preferences=prefs, csrf_token=self.session_id)
        return self._respond(handler, HTTPStatus.OK, body, "text/html; charset=utf-8")

    def _handle_management_post(self, handler, instance_id, action):
        if self.management is None:
            return self._error(handler, HTTPStatus.NOT_FOUND)
        if not self._valid_origin(handler.headers.get("Origin")):
            return self._error(handler, HTTPStatus.FORBIDDEN)
        if handler.headers.get("Content-Type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
            return self._error(handler, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        try:
            length = int(handler.headers.get("Content-Length", "-1"))
            if not 0 <= length <= MAX_POST_BYTES:
                return self._error(handler, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            handler.connection.settimeout(10)
            form = parse_qs(handler.rfile.read(length).decode("utf-8"), strict_parsing=True,
                keep_blank_values=True, max_num_fields=16 if action == "agent-settings" else 8)
            if not secrets.compare_digest(_required(form, "csrf_token"), self.session_id):
                raise BrowserAccessDenied("CSRF mismatch")
            # Authenticate under a short shared read. Archive owns EX later, outside it.
            with self._shared_maintenance():
                self._browser_grant(handler, instance_id, scope="maintenance")
                self.bindings.get_instance(instance_id=instance_id)
                if action == "agent-settings":
                    if self.read_model is None or self.read_model.runs is None:
                        raise ValueError("Agent 设置服务不可用。")
                    from .agent_settings import apply_form
                    current = self.bindings.agent_settings(instance_id)
                    value = apply_form(current["settings"], form, self.read_model.operation_catalog)
                    self.bindings.save_agent_settings(instance_id, value,
                        expected_revision=int(_required(form, "expected_revision")),
                        maintenance=self.instance_archive.gate)
                elif action == "preferences":
                    if not self._cache_writable(instance_id):
                        raise ValueError("归档资料或维护期间不能改写显示设置。")
                    value = _required(form, "show_artifacts")
                    if value not in {"true", "false"}:
                        raise ValueError("invalid preference")
                    with self.instance_archive.gate.guard(instance_id):
                        self.trajectory_store.preferences(instance_id, {"show_artifacts": value == "true"})
                elif _required(form, "confirm") != "confirm":
                    raise ValueError("confirmation is required")
            if action not in {"preferences", "agent-settings"}:
                self.management.start(instance_id, action, _required(form, "fingerprint"))
        except PermissionError as error:
            return self._error(handler, HTTPStatus.FORBIDDEN, str(error))
        except (ValueError, UnicodeError, SchedulerInstanceConflict, InstanceMaintenanceUnavailable) as error:
            return self._error(handler, HTTPStatus.CONFLICT, str(error))
        except Exception as error:
            LOGGER.exception("maintenance request failed")
            return self._error(handler, HTTPStatus.SERVICE_UNAVAILABLE, str(error)[:1024])
        location = f"/instance/{quote(instance_id, safe='')}/manage"
        if action == "agent-settings":
            location = f"/instance/{quote(instance_id, safe='')}/settings"
            selected = _one(form, "operation_id")
            if selected:
                location += "?operation=" + quote(selected, safe="")
        return self._respond(handler, HTTPStatus.SEE_OTHER, b"", "text/plain; charset=utf-8",
            extra_headers={"Location": location})

    def _shared_maintenance(self):
        return (
            self.maintenance.shared()
            if self.maintenance is not None
            else nullcontext()
        )

    def _valid_host(self, handler: BaseHTTPRequestHandler) -> bool:
        return self._valid_loopback_authority(handler.headers.get("Host"))

    def _valid_origin(self, origin: str | None) -> bool:
        if origin is None:
            return False
        try:
            parsed = urlparse(origin)
            if (
                parsed.scheme != "http"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in ("", "/")
                or parsed.params
                or parsed.query
                or parsed.fragment
            ):
                return False
        except ValueError:
            return False
        return self._valid_loopback_authority(parsed.netloc)

    def _valid_loopback_authority(self, authority: str | None) -> bool:
        if authority is None or self._server is None:
            return False
        try:
            parsed = urlparse(f"//{authority}")
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            return False
        if (
            hostname is None
            or port != self._server.server_port
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False
        if hostname.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            return False

    def _static(
        self,
        handler: BaseHTTPRequestHandler,
        name: str,
        content_type: str,
    ) -> None:
        self._respond(handler, HTTPStatus.OK, (STATIC_ROOT / name).read_bytes(), content_type)

    def _error(
        self,
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        message: str | None = None,
    ) -> None:
        body = f"{status.value} {status.phrase}"
        if message is not None and message.strip():
            body += "\n" + message.strip()
        if "text/html" in handler.headers.get("Accept", ""):
            from .home_render import page
            return self._respond(handler, status, page("页面暂不可用",
                "<p>" + html.escape(body) + "</p><p>可返回首页查看当前授权与实例入口。</p>"),
                "text/html; charset=utf-8")
        self._respond(
            handler,
            status,
            (body + "\n").encode("utf-8"),
            "text/plain; charset=utf-8",
        )

    def _respond(
        self,
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if getattr(handler, "_defer_reply", False):
            handler._pending_reply = lambda: self._respond(handler, status, body, content_type, extra_headers=extra_headers)
            return
        handler.send_response(status)
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Content-Security-Policy": "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            "X-Frame-Options": "DENY",
            # Chrome serializes form POST Origin as "null" under no-referrer,
            # which makes the same-origin decision form fail our Origin gate.
            "Referrer-Policy": "same-origin",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        if extra_headers:
            headers.update(extra_headers)
        for name, value in headers.items():
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(body)


def _render_dashboard(
    items: tuple[object, ...],
    *,
    context_for,
    home_controls: str = "",
    csrf_token: str,
) -> bytes:
    rows = []
    for item in items:
        review_path = getattr(item, "review_path", None)
        access_expired = bool(getattr(item, "access_expired", False))
        if review_path is None and not access_expired:
            continue
        kind_value = str(getattr(item, "kind", "review"))
        kind = html.escape(
            {
                "scientific_foundation": "科学基础审批",
                "execution_authorization": "执行授权审批",
                "review": "科研审批",
            }.get(kind_value, kind_value)
        )
        question = html.escape(str(getattr(item, "question", "待审批事项")))
        created_at = html.escape(str(getattr(item, "created_at", "")))
        href = html.escape(str(review_path), quote=True)
        approval_id = html.escape(
            str(getattr(item, "approval_id", "")), quote=True
        )
        context = context_for(str(getattr(item, "approval_id", "")))
        if context is None:
            instance = (
                "<div class='approval-instance approval-instance-unbound'>"
                "<span>未绑定研究实例</span></div>"
            )
            approval_name = "旧审批"
        else:
            instance = (
                "<div class='approval-instance'>"
                "<span class='approval-instance-label'>研究实例</span>"
                f"<strong>{html.escape(context.instance_title)}</strong>"
                f"<code>{html.escape(context.instance_name)}</code></div>"
            )
            approval_name = context.approval_name
        summary = (
            f"{instance}"
            "<div class='approval-summary'>"
            f"<h2>{question}</h2>"
            "<p>"
            f"<span class='approval-kind'>{kind}</span>"
            f"<span>审批项：<code>{html.escape(approval_name)}</code></span>"
            f"<time>{created_at}</time></p></div>"
        )
        if access_expired:
            csrf = html.escape(csrf_token, quote=True)
            action = f"/review/{approval_id}/refresh-access"
            rows.append(
                "<li class='approval-item'>"
                f"<div>{summary}</div>"
                f"<form method='post' action='{action}'>"
                f"<input type='hidden' name='csrf' value='{csrf}'>"
                "<button type='submit'>刷新访问链接</button></form></li>"
            )
        else:
            rows.append(
                "<li class=\"approval-item\">"
                f"<a href=\"{href}\">{summary}"
                "<span class='approval-open' aria-hidden='true'>查看</span>"
                "</a></li>"
            )
    content = (
        "<ul class=\"approval-list\">" + "".join(rows) + "</ul>"
        if rows
        else "<p class=\"empty-state\">当前没有待审批事项。</p>"
    )
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>科研工作台 · 首页</title><link rel='stylesheet' href='/static/style.css'>"
        "<script src='/static/home.js' defer></script></head><body>"
        + navigation() + "<main class='home-page'><header><p class='eyebrow'>SciDiscovery</p><h1>科研工作台</h1>"
        "<p>管理研究实例，查看进展，处理人工审批。</p></header>"
        "<div class='home-entry-grid'><a class='home-entry' href='/instances/manage'><strong>实例管理</strong>"
        "<span>查看实例当前状态、研究轨迹和阶段成果</span></a>"
        "<a class='home-entry' href='/sessions'><strong>科研会话</strong><span>处理客户端绑定申请，暂停或恢复后续调度</span></a>"
        "<a class='home-entry' href='#create-instance'><strong>创建实例</strong><span>建立新的研究实例</span></a>"
        "<a class='home-entry' href='#pending-approvals'><strong>待审批事项</strong><span>审阅原请求并记录决定</span></a></div>"
        + home_controls + "<details class='research-panel' id='pending-approvals'><summary>待审批事项 · " + str(len(rows))
        + "</summary><div class='research-panel-body'>" + content + "</div></details></main></body></html>"
    ).encode("utf-8")


def _render_instance_page(
    instance: object,
    history: tuple[object, ...],
) -> bytes:
    name_raw = str(getattr(instance, "name", ""))
    name = html.escape(name_raw)
    title = html.escape(str(getattr(instance, "title", "")))
    objective = html.escape(str(getattr(instance, "objective", "")))
    state_value = str(getattr(instance, "state", ""))
    state = html.escape(
        {"active": "进行中", "proposed": "待创建", "binding": "待绑定"}.get(
            state_value, state_value
        )
    )
    created_at = html.escape(str(getattr(instance, "created_at", "")))

    history_rows = []
    for item in history:
        status_value = str(getattr(item, "status", "unknown"))
        status = html.escape(
            {
                "pending": "待审批",
                "decided": "已决定",
                "expired": "已过期",
                "cancelled_by_human": "已由用户取消",
                "unknown": "未知",
            }.get(status_value, status_value)
        )
        question = html.escape(str(getattr(item, "question", "审批事项")))
        kind_value = str(getattr(item, "kind", "review"))
        kind = html.escape(
            {
                "scientific_foundation": "科学基础审批",
                "execution_authorization": "执行授权审批",
                "review": "科研审批",
            }.get(kind_value, kind_value)
        )
        created = html.escape(str(getattr(item, "created_at", "")))
        selected = getattr(item, "selected_label", None)
        rationale = getattr(item, "rationale", None)
        decided_at = getattr(item, "decided_at", None)
        decision = (
            "<div class='history-decision'>"
            f"<strong>结果：{html.escape(str(selected))}</strong>"
            + (
                f"<p>理由：{html.escape(str(rationale))}</p>"
                if rationale
                else "<p>未填写理由。</p>"
            )
            + (f"<time>{html.escape(str(decided_at))}</time>" if decided_at else "")
            + "</div>"
            if selected is not None
            else "<p class='history-pending'>尚未作出决定。</p>"
        )
        review_path = getattr(item, "review_path", None)
        open_link = (
            f"<a class='history-open' href='{html.escape(str(review_path), quote=True)}'>打开审批</a>"
            if review_path
            else ""
        )
        history_rows.append(
            "<li class='history-item'><div class='history-heading'>"
            f"<h3>{question}</h3><span>{status}</span></div>"
            f"<p><code>{kind}</code><time>{created}</time></p>{decision}{open_link}</li>"
        )
    history_content = (
        "<ol class='approval-history'>" + "".join(history_rows) + "</ol>"
        if history_rows
        else "<p class='empty-state'>这个实例还没有审批记录。</p>"
    )

    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title} · 实例管理</title>"
        "<link rel='stylesheet' href='/static/style.css'></head><body>"
        + navigation(str(getattr(instance, "instance_id", ""))) + "<main class='instance-page'>"
        f"<header><p class='eyebrow'>研究实例 · {state}</p><h1>{title}</h1>"
        f"<code>{name}</code><p>{objective}</p><time>{created_at}</time></header>"
        f"<section><h2>历史审批结果</h2>{history_content}</section>"
        "</main></body></html>"
    ).encode("utf-8")


def _one(values: dict[str, list[str]], key: str) -> str | None:
    items = values.get(key)
    return items[0] if items is not None and len(items) == 1 else None


def _required(
    values: dict[str, list[str]], key: str, *, allow_blank: bool = False
) -> str:
    value = _one(values, key)
    if value is None or (not allow_blank and not value):
        raise ValueError(f"missing form field: {key}")
    return value


__all__ = ["ApprovalUI", "MAX_POST_BYTES"]
