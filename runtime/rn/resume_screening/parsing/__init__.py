"""简历解析层：简历入库、事实抽取与仓库门面。

- ``resume_db``        后端接口 ``ResumeBackend``、本地 JSON store、HTTP 后端、
                       文本简历解析 ``parse_text_resume``、哈希工具。
- ``resume_jsonl``     JSONL 只读后端（recruiter-na-parsed-resumes 导出文件）。
- ``resume_mysql``     MySQL 生产后端（candidate_resume / candidate_info /
                       mail_receive_log 三表映射）。
- ``resume_repository`` 后端门面 ``ResumeRepository``：生产环境 fail-closed。

本层只负责「把简历变成结构化事实」，不做匹配，也不产生任何候选人分数。
"""
