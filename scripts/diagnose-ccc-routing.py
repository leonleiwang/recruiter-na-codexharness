# -*- coding: utf-8 -*-
"""复现 + 验证：SELECTED 抢答分支（ISSUE-001 / ISSUE-002）。

桩分类器刻意复现线上误判：任何处于 SELECTED 上下文的句子都返回
confirm_ccc_contact。修复前，"紧急"会收到企业云联络中心那段承诺；
修复后，活跃需求优先，历史 SELECTED 需求不再接管消息。
依赖 tests/ 下的既有假件，保证与实际回归用例同源。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT / "tests"))

import test_hiring_workflow_v5 as T
from rn.hiring_requests import HiringRequestRepository
from rn.hiring_workflow_v5 import HiringManagerWorkflow

OWNER = "dingtalk:shilong"


class HijackAnalyzer(T.FakeAnalyzer):
    """SELECTED 上下文一律判成"同意联系"——即线上发生的误判。"""

    def _call(self, prompt):
        if "当前需求状态: SELECTED" in prompt:
            return {"intent": "confirm_ccc_contact", "selection_ordinal": None, "selection_name": "", "confidence": 0.9}
        return super()._call(prompt)


db = ROOT / "tmp" / "repro-requests.sqlite3"
if db.exists():
    db.unlink()
repo = HiringRequestRepository(db)
wf = HiringManagerWorkflow(request_repo=repo, profile_repo=T.FakeProfileRepo(), jd_repo=T.FakeJDRepo(),
                           resume_repo=T.FakeResumeRepo(), analyzer=HijackAnalyzer(),
                           screening_service=T.FakeScreening(), delivery_service=None)


def show(tag, out):
    text = str(out.get("message") or "").replace(chr(10), " ")
    print(f"  {tag}: state={out.get('state')} 含CCC承诺={'企业云联络中心' in text}")
    print(f"      {text[:70]}")


print("=== 场景一（ISSUE-001）：先选过人，再新开一条需求并回答「紧急」 ===")
a = wf.handle(OWNER, "我要招一个检验技术员")
a = wf.handle(OWNER, "实验室部，10-13K，尽快，没有额外硬要求")
wf.handle(OWNER, "开始筛选")
show("A 选人", wf.handle(OWNER, "第一个"))

b = wf.handle(OWNER, "我要招一个检验技术员")
print("  B 新建需求:", b["state"], b["request_id"])
show("B 回答紧急", wf.handle(OWNER, "紧急"))
b_stored = repo.get_for_owner(OWNER, b["request_id"])
a_stored = repo.get_for_owner(OWNER, a["request_id"])
print("  B 落库:", b_stored["status"], "| urgency =", (b_stored["request"].get("fields") or {}).get("urgency"))
print("  A 联系状态:", (a_stored["request"].get("candidate_contact") or {}).get("status"))

print()
print("=== 场景二（ISSUE-002）：两条需求并存时「选第2个」归属 ===")
# B 先进入 CANDIDATES_READY 并被选中，使其成为"历史 SELECTED 需求"
wf.handle(OWNER, "开始筛选")
wf.handle(OWNER, "第一个")
# 此时没有活跃需求，再新开 C
c = wf.handle(OWNER, "我要招一个检验技术员")
wf.handle(OWNER, "实验室部，10-13K，尽快，没有额外硬要求")
wf.handle(OWNER, "开始筛选")
show("C 选人", wf.handle(OWNER, "选第2个"))
b_stored = repo.get_for_owner(OWNER, b["request_id"])
c_stored = repo.get_for_owner(OWNER, c["request_id"])
print("  历史 SELECTED 需求 B.selected =", repr(b_stored["selected_candidate_id"]), "（必须保持 C1）")
print("  当前批次需求   C.selected =", repr(c_stored["selected_candidate_id"]), "（必须是 C2）")
