"""Hiring-manager product workflow for Recruiter-Na v5 Candidate Decision."""
from __future__ import annotations

import json
import re
import sys
from typing import Any

from rn.hiring_requests import HiringRequestRepository
from rn.progress import emit_progress
from rn.resume_screening.decision.screening_v5 import UnsupportedScreeningPolicy
from rn.resume_screening.decision.candidate_decision_v5 import applicable_tag_names
from rn.resume_screening.decision.criteria_adapter_v5 import build_actual_criteria, supports_inspection_technician
from rn.resume_screening.jd.generator import build_request_jd, render_jd_summary
from rn.resume_delivery import ResumeDeliveryError


def _diagnose(event: str, **fields: Any) -> None:
    """Host-side diagnostics for journalctl. Never part of a hiring-manager reply."""
    detail = " ".join(f"{key}={value}" for key, value in fields.items() if value not in (None, ""))
    print(f"[workflow] {event}{' ' + detail if detail else ''}", file=sys.stderr, flush=True)


# 这条消息是否可能与"已选需求"有关（简历补发 / 联系安排）。只用来决定要不要把
# 消息交给已选需求解释，不构成意图判定本身。
SELECTED_CONTEXT_HINT = re.compile(
    r"简历|附件|原件|下载|链接|没看到|没收到|重发|补发|改选|换成|改成|联系|外呼|电话|沟通|语音"
)


def _candidate_contact(request: dict[str, Any]) -> dict[str, Any]:
    contact = (request.get("request") or {}).get("candidate_contact") or {}
    return contact if isinstance(contact, dict) else {}


def _contact_reference(text: str, contact: dict[str, Any]) -> bool:
    """True only when the message explicitly names the already-selected candidate."""
    name = str(contact.get("candidate_name") or "").strip()
    return len(name) >= 2 and name in str(text or "")


BASIC_REQUIRED = ["position_title", "department", "headcount", "location", "salary_range", "urgency", "extra_hard_requirements"]
NO_PROFILE_REQUIRED = ["core_responsibilities", "hard_requirements", "key_experience_skills", "bonus_points"]
ACTIVE_INPUT_STATUSES = {"DRAFT", "JD_READY", "SCREENING", "EVALUATION_PENDING", "CANDIDATES_READY", "REVIEWING_CANDIDATES", "WAITING_HR_SOURCING", "NEW_CANDIDATES_AVAILABLE", "PAUSED"}

STATUS_LABELS = {
    "DRAFT": "需求待补充", "JD_READY": "JD待确认", "SCREENING": "正在筛选",
    "CANDIDATES_READY": "候选人已就绪", "REVIEWING_CANDIDATES": "候选人评审中",
    "EVALUATION_PENDING": "候选人评估待重试",
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


# ── 语义意图分类 ──

INTENT_CLASSIFY_PROMPT = """你是招聘助手意图分类器。根据用户消息和当前招聘需求状态，输出 JSON 判断用户最可能的意图。

当前需求状态: {status}
岗位名称: {title}
当前待补字段: {missing}
用户消息: {text}

可能的意图（选一个）:
- next_batch: 想换一批/继续推荐/继续筛选/再筛/再推/不合适/都看看/还有其他人/下一组/还有吗
- retry_pending: 重试尚未完成/技术失败的候选人评估，或请重新核实这些人
- select: 选某人/确定了/这个人可以/要这个/就这个
- start_screen: 开始筛选/开始推荐/确认JD/可以筛选
- pause: 暂停/挂起/先不招/暂时停
- restart_hiring: 放弃当前未完成需求并重新开始/重来/从头再来
- show_progress: 查看进展/我的需求/招聘列表
- new_hiring: 我要招/新需求/换个岗位
- modify_jd: 修改JD/加条件/改需求/补充信息
- resend_resume: 没看到已选候选人的简历/要求补发简历或下载链接
- confirm_ccc_contact: 同意/确认让企业云联络中心或语音智能体联系已选候选人
- decline_ccc_contact: 明确暂不让企业云联络中心联系已选候选人
- restricted: 想浏览简历库、画像库、JD库、所有招聘需求或其他人的需求
- generic: 其他/闲聊/不知道

若 intent 为 select，必须同时判断用户选择的是当前批次的第几位：
- selection_ordinal: 只能是 1、2、3 或 null；“第二个”“第2个”“选 2 位”等都填写对应数字。
- selection_name: 仅用户明确说出候选人姓名时填写，否则为空字符串。

按用户真正想推进的动作理解，不依赖固定触发词；不能因为用户没有使用示例措辞而返回 generic。
不得把补充岗位字段、应届/社招画像区分、候选人选择或单纯回答问题误判成 new_hiring。

决策边界：
- 「当前待补字段」不是“无”时，说明助手刚追问过这些字段。只要用户消息是在提供这些字段的取值，意图就是 modify_jd（继续当前草稿）——哪怕一次给出部门、人数、地点、薪资、到岗多个字段，哪怕整段读起来像一段招聘描述，都不得判成 new_hiring。
- 「当前待补字段」是“无”，且消息本身是一段可独立成立的完整招聘描述时，意图才是 new_hiring。
- 用户明确表达“另外再招一个 / 新开一个 / 换成招某个岗位”（引入新的岗位或新招聘诉求）时，无论待补字段是否为空，都是 new_hiring。
- 只有用户明确在已确认的 JD 上增删、替换或调整某项要求时，才是 modify_jd；DRAFT 仍是未确认采集。

严格输出 JSON，不要额外文字:
{{"intent":"<意图>","selection_ordinal":null,"selection_name":"","confidence":0.0}}
"""


def _missing_labels(request: dict[str, Any]) -> str:
    # 助手当前还在追问的字段（人类可读）。分类器据此区分“补充字段”与“另起新需求”。
    try:
        fields = _missing_fields(request)
    except Exception:
        return "无"
    return "、".join(FIELD_LABELS.get(str(name), str(name)) for name in fields) or "无"


def _classify_intent_semantic(analyzer: Any, text: str, status: str, title: str,
                              missing: str = "无") -> dict[str, Any] | None:
    """Use the configured LLM as the sole natural-language action interpreter."""
    if analyzer is None:
        return None
    try:
        prompt = INTENT_CLASSIFY_PROMPT.format(status=status, title=title or "无", text=text,
                                                missing=str(missing or "无"))
        result = analyzer._call(prompt)
        intent = str(result.get("intent", "")).strip().lower()
        valid = {"retry_pending", "next_batch", "select", "start_screen", "pause", "restart_hiring", "show_progress", "new_hiring", "modify_jd", "resend_resume", "confirm_ccc_contact", "decline_ccc_contact", "restricted", "generic"}
        if intent in valid:
            ordinal = result.get("selection_ordinal")
            ordinal = ordinal if isinstance(ordinal, int) and ordinal in {1, 2, 3} else None
            return {"intent": intent, "selection_ordinal": ordinal,
                    "selection_name": str(result.get("selection_name") or "").strip()}
        _diagnose("intent_classify_unusable", status=status, returned=intent or "<empty>")
    except Exception as exc:
        # 分类失败必须留下运维可见的原因，否则用户只会看到"暂时无法理解"。
        _diagnose("intent_classify_failed", status=status,
                  error=f"{type(exc).__name__}: {exc}")
    return None


def _selection_reference(text: str, semantic: dict[str, Any] | None) -> tuple[int | None, str]:
    """Candidate selection is entirely supplied by the semantic turn decision."""
    if (semantic or {}).get("intent") == "select":
        ordinal = semantic.get("selection_ordinal")
        if ordinal in {1, 2, 3}:
            return int(ordinal) - 1, ""
        name = str(semantic.get("selection_name") or "").strip()
        if name:
            return None, name
    return None, ""


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


def _numbered_items(rows: Any, limit: int = 4) -> list[str]:
    rendered = _display_items(rows, limit)
    if not rendered:
        return []
    numerals = "①②③④"
    return [f"{numerals[index]} {item}" for index, item in enumerate(rendered.split("；"))]


def _latest_batch(request: dict[str, Any], shown: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = (request.get("request") or {}).get("last_batch_candidate_ids")
    if ids is None:  # old snapshots remain selectable without a data migration
        return shown[-3:]
    by_id = {x["candidate_id"]: x for x in shown}
    return [by_id[cid] for cid in ids if cid in by_id]


_HARD_LABEL_FIELDS = (("education", "学历"), ("certification", "检验资格证"), ("location", "工作地点"))
_HARD_PASS_STATUSES = {"matched", "valid", "pending_exam", "eligible_to_apply", "match"}
_HARD_FAIL_STATUSES = {"not_matched", "expired", "none", "refused"}


def _hard_condition_line(hard_summary: Any) -> str:
    """资格门槛单独一行展示；它们不是「符合标签」，也不进命中率。"""
    details = (hard_summary or {}).get("details") or {}
    if not details:
        return ""
    bits = []
    for key, label in _HARD_LABEL_FIELDS:
        status = str((details.get(key) or {}).get("status") or "")
        if status in _HARD_PASS_STATUSES:
            bits.append(f"{label}✅")
        elif status in _HARD_FAIL_STATUSES:
            bits.append(f"{label}❌")
        elif status == "unknown":
            bits.append(f"{label}待核实")
    return "**硬性条件：** " + "、".join(bits) if bits else ""


def _render_candidates(request: dict[str, Any], batch: list[dict[str, Any]]) -> str:
    pending = (request.get("request") or {}).get("pending_evaluations") or []
    notice = (f"另有 {len(pending)} 位候选人因技术问题尚未完成评估，当前评估尚未结束。"
              "可回复「重试未完成的候选人」，或请 HR 人工核实。") if pending else ""
    if not batch and pending:
        return notice
    if not batch:
        return "当前简历库中与这个招聘需求相关、且尚未推荐过的候选人已经全部推荐完了。该需求已进入 **等待 HR 寻源** 状态；后续有新简历进入后可以继续推荐。"
    lines = [f"### 👥 {request.get('position_title') or '当前岗位'}｜本批推荐 {len(batch)} 人", ""]
    for idx, item in enumerate(batch, 1):
        lines.extend((["---", ""] if idx == 1 else ["", "────────────", ""]))
        readiness = "⚠️ 通过（需确认）" if item.get("readiness") == "needs_verification" else "✅ 可直接推荐"
        lines.append(f"**{idx}. {item.get('name') or item.get('candidate_id')}**｜{readiness}")
        summary = item.get("summary") or {}
        bits = [str(x) for x in [summary.get("current_position"), summary.get("degree"), (f"{summary.get('total_work_years')}年经验" if summary.get("total_work_years") not in (None, "", 0) else ""), summary.get("location")] if x]
        if bits:
            lines.append("｜".join(bits))
        tag_total = int(item.get("applicable_tag_count") or 0)
        tag_summary = f"符合标签：{int(item.get('matched_tag_count') or 0)}/{tag_total}" if tag_total else "已按本次岗位标签完成筛选"
        if tag_total:
            names = item.get("matched_tag_names")
            detail = "历史记录未保存标签明细" if names is None else ("、".join(names) or "暂无已确认命中标签")
            tag_summary += f"（{detail}）"
        lines.append(f"**通过理由：** {tag_summary}")
        hard_line = _hard_condition_line(item.get("hard_check_summary"))
        if hard_line:
            lines.append(hard_line)
        reasons = item.get("recommendation_reasons") or []
        if reasons:
            lines.append("推荐依据：" + "；".join(str(x) for x in reasons[:3]))
        verification = _numbered_items(item.get("needs_verification"), 4)
        if verification:
            lines.append("**待核实：**\n" + "\n".join(verification))
        interview = _numbered_items(item.get("interview_focus"), 4)
        if interview:
            lines.append("**面试重点：**\n" + "\n".join(interview))
    lines.append("\n---\n\n不合适可回复 **「换一批」**；看中某人可回复 **「选第1个/第2个/第3个」** 或直接说名字。")
    if notice:
        lines.append("\n" + notice)
    return "\n".join(lines)


class HiringManagerWorkflow:
    def __init__(self, *, request_repo: HiringRequestRepository, profile_repo: Any, jd_repo: Any, resume_repo: Any,
                 analyzer: Any, screening_service: Any, delivery_service: Any | None = None):
        self.requests = request_repo
        self.profiles = profile_repo
        self.jds = jd_repo
        self.resumes = resume_repo
        self.analyzer = analyzer
        self.screening = screening_service
        self.delivery = delivery_service

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
        if supports_inspection_technician(request, profile):
            jd["screening_tag_names"] = applicable_tag_names(build_actual_criteria(request, profile))
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

    def _restart(self, owner: str, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.update_for_owner(owner, request["request_id"], status="CANCELLED", event_type="REQUEST_RESTARTED")
        return {
            "handled": True,
            "request_id": request["request_id"],
            "state": "CANCELLED",
            "message": "好的，刚才未完成的招聘需求已重新开始。请直接描述这次想招的岗位和已知信息即可。",
        }

    def _redeliver_selected_resume(self, owner: str, request: dict[str, Any]) -> dict[str, Any]:
        """Prepare the already-authorized original again without changing selection."""
        result = {
            "handled": True,
            "request_id": request["request_id"],
            "state": request["status"],
            "message": "已重新准备你刚才选中的候选人简历。",
        }
        if self.delivery is None:
            result["message"] = "该候选人已选中，但简历原件暂未准备完成，HR 将协助补发。"
            return result
        try:
            result["resume_delivery"] = self.delivery.prepare(request)
        except ResumeDeliveryError as exc:
            _diagnose("resume_delivery_failed", request=request.get("request_id"),
                      candidate=request.get("selected_candidate_id"),
                      reason=str(exc) or type(exc).__name__)
            result["message"] = "该候选人已选中，但简历原件暂未准备完成，HR 将协助补发。"
        return result

    def _set_candidate_contact_status(self, owner: str, request: dict[str, Any], target: dict[str, Any], status: str,
                                      event_type: str) -> dict[str, Any]:
        payload = dict(request.get("request") or {})
        payload["candidate_contact"] = {
            "status": status,
            "candidate_id": str(target.get("candidate_id") or request.get("selected_candidate_id") or ""),
            "candidate_name": str(target.get("name") or ""),
        }
        return self.requests.update_for_owner(
            owner, request["request_id"], request=payload, status="SELECTED", event_type=event_type,
        )

    @staticmethod
    def _selected_target(request: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(request.get("selected_candidate_id") or "")
        return next((x for x in request.get("ranking") or [] if str(x.get("candidate_id") or "") == candidate_id), {})

    @staticmethod
    def _shown_selection_target(request: dict[str, Any], semantic: dict[str, Any] | None) -> dict[str, Any]:
        idx, name = _selection_reference("", semantic)
        shown_ids = {str(x) for x in request.get("recommended_candidate_ids") or []}
        shown = [x for x in request.get("ranking") or [] if str(x.get("candidate_id") or "") in shown_ids]
        latest_batch = _latest_batch(request, shown)
        if idx is not None and 0 <= idx < len(latest_batch):
            return latest_batch[idx]
        if name:
            return next((x for x in shown if name in str(x.get("name") or "")), {})
        return {}

    def _correct_selected_candidate(self, owner: str, request: dict[str, Any], semantic: dict[str, Any] | None) -> dict[str, Any]:
        target = self._shown_selection_target(request, semantic)
        if not target:
            return {
                "handled": True, "request_id": request["request_id"], "state": "SELECTED",
                "message": "当前已选候选人保持不变。请从本次已推荐的候选人中说明想改选的人。",
            }
        selected = self.requests.select_candidate(owner, request["request_id"], str(target["candidate_id"]))
        selected = self._set_candidate_contact_status(owner, selected, target, "AWAITING_CONFIRMATION", "CANDIDATE_SELECTION_CORRECTED")
        result = {
            "handled": True, "request_id": request["request_id"], "state": "SELECTED",
            "message": f"已将已选候选人改为 **{target.get('name') or target.get('candidate_id')}**。后续面试/录用仍由 HR 与业务流程确认。",
        }
        if self.delivery is not None:
            try:
                result["resume_delivery"] = self.delivery.prepare(selected)
            except ResumeDeliveryError as exc:
                _diagnose("resume_delivery_failed", request=request.get("request_id"),
                          candidate=selected.get("selected_candidate_id"),
                          reason=str(exc) or type(exc).__name__)
                result["message"] += " 简历原件暂未准备完成，HR 将协助补发。"
        return result

    def _confirm_ccc_contact(self, owner: str, request: dict[str, Any]) -> dict[str, Any]:
        target = self._selected_target(request)
        if not target:
            return {"handled": True, "request_id": request["request_id"], "state": "SELECTED",
                    "message": "当前未能定位已选候选人，暂不能安排企业云联络中心联系。"}
        self._set_candidate_contact_status(owner, request, target, "CONTACT_REQUESTED", "CCC_CONTACT_REQUESTED")
        _diagnose("candidate_contact_requested", request=request["request_id"],
                  candidate=target.get("candidate_id"), capability="CCC_NOT_INTEGRATED")
        # 企业云联络中心外呼尚未接入（见 docs/reports/TENCENT_CCC_CONTACT_IMPLEMENTATION_HANDOFF_20260921.md）。
        # 不得向用人经理承诺电话、录音或语音智能体动作。
        return {"handled": True, "request_id": request["request_id"], "state": "SELECTED",
                "message": "已记录你的联系意向。企业云联络中心外呼能力尚未接入，本次不会发起电话或录音；请联系 HR 跟进该候选人的联系安排。"}

    def _decline_ccc_contact(self, owner: str, request: dict[str, Any]) -> dict[str, Any]:
        target = self._selected_target(request)
        if target:
            self._set_candidate_contact_status(owner, request, target, "DECLINED", "CCC_CONTACT_DECLINED")
        return {"handled": True, "request_id": request["request_id"], "state": "SELECTED",
                "message": "已记录，本次暂不通过企业云联络中心联系该候选人。"}

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

    def _store_evaluations(self, owner: str, request: dict[str, Any], result: dict[str, Any], *, append: bool = False) -> dict[str, Any]:
        old = {x["candidate_id"]: x for x in (request["request"].get("pending_evaluations") or [])}
        attempted = set(result.get("attempted_candidate_ids") or [])
        pending = [x for cid, x in old.items() if cid not in attempted] if append else []
        for failed in result.get("failures") or []:
            cid = failed["candidate_id"]
            pending.append({"candidate_id": cid, "source_resume_ref": failed["source_resume_ref"],
                            "attempts": int(old.get(cid, {}).get("attempts", 0)) + 1})
        return self.requests.set_recommendations_for_owner(
            owner, request["request_id"], result.get("recommendations") or [],
            resume_count=int(result.get("resume_count") or 0), trace_dir=str(result.get("trace_dir") or ""),
            pending_evaluations=pending, append=append)

    def _retry_pending(self, owner: str, request: dict[str, Any]) -> dict[str, Any]:
        pending = request["request"].get("pending_evaluations") or []
        if not pending:
            return {"handled": True, "state": request["status"], "candidates": [], "message": "目前没有技术未完成的候选人需要重试。"}
        try:
            result = self.screening.screen_request(request, retry_only=pending)
        except Exception as exc:
            _diagnose("evaluation_retry_failed", request=request["request_id"], error=str(exc))
            return {"handled": True, "state": request["status"], "candidates": [],
                    "message": "重试暂未完成，待核实候选人已保留，可稍后重试或请 HR 人工核实。"}
        self._store_evaluations(owner, request, result, append=True)
        batch = self.requests.next_batch(owner, request["request_id"], 3)
        latest = self.requests.get_for_owner(owner, request["request_id"]) or request
        return {"handled": True, "request_id": request["request_id"], "state": latest["status"],
                "candidates": batch, "message": _render_candidates(latest, batch)}

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
        except Exception as exc:
            # 后端/凭据类故障只对运维可见，绝不把内部错误原文回给用人经理。
            restored = self.requests.update_for_owner(owner, request["request_id"], status="JD_READY", event_type="SCREENING_BACKEND_UNAVAILABLE")
            _diagnose("screening_failed", request=request["request_id"],
                      error=f"{type(exc).__name__}: {exc}")
            return {
                "handled": True,
                "request_id": request["request_id"],
                "state": restored["status"],
                "message": "筛选服务当前不可用，你的招聘需求与 JD 已保留。请联系管理员处理后再回复「开始筛选」。",
            }
        recommendations = result.get("recommendations") or []
        stored = self._store_evaluations(owner, request, result)
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
            pending = item["request"].get("pending_evaluations") or []
            extra = f"｜{len(pending)} 人评估未完成，可重试或请 HR 核实" if pending else ""
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

        # 选中（SELECTED）不是可继续填写的采集状态，但它也**不得接管该账号之后的
        # 每一条消息**：否则会吞掉新招聘需求的字段补充（"紧急"），并把不相关的话
        # 误判成"确认联系"。因此改为——活跃需求优先；只有在该消息确实指向已选需求
        # 时（没有活跃需求，或消息谈到简历/联系）才用已选需求解释意图。
        active = self._request_from_text(owner, t)
        selected = self.requests.list_for_owner(owner, statuses={"SELECTED"}, limit=1)
        if selected:
            selected_request = selected[0]
            contact = _candidate_contact(selected_request)
            has_selection = bool(str(selected_request.get("selected_candidate_id") or contact.get("candidate_id") or "").strip())
            related = bool(SELECTED_CONTEXT_HINT.search(t))
            if has_selection and (active is None or related):
                selected_semantic = _classify_intent_semantic(
                    self.analyzer, t, "SELECTED", str(selected_request.get("position_title") or ""),
                )
                selected_intent = (selected_semantic or {}).get("intent")
                if selected_intent == "resend_resume":
                    return self._redeliver_selected_resume(owner, selected_request)
                # 改选只发生在没有活跃需求时；否则"选第 N 个"属于当前批次所在的需求。
                if selected_intent == "select" and active is None:
                    return self._correct_selected_candidate(owner, selected_request, selected_semantic)
                # 联系意向必须由消息本身谈到"联系"来佐证，避免任何肯定式回复都被当成同意外呼。
                contact_routed = related and (active is None or _contact_reference(t, contact))
                if contact_routed and selected_intent == "confirm_ccc_contact":
                    return self._confirm_ccc_contact(owner, selected_request)
                if contact_routed and selected_intent == "decline_ccc_contact":
                    return self._decline_ccc_contact(owner, selected_request)

        request = active
        if request is None:
            top_decision = _classify_intent_semantic(self.analyzer, t, "none", "")
            top_intent = (top_decision or {}).get("intent")
            if top_intent == "new_hiring":
                return self._start(owner, t, conversation_id)
            if top_intent == "show_progress":
                return self._list_progress(owner)
            if top_intent == "restricted":
                return {"handled": True, "message": "这个机器人面向用人部门，不提供全库浏览、其他人的招聘需求或简历数据库管理。我可以帮你发起招聘需求，或查看你自己的招聘进展。"}
            if top_intent == "restart_hiring":
                return {"handled": True, "message": "好的，请直接描述这次想招的岗位和已知信息即可，我会从新的招聘需求开始。"}
            if top_decision is None:
                return {"handled": True, "message": "当前暂时无法理解这条消息，请稍后换一种说法再试。"}
            return {"handled": True, "message": "我是面向用人部门的招聘娜。你可以直接说 **\"我要招一个……\"** 发起招聘需求，或说 **\"查看我的招聘进展\"**。"}

        # ── 所有普通自然语言由模型统一解释为工作流行动 ──
        req_status = request.get("status", "")
        req_title = request.get("position_title", "")
        semantic = _classify_intent_semantic(self.analyzer, t, req_status, req_title, _missing_labels(request))
        intent = (semantic or {}).get("intent")

        if semantic is None:
            return {"handled": True, "request_id": request["request_id"], "state": req_status,
                    "message": "当前暂时无法理解这条消息，请稍后换一种说法再试。"}

        if intent == "restricted":
            return {"handled": True, "message": "这个机器人面向用人部门，不提供全库浏览、其他人的招聘需求或简历数据库管理。我可以帮你发起招聘需求，或查看你自己的招聘进展。"}

        if intent == "pause":
            paused = self.requests.update_for_owner(owner, request["request_id"], status="PAUSED", extra={"previous_status": request["status"]}, event_type="REQUEST_PAUSED")
            return {"handled": True, "request_id": request["request_id"], "state": paused["status"], "message": f"已暂停 **{request.get('position_title')}** 的招聘需求。之后说「继续 {request.get('position_title')}」即可恢复。"}

        if intent == "restart_hiring":
            return self._restart(owner, request)

        if intent == "show_progress":
            return self._list_progress(owner)

        if intent == "new_hiring":
            return self._start(owner, t, conversation_id)

        if intent == "retry_pending" and req_status in {"CANDIDATES_READY", "REVIEWING_CANDIDATES", "EVALUATION_PENDING", "WAITING_HR_SOURCING"}:
            return self._retry_pending(owner, request)

        # ── 按状态路由 ──
        if req_status == "DRAFT":
            return self._update_draft(owner, request, t)

        if req_status == "JD_READY":
            if intent == "start_screen":
                return self._screen(owner, request)
            if intent == "modify_jd":
                analysis = self.analyzer.analyze_update(t, request, [])
                payload = _merge_analysis(request, analysis)
                request = self.requests.update_for_owner(owner, request["request_id"], request=payload, status="DRAFT", event_type="JD_CHANGE_REQUESTED")
                return self._complete_intake(owner, request)
            return {"handled": True, "request_id": request["request_id"], "state": "JD_READY", "message": "JD 已经整理完成，回复 **\"开始筛选\"** 即可开始筛选候选人。"}

        if req_status in {"CANDIDATES_READY", "REVIEWING_CANDIDATES", "EVALUATION_PENDING"}:
            if intent == "select":
                idx, name = _selection_reference(t, semantic)
                if idx is not None or name:
                    ranking = request.get("ranking") or []
                    shown_ids = request.get("recommended_candidate_ids") or []
                    shown = [x for x in ranking if x.get("candidate_id") in shown_ids]
                    target = _latest_batch(request, shown)[idx] if idx is not None and len(_latest_batch(request, shown)) > idx else next((x for x in shown if name and name in str(x.get("name") or "")), None)
                    if target:
                        selected = self.requests.select_candidate(owner, request["request_id"], str(target["candidate_id"]))
                        result = {"handled": True, "request_id": request["request_id"], "state": selected["status"], "message": f"已记录你选择 **{target.get('name') or target.get('candidate_id')}**。该招聘需求已进入 SELECTED 状态，后续面试/录用仍由 HR 与业务流程确认。"}
                        selected = self._set_candidate_contact_status(owner, selected, target, "AWAITING_CONFIRMATION", "CANDIDATE_CONTACT_PENDING_HR")
                        if self.delivery is not None:
                            try:
                                result["resume_delivery"] = self.delivery.prepare(selected)
                            except ResumeDeliveryError as exc:
                                _diagnose("resume_delivery_failed", request=request.get("request_id"),
                                          candidate=target.get("candidate_id"),
                                          reason=str(exc) or type(exc).__name__)
                                result["message"] += " 简历原件暂未准备完成，HR 将协助补发。"
                        return result
            idx, name = _selection_reference(t, semantic)
            if idx is not None or name:
                ranking = request.get("ranking") or []
                shown_ids = request.get("recommended_candidate_ids") or []
                shown = [x for x in ranking if x.get("candidate_id") in shown_ids]
                target = _latest_batch(request, shown)[idx] if idx is not None and len(_latest_batch(request, shown)) > idx else next((x for x in shown if name and name in str(x.get("name") or "")), None)
                if target:
                    selected = self.requests.select_candidate(owner, request["request_id"], str(target["candidate_id"]))
                    result = {"handled": True, "request_id": request["request_id"], "state": selected["status"], "message": f"已记录你选择 **{target.get('name') or target.get('candidate_id')}**。该招聘需求已进入 SELECTED 状态，后续面试/录用仍由 HR 与业务流程确认。"}
                    selected = self._set_candidate_contact_status(owner, selected, target, "AWAITING_CONFIRMATION", "CANDIDATE_CONTACT_PENDING_HR")
                    if self.delivery is not None:
                        try:
                            result["resume_delivery"] = self.delivery.prepare(selected)
                        except ResumeDeliveryError as exc:
                            _diagnose("resume_delivery_failed", request=request.get("request_id"),
                                      candidate=target.get("candidate_id"),
                                      reason=str(exc) or type(exc).__name__)
                            result["message"] += " 简历原件暂未准备完成，HR 将协助补发。"
                    return result
            if intent == "next_batch":
                batch = self.requests.next_batch(owner, request["request_id"], 3)
                latest = self.requests.get_for_owner(owner, request["request_id"]) or request
                return {"handled": True, "request_id": request["request_id"], "state": latest["status"], "candidates": batch, "message": _render_candidates(latest, batch)}
            return {"handled": True, "request_id": request["request_id"], "state": request["status"], "message": "当前正在候选人评审阶段。说「换一批」获取下一组 3 人，或说「选第1个/第2个/第3个」。"}

        if req_status == "SCREENING":
            if intent == "next_batch":
                old_seen = set(request.get("recommended_candidate_ids") or [])
                result = self.screening.screen_request(request)
                recs = result.get("recommendations") or []
                new_ranking = [x for x in recs if x.get("candidate_id") not in old_seen]
                self._store_evaluations(owner, request, {**result, "recommendations": new_ranking})
                batch = self.requests.next_batch(owner, request["request_id"], 3)
                latest = self.requests.get_for_owner(owner, request["request_id"]) or request
                return {"handled": True, "request_id": request["request_id"], "state": latest["status"], "candidates": batch, "message": _render_candidates(latest, batch)}
            return {"handled": True, "request_id": request["request_id"], "state": req_status, "message": "服务中断后状态重置中。回复「继续推荐」从断点恢复。"}
        if req_status == "WAITING_HR_SOURCING":
            if intent == "next_batch":
                old_seen = set(request.get("recommended_candidate_ids") or [])
                result = self.screening.screen_request(request)
                new_ranking = [x for x in (result.get("recommendations") or []) if x.get("candidate_id") not in old_seen]
                self._store_evaluations(owner, request, {**result, "recommendations": new_ranking})
                batch = self.requests.next_batch(owner, request["request_id"], 3)
                latest = self.requests.get_for_owner(owner, request["request_id"]) or request
                return {"handled": True, "request_id": request["request_id"], "state": latest["status"], "candidates": batch, "message": _render_candidates(latest, batch)}
            if intent is None:
                return {"handled": True, "request_id": request["request_id"], "state": req_status, "message": "这个招聘需求当前在 **等待 HR 寻源**。有新简历进入后，回复「继续推荐」即可重新筛选。"}
            return {"handled": True, "request_id": request["request_id"], "state": req_status, "message": "这个招聘需求在等待新简历。有新简历入库后随时告诉我。"}

        if req_status == "PAUSED":
            if intent == "next_batch":
                target_status = request.get("previous_status") or ("REVIEWING_CANDIDATES" if request.get("ranking") else "JD_READY")
                restored = self.requests.update_for_owner(owner, request["request_id"], status=target_status, event_type="REQUEST_RESUMED")
                if restored["status"] in {"CANDIDATES_READY", "REVIEWING_CANDIDATES", "EVALUATION_PENDING"}:
                    batch = self.requests.next_batch(owner, restored["request_id"], 3)
                    latest = self.requests.get_for_owner(owner, restored["request_id"]) or restored
                    return {"handled": True, "request_id": restored["request_id"], "state": latest["status"], "candidates": batch, "message": _render_candidates(latest, batch)}
                return {"handled": True, "request_id": restored["request_id"], "state": restored["status"], "message": "已恢复这个招聘需求，可以继续处理。"}
            return {"handled": True, "request_id": request["request_id"], "state": "PAUSED", "message": "这个招聘需求目前已暂停。说「继续」可恢复。"}

        return {"handled": True, "request_id": request["request_id"], "state": req_status, "message": f"该招聘需求当前状态为 {req_status}。"}
