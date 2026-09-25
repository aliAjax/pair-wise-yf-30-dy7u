# 药物警戒案例处理系统

使用 Python 标准库实现的独立原型，覆盖多渠道案例接入、去重、随访更正、严重性医学裁定、分国家报告、**报告更正与重提（版本链、旧稿/回执存档、期限重算）**、逾期升级、跨区域权限和案例合并审计。

## 运行

要求 Python 3.11+。

```bash
python3 app.py --db pharmacovigilance.db
```

默认监听 `127.0.0.1:8201`。首页为 `http://127.0.0.1:8201/`，健康检查为 `/health`。

所有接口使用请求头 `X-User-Id`、`X-Role` 和区域角色必需的 `X-Region`。角色为 `reporter`、`regional_lead`、`medical_reviewer`、`global_admin`。

## 报告更正与重提

监管退回或补录死亡时不再另建重复报告，同一 `(案例, 国家)` 报告保留版本链：

- `POST /api/cases/{id}/reports`：生成报告即创建 **v1 草稿**（`content` 为稿件 JSON）。
- `POST /api/reports/{id}/submit`：提交未提交稿；可带 `receipt`（监管回执 JSON）。
- `POST /api/reports/{id}/corrections`：对已提交报告发起更正，必填 `reason`（更正原因）与 `content`。每次更正都保存**更正原因、旧稿、旧回执、原提交人和原提交时间**。
- 同一国家已有未提交更正时：
  - 再次发起更正必须带 `replace_reason`，系统把原未提交稿标记为 `void` 后另开新版本；
  - 提交重提必须带 `reason`。
- `GET /api/cases/{id}` 的每个报告带 `versions`（按版本号升序，旧稿在前、当前有效版本在最后）、`effective_version`（当前有效＝最大的已提交版本）、`pending_version`（未提交稿）和 `version_state`（`pending`/`submitted`/`correction_pending`）。
- 医学审核导致**严重性或死亡转归变化**时，以 `received_at`（变更时间）重算该国期限（死亡 7 天、严重 15 天、非严重 90 天）：只改未提交稿；已提交版本是不可变存档，报告头 `due_at`/`due_changed_at` 始终保存最新监管期限，新更正稿继承它。
- 跨区域隔离：只有案例所在区域的负责人（及全局管理员）能发起更正、作废重开或重提；其他区域不能操作，页面只显示本区域案例及其新版本与期限。

## 代码分层（分开维护）

- `pv_rules.py`：版本与期限**规则**（版本号、状态、当前有效版本、期限重算），纯函数无数据库依赖。
- `pv_archive.py`：版本**存档**表结构、旧库迁移/回填、版本读写（旧稿、回执、提交人快照）。
- `app.py`：服务编排、权限与 HTTP 接口。
- `static/index.html`：页面操作台（发起更正、作废重开、带原因重提、按顺序查看版本与期限），只调 API。

## 主要接口

- `POST /api/cases`：录入案例，`dedupe_key` 相同则返回已存在案例。
- `GET /api/cases`、`GET /api/cases/{id}`：按权限查询。
- `POST /api/cases/{id}/followups`：用 `expected_revision` 防止覆盖随访。
- `POST /api/cases/{id}/medical-review`：医学审核员更新严重性、死亡和关联性；转归变化时重算各国期限。
- `POST /api/cases/{id}/reports`、`POST /api/reports/{id}/submit`、`POST /api/reports/{id}/corrections`：生成、提交、更正重提分国家报告。
- `POST /api/cases/{id}/merge`：全局管理员合并重复案例。
- `POST /api/escalate-overdue`、`GET /api/overdue`：逾期检查与升级（含未提交更正稿）。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 主要局限

该实现使用请求头模拟身份，不含生产级登录、签名和密钥管理；SQLite 与标准库 HTTP 服务适合单机原型。分国家规则采用内置严重 15 天、死亡 7 天、非严重 90 天规则，接入真实监管网关前需按当地法规扩展。
