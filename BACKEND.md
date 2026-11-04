# 后端架构与题型扩展

## 业务链路

```text
管理员在服务器创建账号
        ↓
教师登录 → 创建阅卷任务（题型、题目、配置快照）
        ↓
逐份提交图片 + 教师指定的学生身份 / 匿名编号 + 提交标识
        ↓
同一数据库事务：保存答卷记录 + 创建阅卷作业
        ↓
独立 worker 原子领取 → 租约续期 → 按题型与版本选择 Grader
        ↓
一次单份阅卷 → 校验结果 → 有效租约提交结果与 AI 版本
        ↓
教师复核 → 教师结果版本 → 确认成绩 → 导出
```

这里使用一个关系数据库按业务拆表，便于保证“答卷保存”和“作业入队”的原子性。拆成多个数据库文件反而需要额外处理跨库一致性。

## 代码职责

| 位置 | 职责 |
|---|---|
| `web/server.py` | HTTP 应用组装、安全响应头、健康检查 |
| `web/routes/` | 接收请求、登录认证、调用业务服务、返回响应 |
| `backend/auth.py` / `admin.py` | 管理员分配账号、密码、会话、停用和登录限制 |
| `backend/resources.py` | 班级、个人模板和默认模板 |
| `backend/tasks.py` | 任务建立、所属账户、配置与题型版本 |
| `backend/submissions.py` | 单份录入、去重、复核、身份更正和重新评分 |
| `backend/files.py` | 原文件与模型用图存储、路径边界检查 |
| `backend/queue.py` | 作业入队、领取、租约、重试、失效结果隔离 |
| `backend/worker.py` | 独立工作进程与可配置并发 |
| `backend/grading/base.py` | 单份阅卷接口与注册表 |
| `backend/grading/essay.py` | 作文阅卷实现、配置和教师结果校验 |
| `backend/reports.py` | 成绩汇总和报告生成 |
| `backend/database.py` | 连接、业务表、增量迁移与兼容旧数据 |

业务服务不依赖 FastAPI。API 与 worker 共用 `build_services()`，从同一配置组装服务。

## 数据表

| 表 | 内容 |
|---|---|
| users / account_audit | 服务器创建的账号、启停状态、分配对象备注、管理操作记录 |
| sessions / login_limits | 会话摘要、有效期与登录失败限制 |
| classes / templates / preferences | 班级名单、个人评分标准、默认模板 |
| tasks | 阅卷任务、题型、实现版本、评分配置快照和学生名单 |
| submissions | 单份答卷、教师绑定身份、文件位置、AI/教师结果投影、复核版本号、幂等提交标识 |
| versions | AI 结果、教师草稿/确认、身份更正的追加记录 |
| grading_jobs | 持久化作业、冻结输入、租约、重试时间、执行次数、结果版本引用 |
| job_attempts | 每次执行的进程、开始/结束、结果、异常或租约过期 |
| workers | 工作进程心跳 |
| pairs | 限时且仅允许上传的手机入口 |
| schema_migrations | 迁移记录；字段升级以可重复执行的增量操作完成 |

学生名单和评分配置保留 JSON 以便任务快照独立。学生内部标识用于绑定，姓名不进入模型输入。原图在文件卷中，数据库记录文件键；结果和历史在数据库中。

## 单份阅卷接口

新题型实现 `Grader` 协议：

```python
class Grader(Protocol):
    task_type: str
    version: str
    title: str

    def validate_configuration(self, configuration: dict) -> dict: ...
    async def grade(self, request: GradingInput) -> GradingResult: ...
    def validate_review(self, configuration: dict, result: GradingResult) -> GradingResult: ...
```

`GradingInput` 仅包含题目说明、图片路径和题型配置，不含账户、姓名、名单或班级。`GradingResult` 提供通用分数、维度分、说明、转写与教师扣分字段；每个实现负责自己题型的给分规则及校验。

接入其他题型的步骤：

1. 新增对应 Grader，声明题型名称和版本，实现配置校验、单次评分和复核校验。
2. 在 `default_registry()` 注册该实现。网页进程与 worker 必须部署同一组实现。
3. 前端增加该题型的配置表单和入口，调用现有创建任务 API。
4. 原有提交、权限、队列、复核、版本和导出链路继续共用。若该题型需要不同结果结构或专用报告，再显式扩展结果/报告契约。

创建接口兼容原作文前端：

```json
{
  "title": "作文测验",
  "assignment": "写作要求",
  "task_type": "essay",
  "mode": "anonymous",
  "configuration": {
    "rubric": {
      "max_score": 50,
      "grade_level": "高中",
      "criteria": [{"name": "内容", "weight": 1, "description": "结合题意、选材与表达综合评价"}]
    }
  }
}
```

旧前端仍可在顶层提交 `rubric`，服务端将其转换为作文配置。`GET /api/task-types` 返回已注册题型。当前生产注册表只有作文；测试中的翻译实现仅用于证明扩展接口，不代表翻译功能已经上线。

任务固定保存实现版本。更新旧实现时，应保留仍有待处理任务所需的版本；未部署的版本会明确失败，不悄悄换用其他评分规则。

## 队列一致性

- 每个答卷最多一个活动作业，由数据库唯一索引保证。
- 请求标识在同一任务下唯一；同一标识与相同图片/身份重复提交返回原记录，不重新入队。标识复用但内容不同返回冲突。
- 领取在短事务中进行；模型调用在事务外执行，避免长时间持有数据库写锁。
- 领取产生新随机租约令牌并增加尝试次数。模型调用期间由独立协程续期。
- 租约过期后另一个 worker 可重新领取；完成/失败提交同时校验令牌和有效期。旧 worker 的迟到结果会被丢弃。
- 临时调用失败按退避时间重新排队；解析、配置等非临时错误直接失败。超过最大次数不会无限重复执行。
- 有效结果、AI 版本、作业成功状态和结果版本引用在同一事务保存。
- 重新评分保留教师草稿与确认成绩，只创建新的作业与 AI 版本。
- 教师保存携带 `revision`，避免多个页面或重评更新相互覆盖。

`GET /api/submissions/{id}/jobs` 为答卷所属老师提供作业状态和尝试次数。快照及执行审计保存在服务端；不会把模型密钥放进队列或返回给浏览器。

## 验证范围

测试覆盖真实多进程竞争领取、租约恢复与旧结果隔离、长调用续期、退避重试、重复请求去重、入队失败回滚、独立 API/worker 实例、账户启停与密码重置、题型注册，以及原有上传/模板/复核/导出流程。浏览器仍验证分配账号登录、录入、评分、图片查看和手机入口。真实模型调用及 Docker 镜像运行需在目标服务器配置后验证。
