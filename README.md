# 文印室双面打印队列

按页录入文档，操作两台模拟打印机逐面出纸；卡纸后原机确认停稳，再把续单派给另一台打印机，从首张未完整出纸处重印两面。奇数页文档末张背面留白。所有出纸记录写入 SQLite，网页可核对装订页序，重启容器后进度不丢。

讲义排队后改版稿才送到也不用重排：未开打的作业可换用另一份录好的稿，作业号与排队位置不变，页数与双面张数按新稿重算，换下的稿仍可查。

## 启动

```bash
docker compose up --build
```

浏览器打开 <http://localhost:8000/>。

停止：`docker compose down`；数据在命名卷 `printroom-data` 中，`docker compose down -v` 会连同数据一起删除。

## 操作流程

1. **录入文档**：填标题新建 → 按顺序逐页添加内容（可留空表示空白页）→ “提交入队”。
   入队后页面锁定；同一文档重复提交只产生一个作业。
2. **开始打印**：在空闲打印机上选择排队作业 → 开始。
3. **逐面出纸**：每张纸先“出正面”，再“出背面”。双面都出完（或奇数页末张的正面出完）才算**完整出纸**，自动进入下一张。
4. **卡纸**：点“报告卡纸”。当前半张立即作废；原机停在“卡纸待停稳”，此时**不能**派续单，也不能继续出纸。
5. **确认停稳**：原机点“确认原机停稳”。停稳前已完整出纸的纸张全部保留，只有作废的半张需要重印。
6. **换机**：对已停稳的作业点“换机派续单”，系统派给另一台打印机，从首张未完整出纸的那张开始**重印两面**。重复点击不会重复派单。
7. **换稿（仅未开打）**：改版稿先在左侧录入（不必提交入队），再到作业卡片“换稿”选它 → “换用选中的稿”。作业号与排队位置不变，页数/双面张数按新稿重算；换下的稿留在“稿件版本”里，可展开查页面、可再换回。已开打（含卡纸、停稳、已完成）的作业拒绝换稿，换稿失败仍按原稿排队。
8. **重新确认**：若另一页面已换稿，本页面（旧数据）点“开始打印”会被拒绝并提示“稿件已更换……请重新确认”，**本次不启动机台**；页面刷新后按新稿再点即可。开打始终按打印员在页面上选中的那版算页数与张数。
9. **核对**：页面底部“出纸记录”按次列出每张纸的正/背面页码与状态；作业卡片里的“装订核对”只列完整出纸的纸张并按张号排序，按此顺序叠放即为原页序。

页序规则：第 s 张纸正面印第 `2s-1` 页，背面印第 `2s` 页；`2s` 超过总页数时背面留白。

## 本地运行（不装 Docker）

只依赖 Python 3.11+ 标准库：

```bash
python3 -m printroom          # 默认 0.0.0.0:8000，数据库 data/printroom.db
```

环境变量：`PRINTROOM_HOST`、`PRINTROOM_PORT`、`PRINTROOM_DB`。

## 测试与自检

```bash
python3 -m unittest tests.test_printroom -v   # 37 个端到端用例
python3 scripts/check_ui.py                   # 静态页面与脚本引用一致性
python3 scripts/demo.py                       # 跑通“半张卡纸”“停稳前出纸”“两个网页交错换稿与开打”三条验收路径
```

测试使用临时 SQLite 数据库和随机端口，不会污染 `data/`。

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/state` | 文档、作业、打印机、出纸记录全量快照 |
| GET | `/api/jobs/{id}` | 单个作业（含 `collation` 装订视图） |
| GET | `/api/documents/{id}` | 文档及页面 |
| POST | `/api/documents` | 新建文档 `{title}` |
| POST | `/api/documents/{id}/pages` | 追加一页 `{content}` |
| POST | `/api/documents/{id}/pages/last` | 删除末页 |
| POST | `/api/documents/{id}/finalize` | 提交入队（幂等） |
| POST | `/api/jobs/{id}/swap-document` | 未开打作业换用另一份稿 `{document_id}`（换成当前稿为幂等空操作） |
| POST | `/api/jobs/{id}/start` | 开始打印 `{printer_id, expected_document_id?}`；版本不符返回 `document_changed`，不启动机台 |
| POST | `/api/jobs/{id}/print` | 出一面 `{side: front\|back, sheet_no?}` |
| POST | `/api/jobs/{id}/jam` | 报告卡纸（幂等） |
| POST | `/api/jobs/{id}/stop` | 原机确认停稳（幂等） |
| POST | `/api/jobs/{id}/switch` | 换机派续单 `{printer_id?}`（每个停稳段最多一份） |

错误返回 `{error, code}`，常见 `code`：`printer_busy`、`not_stopped`、`sheet_mismatch`、`empty_document`、`already_started`、`document_changed`、`document_in_use`。

作业 JSON 里的 `document_version` 是当前稿版号（原始稿为 1），`document_history` 是稿件版本时间线（每版的文档 id、标题、页数、起止时间、是否当前用稿），换稿记录重启后仍在。

## 数据一致性

- 写操作走 `BEGIN IMMEDIATE` 事务，`print_segments` 上有“每台打印机最多一个活动/卡纸段”的部分唯一索引。
- 换稿在单个事务内完成“校验 → 换当前稿 → 记换稿记录”：已开打、目标稿为空、目标稿被其它作业占用等情况整体回滚，作业仍按原稿排队；换稿与开打并发时恰好一个成功。
- `job_document_swaps` 持久化每次换稿（从哪版换到哪版、各自页数、时间），当前稿即 `jobs.document_id`，重启后当前稿与换稿记录都可查。
- `sheet_attempts(job_id, sheet_no) WHERE status='complete'` 唯一，保证同一张纸不会被计为两次完整出纸。
- `print_segments.continued_from` 唯一，保证一个停稳段最多派生一份续单；并发重复请求返回同一份。
- 进度全部落在 SQLite（`data/` 命名卷），服务重启后继续从数据库状态续印。
