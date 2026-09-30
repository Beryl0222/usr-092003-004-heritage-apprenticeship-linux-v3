# 老字号传承履历

服务用于连接老字号门店与职业学校的传承履历，把**技艺版本、分步示范、练习批次、
原料责任、师徒关系与现场考核**串成一条可核验、可追溯、按角色可见的履历链。

## 运行

```bash
python3 service.py --check                         # 核对配置
python3 -m unittest discover -s tests -v           # 运行规则与接口测试
python3 service.py --port 8000                     # 启动服务
curl http://127.0.0.1:8000/health
```

## 核心规则

| 议题 | 规则 |
| --- | --- |
| 评审签署 | 评审人须在考核当日持**有效资质**，且资质覆盖考核所采用的标准版本；带过该学徒的师傅、证据门店与评审挂职门店相同的，一律**利益冲突回避** |
| 考核结论 | 结论只追加不覆盖。补考产生新结论与新资格版本，旧资格标记 `superseded_by_attempt`，旧评语、旧作品永久保留 |
| 学时去重 | 学时按（学徒、工序、日期）唯一计算，跨店轮转同日同工序重复登记直接拒绝；批次号重复提交幂等 |
| 原料责任 | 分步示范登记原料清单并标注秘方；`master`/`brand_admin` 可见完整信息，其他角色只看到“秘方材料”占位 |
| 上岗核对 | `GET /apprentices/{id}/readiness?craft_id=…` 按当前生效标准逐工序给出 `independent` / `supervised` / `not_ready` / `not_started` |
| 资格追溯 | `GET /qualifications/{id}/trace` 回溯该工序全部考核（含补考）的结论、评语、作品、评审人与当时标准快照 |
| 学校汇总 | `GET /school/employment-summary` 仅输出聚合指标；专业队列 < 3 人整体隐去，单店 < 3 人该店计数置空，不含姓名、配方 |

## 数据链

```
门店/人员 → 技艺(craft) → 版本(version, 分步标准) → 示范(demonstration, 含原料秘方标记)
        → 师徒关系(mentorship, 有起止日期)
        → 练习批次(practice, 按日学时 + 作品)
        → 现场考核(attempt, 引用练习批次/作品/标准版本)
        → 阶段能力资格(qualification, pass=独立 / conditional_pass=需监督)
        → 毕业与就业 → 学校聚合报表
```

## HTTP 接口

写接口均为 `POST` JSON；规则不满足返回 `400`，引用对象不存在返回 `404`。

- `POST /people`、`POST /stores`、`POST /crafts` — 档案登记
- `POST /craft-versions` — 发布技艺标准版本（`steps` 为分步标准与最低学时）
- `POST /credentials` — 登记评审资质（有效期 + 覆盖版本清单，空清单表示全版本）
- `POST /mentorships` — 建立/结束师徒关系（`start_date`/`end_date`），支撑师傅停带、门店调岗后的接续
- `POST /demonstrations` — 分步示范与原料清单（秘方标记 `secret: true`）
- `GET  /demonstrations/{id}` — 按请求头 `X-Viewer-Role` 决定是否脱敏
- `POST /practices` — 练习批次（`sessions` 带日期学时与带教师傅，`works` 为作品）
- `POST /assessments` — 现场考核签署，结果为 `pass` / `conditional_pass` / `fail`
- `GET  /apprentices/{id}/hours?craft_id=…` — 去重后的学时汇总（分工序/分门店）
- `GET  /apprentices/{id}/readiness?craft_id=…` — 毕业上岗逐工序能力核对
- `GET  /qualifications/{id}/trace` — 资格 → 历次考核/作品/评语/标准追溯
- `POST /graduations`、`POST /employments` — 毕业与就业登记
- `GET  /school/employment-summary?graduation_year=…` — 脱敏聚合报表

## 布局

- `heritage/store.py` — 领域模型与全部业务规则（纯标准库，线程安全）
- `service.py` — JSON HTTP 层，负责编解码与错误码映射
- `tests/` — 20 项测试：规则单测、HTTP 端到端与原有健康检查契约
