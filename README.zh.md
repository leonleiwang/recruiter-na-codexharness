# Recruiter-Na

[English](README.md) | 中文

Recruiter-Na 是一个基于 Codex Harness、面向用人部门的开源钉钉招聘助手。

它支持用人部门发起或继续**本人**的招聘需求，并查看**本人**需求的进展。对于当前已支持的检验技术员 Policy，筛选链路为：`简历事实 → LLM 语义证据 → 证据 Grounding → 确定性 Candidate Decision`。

文档：[使用说明](USAGE.md) 与 [Candidate Decision 合同](docs/RECRUITMENT_DECISION_V5.md)。

## 开发者预览

Recruiter-Na 仍处于开发者预览阶段，并在快速迭代。可能会有破坏兼容性的变更。

此公开源码发行版不包含任何生产凭据、租户配置、候选人简历、内部岗位/JD 库或招聘需求数据库。运行项目前请阅读示例配置。

## 运行

### 从源码运行

安装 Python 3.10+，然后执行本公开发行版包含的源码级验证：

```bash
git clone https://github.com/leonleiwang/recruiter-na-codexharness.git
cd recruiter-na-codexharness
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/secret_scan.py
PYTHONPATH=runtime .venv/bin/python -m pytest tests/test_secrets_and_privacy.py tests/test_codex_packaging.py -q
```

完整的本地/生产验证脚本 `scripts/verify.sh` 还需要合法取得的 IM adapter 与已获授权的招聘数据。请使用 `config/codex/*.example` 之一在隔离的 `CODEX_HOME` 中配置模型提供方，只向未跟踪的 `.env` 写入所需值。服务端安装请从 `deploy/recruiter-na.env.example` 与 `deploy/install-cloud.sh` 开始。

在启用真实机器人前，运营方必须自行提供获授权的简历库、已核验的岗位/JD 材料及合法取得的 IM adapter。被排除的私有运行时 adapter 不属于本 Apache-2.0 发行版。

## 社区与支持

- 通过 [GitHub Issues](https://github.com/leonleiwang/recruiter-na-codexharness/issues) 提交可复现的 Bug 或功能请求。
- 通过 [GitHub Discussions](https://github.com/leonleiwang/recruiter-na-codexharness/discussions) 提问或讨论设计。
- 请勿在公开报告中包含简历、访问令牌、租户标识或其他个人数据。

## 贡献

重大变更请先创建 Issue。贡献必须保持 owner-scoped 的招聘需求访问边界，禁止将敏感属性用于候选决策，也绝不能把密钥或真实候选人数据加入仓库。

## 开发

在提供上述已获授权的私有依赖后，运行完整验证：

```bash
bash scripts/verify.sh
```

活跃服务使用 `screening_v5.py`；legacy scorer 仅保留给回归和回滚分析，公开的用人部门链路不会加载它们。Candidate Decision 仅在存在已获批准 Policy 的岗位上执行；未支持岗位不得回退到数值化候选人评分。

## 引用

```text
@software{recruiter_na_2026,
  title = {Recruiter-Na: Hiring Manager Recruitment Assistant},
  author = {Recruiter-Na Contributors},
  year = {2026},
  url = {https://github.com/leonleiwang/recruiter-na-codexharness}
}
```

## 许可证

[Apache-2.0](LICENSE)

该许可证只适用于本仓库实际分发的文件；它不授予任何被排除的私有数据、凭据、租户配置或另行取得的运行时依赖之权利。
