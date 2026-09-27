# 老字号传承履历

服务用于连接老字号门店与职业学校的传承履历，把技艺版本、分步示范、练习批次、
原料责任、师徒关系与现场考核串联起来，由品牌与学校共同维护。

## 核心规则

- 只有资质有效且不存在利益冲突（非师徒、非本店）的评审人才能签署阶段能力；
- 补考只追加新考核记录，历史评价不被覆盖；
- 跨店轮转的练习时段按天合并，重叠部分不重复计算学时；
- 涉及秘方的原料仅品牌方与师傅可见，其余角色一律显示为“保密配料”；
- 门店可核对学徒每道工序是“可独立操作”还是“仍需监督”；
- 每项资格可追溯到所依据的作品批次、现场评语与当时采用的技艺标准；
- 学校汇总就业与培养成效时只得到聚合数字，不含商业配方与个人敏感信息。

## 接口

- `GET /health`：健康检查。
- `POST /persons`、`/reviewers`、`/technique-versions`、`/materials`、`/relations`、`/employments`：登记基础档案。
- `POST /batches`：登记练习批次（含原料与作品）。
- `POST /assessments`：评审人签署阶段能力（校验资质与利益冲突，只增不改）。
- `GET /apprentices/{id}/clearance`：上岗核对，列出可独立操作与仍需监督的工序。
- `GET /apprentices/{id}/hours?date=YYYY-MM-DD`：合并后的有效学时。
- `GET /assessments/{id}/trace`：追溯资格依据的作品、评语与当时标准。
- `GET /materials?role=...`：按角色过滤后的原料视图。
- `GET /summary/school`：学校汇总（仅聚合数据）。

## 运行

`python3 service.py --check` 核对配置；`python3 -m unittest discover -s tests -v`
验证接口契约与领域规则；`python3 service.py --port 8000` 启动服务后访问 `/health`。
