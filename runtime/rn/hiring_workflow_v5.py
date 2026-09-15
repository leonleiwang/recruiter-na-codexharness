"""Hiring-manager product workflow for Recruiter-Na v5 Candidate Decision."""
from __future__ import annotations

import json
import re
from typing import Any

from rn.hiring_jd_v4 import build_request_jd, render_jd_summary
from rn.hiring_requests import HiringRequestRepository
from rn.progress import emit_progress
from rn.screening_v5 import UnsupportedScreeningPolicy

BASIC_REQUIRED = ["position_title", "department", "headcount", "location", "salary_range", "urgency", "extra_hard_requirements"]
NO_PROFILE_REQUIRED = ["core_responsibilities", "hard_requirements", "key_experience_skills", "bonus_points"]
ACTIVE_INPUT_STATUSES = {"DRAFT", "JD_READY", "SCREENING", "CANDIDATES_READY", "REVIEWING_CANDIDATES", "WAITING_HR_SOURCING", "NEW_CANDIDATES_AVAILABLE", "PAUSED"}

STATUS_LABELS = {
    "DRAFT": "需求待补充", "JD_READY": "JD待确认", "SCREENING": "正在筛选",
    "CANDIDATES_READY": "候选人已就绪", "REVIEWING_CANDIDATES": "候选人评审中",
    "WAITING_HR_SOURCING": "等待HR寻源", "NEW_CANDIDATES_AVAILABLE": "有新候选人可筛",
    "SELECTED": "已选中候选人", "PAUSED": "已暂停", "CANCELLED": "已取消", "CLOSED": "已关闭",
}

FIELD_LABELS = {
    "position_title": "岗位名称",
    "department": "用人部门/团队",
    "headcount": "HC 数量",
    "location": "工作地点",
    "salary_range": "薪资预算",
    "urgency": "期望到岗时间/紧急程度",
    "extra_hard_requirements": "本次招聘额外必须满足的条件（没有请写「没有」）",
    "core_responsibilities": "核心职责（3–5 条即可）",
    "hard_requirements": "硬性门槛",
    "key_experience_skills": "关键经验/技能",
    "bonus_points": "加分项（没有请写「没有」）",
}


def _profiles(repo: Any) -> list[Any]:
    if hasattr(repo, "list_all"):
        return list(repo.list_all())
    if hasattr(repo, "all"):
        return list(repo.all())
    return []


# ── 保留硬正则作为 LLM 不可用时的 fallback ──

def _start_intent(text: str) -> bool:
    return bool(re.search(r"我要招|我想招|想招一个|招一个|招个|需要招聘|需要招|帮我招|招聘需求|招人", str(text or "")))


def _progress_intent(text: str) -> bool:
    return bool(re.search(r"查看.*招聘.*进展|我的.*招聘|招聘.*进度|招聘.*进展|我.*哪些.*需求|我的需求", str(text or "")))


def _restricted_intent(text: str) -> bool:
    return bool(re.search(r"画像库|JD\s*库|JD库|简历库.*多少|多少.*简历|所有.*简历|全部.*岗位|所有.*招聘需求|别人的招聘", str(text or ""), re.I))


def _next_batch_intent(text: str) -> bool:
    return bool(re.search(r"换一批|再换|下一批|不合适|都不合适|继续推荐|^继续(?:\s|$)|继续\s*REQ-", str(text or ""), re.I))


def _start_screen_intent(text: str) -> bool:
    return bool(re.search(r"开始筛选|开始推荐|确认.*JD|JD.*确认|可以.*筛|没问题.*筛", str(text or ""), re.I))


def _pause_intent(text: str) -> bool:
    return bool(re.search(r"暂停|先挂起|暂时不招|先放着", str(text or "")))


def _select_intent(text: str) -> tuple[int | None, str]:
    t = str(text or "").strip()
    m = re.search(r"(?:选|要|就)(?:第)?\s*([123])\s*(?:个|位)?", t)
    if m:
        return int(m.group(1)) - 1, ""
    m = re.search(r"(?:选|要|就定|这个人可以)\s*[：:]?\s*([一-龥A-Za-z·]{2,20})", t)
    return (None, m.group(1).strip()) if m else (None, "")


# ── 语义意图分类 ──

INTENT_CLASSIFY_PROMPT = """你是招聘助手意图分类器。根据用户消息和当前招聘需求状态，输出 JSON 判断用户最可能的意图。

当前需求状态: {status}
岗位名称: {title}
用户消息: {text}

可能的意图（选一个）:
- next_batch: 想换一批/继续推荐/继续筛选/再筛/再推/不合适/都看看/还有其他人/下一组/还有吗
- select: 选某人/确定了/这个人可以/要这个/就这个
- start_screen: 开始筛选/开始推荐/确认JD/可以筛选
- pause: 暂停/挂起/先不招/暂时停
- show_progress: 查看进展/我的需求/招聘列表
- new_hiring: 我要招/新需求/换个岗位
- modify_jd: 修改JD/加条件/改需求/补充信息
- generic: 其他/闲聊/不知道

严格输出 JSON，不要额外文字:
{{"intent":"<意图>","confidence":0.0}}
"""


def _classify_intent_semantic(analyzer: Any, text: str, status: str, title: str) -> str | None:
    """用 LLM 做语义意图分类。失败返回 None 表示回退到硬正则。"""
    if analyzer is None:
        return None
    try:
        prompt = INTENT_CLASSIFY_PROMPT.format(status=status, title=title or "无", text=text)
        result = analyzer._call(prompt)
        intent = str(result.get("intent", "")).strip().lower()
        valid = {"next_batch", "select", "start_screen", "pause", "show_progress", "new_hiring", "modify_jd", "generic"}
        if intent in valid:
            return intent
    except Exception:
        pass
    return None


def _missing_fields(request: dict[str, Any]) -> list[str]:
    req = request.get("request") or {}
    fields = req.get("fields") or {}
    provided = set(req.get("provided_fields") or [])
    match = req.get("profile_match") or {}
    required = list(BASIC_REQUIRED)
    if match.get("status") != "MATCHED":
        required += NO_PROFILE_REQUIRED
    missing = []
    for key in required:
        value = fields.get(key)
        if key in provided:
            continue
        if value not in (None, "", []):
            continue
        missing.append(key)
    return missing


def _merge_analysis(request: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    payload = dict(request.get("request") or {})
    fields = dict(payload.get("fields") or {})
    fields.update({k: v for k, v in (analysis.get("fields") or {}).items() if v is not None})
    provided = list(dict.fromkeys([*(payload.get("provided_fields") or []), *(analysis.get("provided_fields") or [])]))
    payload["fields"] = fields
    payload["provided_fields"] = provided
    if analysis.get("profile_match"):
        payload["profile_match"] = analysis["profile_match"]
    return payload


def _missing_prompt(request: dict[str, Any]) -> str:
    missing = _missing_fields(request)
    match = (request.get("request") or {}).get("profile_match") or {}
    lines = []
    if match.get("status") == "MATCHED":
        lines.append(f"已匹配公司标准岗位画像 **{request.get('profile_name') or request.get('position_title') or ''}**，通用能力/经验不用你重复填写。")
    elif match.get("status") == "AMBIGUOUS":
        question = str(match.get("clarifying_question") or "请补充一个能区分岗位方向的关键信息。")
        return "当前需求可能对应多个标准岗位画像，为避免误套画像，我只确认一个关键点：\n\n" + question
    elif match.get("status") == "UNAVAILABLE":
        lines.append("标准画像语义匹配暂不可用，我不会用关键词猜测；这次按新岗位完整采集信息。")
    else:
        lines.append("当前需求未匹配到现有标准岗位画像，这次按新岗位完整采集信息。")
    lines += ["还需要补充："] + [f"- {FIELD_LABELS.get(key, key)}" for key in missing]
    lines.append("\n可以一次性回复，已经说明过的内容不用重复。")
    return "\n".join(lines)


def _display_items(rows: Any, limit: int = 4) -> str:
    out = []
    for row in (rows or [])[:limit]:
        if isinstance(row, dict):
            item = str(row.get("item") or row.get("criterion") or "").strip()
            reason = str(row.get("reason") or row.get("evidence") or row.get("detail") or "").strip()
            text = f"{item}（{reason}）" if item and reason else (item or reason)
        else:
            text = str(row).strip()
        if text:
            out.append(text)
    return "；".join(out)


def _render_candidates(request: dict[str, Any], batch: list[dict[str, Any]]) -> str:
    if not batch:
        return "当前简历库中与这个招聘需求相关、且尚未推荐过的候选人已经全部推荐完了。该需求已进入 **等待 HR 寻源** 状态；后续有新简历进入后可以继续推荐。"
    lines = [f"### 👥 {request.get('position_title') or '当前岗位'}｜本批推荐 {len(batch)} 人"]
    for idx, item in enumerate(batch, 1):
        readiness = "⚠️ 通过（需确认）" if item.get("readiness") == "needs_verification" else "✅ 可直接推荐"
        lines.append(f"**{idx}. {item.get('name') or item.get('candidate_id')}**｜{readiness}")
        summary = item.get("summary") or {}
        bits = [str(x) for x in [summary.get("current_position"), summary.get("degree"), (f"{summary.get('total_work_years')}年经验" if summary.get("total_work_years") not in (None, "", 0) else ""), summary.get("location")] if x]
        if bits:
            lines.append("- " + "｜".join(bits))
        reasons = item.get("recommendation_reasons") or []
        if reasons:
            lines.append("- 推荐依据：" + "；".join(str(x) for x in reasons[:3]))
        verification = _display_items(item.get("needs_verification"), 4)
        if verification:
            lines.append("- 待核实：" + verification)
        interview = _display_items(item.get("interview_focus"), 4)
        if interview:
            lines.append("- 面试重点：" + interview)
    lines.append("\n不合适可回复 **「换一批」**；看中某人可回复 **「选第1个/第2个/第3个」** 或直接说名字。")
    return "\n".join(lines)


class HiringManagerWorkflow:
    def __init__(self, *, request_repo: HiringRequestRepository, profile_repo: Any, jd_repo: Any, resume_repo: Any,
                 analyzer: Any, screening_service: Any):
        self.requests = request_repo
        self.profiles = profile_repo
        self.jds = jd_repo
        self.resumes = resume_repo
        self.analyzer = analyzer
        self.screening = screening_service

    def _latest(self, owner: str) -> dict[str, Any] | None:
        return self.requests.latest_for_owner(owner, statuses=ACTIVE_INPUT_STATUSES)

    def _request_from_text(self, owner: str, text: str) -> dict[str, Any] | None:
        items = self.requests.list_for_owner(owner, statuses=ACTIVE_INPUT_STATUSES, limit=50)
        t = str(text or "").casefold()
        for item in items:
            if str(item.get("request_id") or "").casefold() in t:
                return item
        title_hits = [item for item in items if item.get("position_title") and str(item["position_title"]).casefold() in t]
        if len(title_hits) == 1:
            return title_hits[0]
        return items[0] if items else None

    def _profile_for(self, request: dict[str, Any]) -> Any | None:
        profile_id = ((request.get("request") or {}).get("profile_match") or {}).get("profile_id")
        return self.profiles.get(profile_id) if profile_id and hasattr(self.profiles, "get") else None

    def _complete_intake(self, owner: str, request: dict[str, Any]) -> dict[str, Any]:
        emit_progress("JD_GENERATION", "正在合并本次招聘信息并生成专属 JD…")
        profile = self._profile_for(request)
        related = []
        try:
            related = self.jds.search_related(request.get("position_title") or "") if hasattr(self.jds, "search_related") else []
        except Exception:
            related = []
        jd = build_request_jd(request["request_id"], request["request"]["fields"], getattr(profile, "raw", None), related[0] if related else None)
        request = self.requests.update_for_owner(owner, request["request_id"], jd=jd, status="JD_READY", event_type="JD_READY")
        return {"handled": True, "request_id": request["request_id"], "state": "JD_READY", "message": render_jd_summary(jd)}

    def _start(self, owner: str, text: str, conversation_id: str) -> dict[str, Any]:
        emit_progress("PROFILE_MATCH", "正在判断这次招聘需求是否匹配公司标准岗位画像…")
        analysis = self.analyzer.analyze_initial(text, _profiles(self.profiles))
        request = self.requests.create(owner, {
            "fields": analysis.get("fields") or {},
            "provided_fields": analysis.get("provided_fields") or [],
            "profile_match": analysis.get("profile_match") or {"status": "UNASSESSED"},
            "source_text": text,
        }, owner_conversation_id=conversation_id)
        profile = self._profile_for(request)
        match = (request.get("request") or {}).get("profile_match") or {}
        if match.get("status") == "MATCHED" and profile is None:
            payload = dict(request.get("request") or {})
            payload["profile_match"] = {
                "status": "NO_MATCH", "profile_id": "", "confidence": 0.0,
                "reason": "匹配结果引用的标准画像不存在，已按无画像完整采集处理。", "candidates": [],
            }
            request = self.requests.update_for_owner(owner, request["request_id"], request=payload, status="DRAFT", event_type="PROFILE_MATCH_INVALID")
            profile = None
        if profile:
            request["profile_name"] = getattr(profile, "name", "")
        if not _missing_fields(request):
            return self._complete_intake(owner, request)
        return {"handled": True, "request_id": request["request_id"], "state": "DRAFT", "message": _missing_prompt(request)}

    def _update_draft(self, owner: str, request: dict[str, Any], text: str) -> dict[str, Any]:
        current_match = (request.get("request") or {}).get("profile_match") or {}
        if current_match.get("status") == "AMBIGUOUS":
            original = str((request.get("request") or {}).get("source_text") or "")
            analysis = self.analyzer.analyze_initial((original + "\n补充：" + text).strip(), _profiles(self.profiles))
        else:
            missing = _missing_fields(request)
            analysis = self.analyzer.analyze_update(text, request, missing)
        payload = _merge_analysis(request, analysis)
        request = self.requests.update_for_owner(owner, request["request_id"], request=payload, status="DRAFT", event_type="INTAKE_UPDATED")
        profile = self._profile_for(request)
        if profile:
            request["profile_name"] = getattr(profile, "name", "")
        if _missing_fields(request):
            return {"handled": True, "request_id": request["request_id"], "state": "DRAFT", "message": _missing_prompt(request)}
        return self._complete_intake(owner, request)

    def _screen(self, owner: str, request: dict[str, Any]) -> dict[str, Any]:
        emit_progress("SCREENING", "正在建立候选池并执行 Candidate Decision…")
        self.requests.update_for_owner(owner, request["request_id"], status="SCREENING", event_type="SCREENING_STARTED")
        try:
            result = self.screening.screen_request(request)
        except UnsupportedScreeningPolicy:
            restored = self.requests.update_for_owner(owner, request["request_id"], status="JD_READY", event_type="SCREENING_POLICY_UNSUPPORTED")
            return {
                "handled": True,
                "request_id": request["request_id"],
                "state": restored["status"],
                "message": "当前 v5.0.2 的候选人决策策略只对 **检验技术员（应届/社招）** 完成业务验收。这个岗位的招聘需求与 JD 已保留，但不会回退到旧评分系统；等对应岗位 Decision Policy 完成后再启用候选筛选。",
            }
        recommendations = result.get("recommendations") or []
        stored = self.requests.set_recommendations_for_owner(owner, request["request_id"], recommendations, resume_count=int(result.get("resume_count") or 0), trace_dir=str(result.get("trace_dir") or ""))
        batch = self.requests.next_batch(owner, request["request_id"], 3)
        stored = self.requests.get_for_owner(owner, request["request_id"]) or stored
        return {
            "handled": True, "request_id": request["request_id"], "state": stored["status"],
            "candidates": batch, "message": _render_candidates(stored, batch), "trace_dir": result.get("trace_dir", ""),
            "decision_summary": {
                "pass_count": int(result.get("pass_count") or len(recommendations)),
                "reject_count": int(result.get("reject_count") or 0),
                "technical_failure_count": int(result.get("technical_failure_count") or 0),
            },
        }

    def _list_progress(self, owner: str) -> dict[str, Any]:
        items = self.requests.list_for_owner(owner)
        if not items:
            return {"handled": True, "request_count": 0, "message": "你目前还没有发起过招聘需求。直接告诉我想招什么岗位即可开始。"}
        lines = ["### 📌 我的招聘进展"]
        for i, item in enumerate(items[:20], 1):
            total = len(item.get("ranking") or [])
            shown = len(item.get("recommended_candidate_ids") or [])
            label = STATUS_LABELS.get(item["status"], item["status"])
            extra = ""
            if item["status"] == "WAITING_HR_SOURCING":
                try:
                    current_count = int(self.resumes.count())
                    if current_count > int(item.get("last_screen_resume_count") or 0):
                        extra = "｜已有新简历进入，可继续推荐"
                except Exception:
                    pass
            lines.append(f"**{i}. {item.get('position_title') or '未命名岗位'}**｜{label}｜已推荐 {shown}/{total if total else '-'} 人｜{item['request_id']}{extra}")
        lines.append("\n需要继续某个需求，直接说岗位名或 Request ID + 「继续/换一批」。")
        return {"handled": True, "request_count": len(items), "message": "\n".join(lines)}

    def handle(self, owner_staff_id: str, text: str, conversation_id: str = "") -> dict[str, Any]:
        owner = str(owner_staff_id or "").strip()
        t = str(text or "").strip()
        if not owner:
            return {"handled": True, "message": "当前无法识别你的钉钉身份，不能创建或查看招聘需求。"}
        if not t:
            return {"handled": True, "message": "我可以帮你发起招聘需求，或查看你自己的招聘进展。"}

        # 硬正则快速命中（LLM 无关，不做语义调用）
        if _progress_intent(t):
            return self._list_progress(owner)
        if _restricted_intent(t):
            return {"handled": True, "message": "这个机器人面向用人部门，不提供全库浏览、其他人的招聘需求或简历数据库管理。我可以帮你 **发起招聘需求**，或 **查看你自己的招聘进展**。"}
        if _start_intent(t):
            return self._start(owner, t, conversation_id)

        request = self._request_from_text(owner, t)
        if request is None:
            # 最后尝试：LLM 判断这是否是 "我要招…" 语义
            top_intent = _classify_intent_semantic(self.analyzer, t, "none", "")
            if top_intent == "new_hiring":
                return self._start(owner, t, conversation_id)
            if top_intent == "show_progress":
                return self._list_progress(owner)
            return {"handled": True, "message": "我是面向用人部门的招聘娜。你可以直接说 **\"我要招一个……\"** 发起招聘需求，或说 **\"查看我的招聘进展\"**。"}

        # ── 用 LLM 语义理解代替硬正则，硬正则作为 fallback ──
        req_status = request.get("status", "")
        req_title = request.get("position_title", "")
        intent = _classify_intent_semantic(self.analyzer, t, req_status, req_title)

        # pause: 语义或正则
        if intent == "pause" or (intent is None and _pause_intent(t)):
            paused = self.requests.update_for_owner(owner, request["request_id"], status="PAUSED", extra={"previous_status": request["status"]}, event_type="REQUEST_PAUSED")
            return {"handled": True, "request_id": request["request_id"], "state": paused["status"], "message": f"已暂停 **{request.get('position_title')}** 的招聘需求。之后说「继续 {request.get('position_title')}」即可恢复。"}

        # show_progress: 语义或正则
        if intent == "show_progress" or (intent is None and _progress_intent(t)):
            return self._list_progress(owner)

        # new_hiring: 语义或正则
        if intent == "new_hiring" or (intent is None and _start_intent(t)):
            return self._start(owner, t, conversation_id)

        # ── 按状态路由 ──
        if req_status == "DRAFT":
            return self._update_draft(owner, request, t)

        if req_status == "JD_READY":
            if intent == "start_screen" or (intent is None and _start_screen_intent(t)):
                return self._screen(owner, request)
            if intent == "modify_jd":
                analysis = self.analyzer.analyze_update(t, request, [])
                payload = _merge_analysis(request, analysis)
                request = self.requests.update_for_owner(owner, request["request_id"], request=payload, status="DRAFT", event_type="JD_CHANGE_REQUESTED")
                return self._complete_intake(owner, request)
            # 任何非明确意图都当作可能需要修改 JD
            if intent is None and not _start_screen_intent(t):
                analysis = self.analyzer.analyze_update(t, request, [])
                payload = _merge_analysis(request, analysis)
                request = self.requests.update_for_owner(owner, request["request_id"], request=payload, status="DRAFT", event_type="JD_CHANGE_REQUESTED")
                return self._complete_intake(owner, request)
            return {"handled": True, "request_id": request["request_id"], "state": "JD_READY", "message": "JD 已经整理完成，回复 **\"开始筛选\"** 即可开始筛选候选人。"}

        if req_status in {"CANDIDATES_READY", "REVIEWING_CANDIDATES"}:
            if intent == "select":
                idx, name = _select_intent(t)
                if idx is not None or name:
                    ranking = request.get("ranking") or []
                    shown_ids = request.get("recommended_candidate_ids") or []
                    shown = [x for x in ranking if x.get("candidate_id") in shown_ids]
                    target = shown[-3:][idx] if idx is not None and len(shown[-3:]) > idx else next((x for x in shown if name and name in str(x.get("name") or "")), None)
                    if target:
                        selected = self.requests.select_candidate(owner, request["request_id"], str(target["candidate_id"]))
                        return {"handled": True, "request_id": request["request_id"], "state": selected["status"], "message": f"已记录你选择 **{target.get('name') or target.get('candidate_id')}**。该招聘需求已进入 SELECTED 状态，后续面试/录用仍由 HR 与业务流程确认。"}
            # 语义没有明确 select 时，仍走硬正则 select 检查
            idx, name = _select_intent(t)
            if idx is not None or name:
                ranking = request.get("ranking") or []
                shown_ids = request.get("recommended_candidate_ids") or []
                shown = [x for x in ranking if x.get("candidate_id") in shown_ids]
                target = shown[-3:][idx] if idx is not None and len(shown[-3:]) > idx else next((x for x in shown if name and name in str(x.get("name") or "")), None)
                if target:
                    selected = self.requests.select_candidate(owner, request["request_id"], str(target["candidate_id"]))
                    return {"handled": True, "request_id": request["request_id"], "state": selected["status"], "message": f"已记录你选择 **{target.get('name') or target.get('candidate_id')}**。该招聘需求已进入 SELECTED 状态，后续面试/录用仍由 HR 与业务流程确认。"}
            if intent == "next_batch" or (intent is None and _next_batch_intent(t)):
                batch = self.requests.next_batch(owner, request["request_id"], 3)
                latest = self.requests.get_for_owner(owner, request["request_id"]) or request
                return {"handled": True, "request_id": request["request_id"], "state": latest["status"], "candidates": batch, "message": _render_candidates(latest, batch)}
            return {"handled": True, "request_id": request["request_id"], "state": request["status"], "message": "当前正在候选人评审阶段。说「换一批」获取下一组 3 人，或说「选第1个/第2个/第3个」。"}

        if req_status == "SCREENING":
            if intent == "next_batch" or (intent is None and _next_batch_intent(t)):
                old_seen = set(request.get("recommended_candidate_ids") or [])
                result = self.screening.screen_request(request)
                recs = result.get("recommendations") or []
                new_ranking = [x for x in recs if x.get("candidate_id") not in old_seen]
                self.requests.set_recommendations_for_owner(owner, request["request_id"], new_ranking, resume_count=int(result.get("resume_count") or 0), trace_dir=str(result.get("trace_dir") or ""))
                batch = self.requests.next_batch(owner, request["request_id"], 3)
                latest = self.requests.get_for_owner(owner, request["request_id"]) or request
                return {"handled": True, "request_id": request["request_id"], "state": latest["status"], "candidates": batch, "message": _render_candidates(latest, batch)}
            return {"handled": True, "request_id": request["request_id"], "state": req_status, "message": "服务中断后状态重置中。回复「继续推荐」从断点恢复。"}
        if req_status == "WAITING_HR_SOURCING":
            if intent == "next_batch" or (intent is None and _next_batch_intent(t)):
                old_seen = set(request.get("recommended_candidate_ids") or [])
                result = self.screening.screen_request(request)
                new_ranking = [x for x in (result.get("recommendations") or []) if x.get("candidate_id") not in old_seen]
                self.requests.set_recommendations_for_owner(owner, request["request_id"], new_ranking, resume_count=int(result.get("resume_count") or 0), trace_dir=str(result.get("trace_dir") or ""))
                batch = self.requests.next_batch(owner, request["request_id"], 3)
                latest = self.requests.get_for_owner(owner, request["request_id"]) or request
                return {"handled": True, "request_id": request["request_id"], "state": latest["status"], "candidates": batch, "message": _render_candidates(latest, batch)}
            if intent is None:
                return {"handled": True, "request_id": request["request_id"], "state": req_status, "message": "这个招聘需求当前在 **等待 HR 寻源**。有新简历进入后，回复「继续推荐」即可重新筛选。"}
            return {"handled": True, "request_id": request["request_id"], "state": req_status, "message": "这个招聘需求在等待新简历。有新简历入库后随时告诉我。"}

        if req_status == "PAUSED":
            if intent == "next_batch" or (intent is None and _next_batch_intent(t)):
                target_status = request.get("previous_status") or ("REVIEWING_CANDIDATES" if request.get("ranking") else "JD_READY")
                restored = self.requests.update_for_owner(owner, request["request_id"], status=target_status, event_type="REQUEST_RESUMED")
                if restored["status"] in {"CANDIDATES_READY", "REVIEWING_CANDIDATES"}:
                    batch = self.requests.next_batch(owner, restored["request_id"], 3)
                    latest = self.requests.get_for_owner(owner, restored["request_id"]) or restored
                    return {"handled": True, "request_id": restored["request_id"], "state": latest["status"], "candidates": batch, "message": _render_candidates(latest, batch)}
                return {"handled": True, "request_id": restored["request_id"], "state": restored["status"], "message": "已恢复这个招聘需求，可以继续处理。"}
            return {"handled": True, "request_id": request["request_id"], "state": "PAUSED", "message": "这个招聘需求目前已暂停。说「继续」可恢复。"}

        return {"handled": True, "request_id": request["request_id"], "state": req_status, "message": f"该招聘需求当前状态为 {req_status}。"}