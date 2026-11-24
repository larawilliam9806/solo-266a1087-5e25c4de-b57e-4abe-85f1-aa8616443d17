# 展会联票分组统计服务

接收闸机的**整日清单**（每行 = 匿名券号 + 馆号），按**展期 + 日期**存入 SQLite，
并把每张券归入它在本展期去过的**全部馆组成的那一组**。展会可直接查到：

* 只去甲馆的联用了多少张；
* 甲、乙两馆都去的联用了多少张；
* 以及其他任意馆组合。

## 规则与口径

1. **一张券在同一展期只计一次**，归入它去过的全部馆的并集（去重、规范化排序）。
2. **换交 = 某日清单全量替换**，允许交空清单（等同清空该日记录）。
3. 换交只重算**当天新旧清单涉及的券**，与其他日期、其他券无关。
4. 删去某日记录后，若该券在别日还刷过同一馆，该馆仍计入组合（按全部日期重算并集）。
5. 展期汇总为**物化结果**，查询直接读汇总表，返回当前有券的各组及券数。
6. 换交在单个 `BEGIN IMMEDIATE` 事务内完成：
   * 清单与券数**一同生效**；失败整体回滚，**不改任何数据**。
   * 并发换交（即使涉及同一张券、不同日期）由写锁串行化，**全部生效不丢更新**。
7. SQLite 开启 **WAL**：换交进行期间，查询仍可读上一版已生效的汇总，不被阻塞。

## 表结构

| 表 | 作用 |
| --- | --- |
| `records` | 原始过闸记录（展期、日期、券号、馆号），四列联合主键去重 |
| `ticket_halls` | 逐券物化：每张券当前的馆组合 |
| `group_summary` | 展期汇总物化：每个馆组合的券数（查询直接读这里） |

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `PUT` | `/periods/{展期}/dates/{YYYY-MM-DD}/records` | 换交某日整日清单（可空） |
| `GET` | `/periods/{展期}/summary` | 展期汇总：有券的各组及券数 |
| `GET` | `/periods/{展期}/dates/{日期}/records` | 读某日已生效清单 |
| `GET` | `/periods/{展期}/tickets/{券号}` | 查单张券的馆组合 |
| `GET` | `/health` | 健康检查 |

### 示例

```bash
# 11 月 1 日闸机清单
curl -X PUT http://localhost:8000/periods/autumn2026/dates/2026-11-01/records \
  -H 'Content-Type: application/json' \
  -d '{"entries":[{"ticket_no":"T1","hall":"甲"},{"ticket_no":"T3","hall":"甲"}]}'

# 11 月 2 日：T3 又去了乙馆
curl -X PUT http://localhost:8000/periods/autumn2026/dates/2026-11-02/records \
  -H 'Content-Type: application/json' \
  -d '{"entries":[{"ticket_no":"T3","hall":"乙"}]}'

# 展期汇总（直接读物化汇总）
curl http://localhost:8000/periods/autumn2026/summary
# {"exhibition_id":"autumn2026","groups":[
#   {"halls":["甲"],"ticket_count":1},
#   {"halls":["乙","甲"],"ticket_count":1}]}

# 交空清单：清空 11-01（T3 在 11-02 刷过乙馆，乙馆仍计入）
curl -X PUT http://localhost:8000/periods/autumn2026/dates/2026-11-01/records \
  -H 'Content-Type: application/json' -d '{"entries":[]}'
```

交互式文档：启动后访问 `http://localhost:8000/docs`。

## Docker Compose 启动

```bash
docker compose up -d --build
docker compose logs -f
```

* 服务监听 `8000`，数据库落在命名卷 `ticket-data`（容器内 `/data/exhibition.db`），
  容器重建数据保留。
* 单 uvicorn worker：SQLite 同一时刻只允许一个写事务，换交在应用层用
  `BEGIN IMMEDIATE` 串行化，即可保证并发不同日期的换交全部生效。

## 本地开发

```bash
pip install -r requirements.txt
DB_PATH=./data/exhibition.db uvicorn app.main:app --reload

# 测试（功能 + 真实服务多线程并发）
pytest
```

## 设计说明

* 每次请求使用独立 SQLite 连接（连接不跨线程共享），设置 30s `busy_timeout`：
  并发写时后到者等待先到者提交，而不是立即报锁。
* Pydantic 在进入事务前完成请求体校验（空券号/馆号、日期格式均返回 422），
  非法请求不触碰数据库。
* 馆组合以去重排序后的 JSON 数组字符串作为规范化键，保证同一组只有一行汇总。
