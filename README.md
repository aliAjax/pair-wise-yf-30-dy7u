# 药物警戒案例处理系统

使用 Python 标准库实现的独立原型，覆盖多渠道案例接入、去重、随访更正、严重性医学裁定、分国家报告、报告更正与重提、逾期升级、跨区域权限和案例合并审计。

## 模块划分

版本规则、存档与页面操作分开维护：

- `version_rules.py`：报告版本规则——期限计算（死亡 7 天、严重 15 天、非严重 90 天）、更正/提交校验、回执编号、旧稿快照结构，纯函数无存储依赖。
- `report_archive.py`：报告版本存档——`report_versions` 表读写，保留每次更正的原因、旧稿快照、回执与提交人。
- `app.py`：HTTP 接口与服务编排（页面操作入口）。
- `static/index.html`：案例台页面，展示版本序列、当前有效版本与各国期限，并提供更正/提交操作。

## 运行

要求 Python 3.11+。

```bash
python3 app.py --db pharmacovigilance.db
```

默认监听 `127.0.0.1:8201`。首页为 `http://127.0.0.1:8201/`，健康检查为 `/health`。

所有接口使用请求头 `X-User-Id`、`X-Role` 和区域角色必需的 `X-Region`。角色为 `reporter`、`regional_lead`、`medical_reviewer`、`global_admin`。

## 主要接口

- `POST /api/cases`：录入案例，`dedupe_key` 相同则返回已存在案例。
- `GET /api/cases`、`GET /api/cases/{id}`：按权限查询；详情中每个报告按版本号顺序列出全部版本并标记当前有效版本。
- `POST /api/cases/{id}/followups`：用 `expected_revision` 防止覆盖随访。
- `POST /api/cases/{id}/medical-review`：医学审核员更新严重性、死亡和关联性；严重性或死亡转归变化后，按变更时间重算各国报告与在途版本的期限。
- `POST /api/cases/{id}/reports`、`POST /api/reports/{id}/submit`：生成并提交分国家报告；提交可附 `receipt`（缺省由系统生成回执编号）。
- `POST /api/reports/{id}/corrections`：对已提交报告发起更正，`reason` 必填；该国已有未提交更正时须附 `supersede_reason` 说明再次发起的原因，旧在途版本作废留痕。
- `POST /api/reports/{id}/submit`：提交在途版本；提交更正版本时 `reason` 必填。新版本生效后历史版本自动归档为旧稿。
- `GET /api/reports/{id}/versions`：按顺序查看版本历史（原因、旧稿、回执、提交人）。
- `POST /api/cases/{id}/merge`：全局管理员合并重复案例。
- `POST /api/escalate-overdue`、`GET /api/overdue`：逾期检查与升级（覆盖待提交与更正在途的报告）。

更正与提交均校验区域权限，其他区域不能操作。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 主要局限

该实现使用请求头模拟身份，不含生产级登录、签名和密钥管理；SQLite 与标准库 HTTP 服务适合单机原型。分国家规则采用内置严重 15 天、死亡 7 天、非严重 90 天规则，接入真实监管网关前需按当地法规扩展；回执编号为本地生成，未对接监管回执报文。
