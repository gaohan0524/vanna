# P1-23 CORS 默认收紧

- **对应问题**：默认 `allow_origins=["*"]` 且 `allow_credentials=True`，任意源可跨域调用计费型 LLM 接口
- **优先级**：P1（安全）
- **状态**：已完成
- **提交**：`3c4c958 feat: 收紧 SQL 默认值并修复公开 API（P1-21/22/23）`

---

## 1. 问题背景

`VannaFastAPIServer` 的 CORS 默认配置：

```python
cors_params.setdefault("allow_origins", ["*"])
cors_params.setdefault("allow_credentials", True)
```

- `allow_origins=["*"]` 开放所有来源；
- 与 `allow_credentials=True` 同时出现时，浏览器按 CORS 规范实际会拒绝该组合（行为不确定），但也意味着默认部署即允许任意站点发起请求；
- 示例默认部署直接暴露 `/chat_sse`，任意网页可触发计费型 LLM 调用。

---

## 2. 方案设计

默认「不开放跨域」：

```python
cors_params.setdefault("allow_origins", [])
cors_params.setdefault("allow_credentials", False)
```

- 需要跨域时显式配置 `cors.allow_origins`（JSON 配置或代码）；
- `allow_methods` / `allow_headers` 保持 `["*"]`（同源请求下影响小）。

---

## 3. 代码变动清单

| 文件 | 改动 |
|---|---|
| `servers/fastapi/app.py` | CORS 默认值改为 `allow_origins=[]`、`allow_credentials=False`，更新注释 |

### 关键代码

```python
# 安全默认：默认不开放任何跨域来源，也不允许携带凭据
cors_params.setdefault("allow_origins", [])
cors_params.setdefault("allow_credentials", False)
```

---

## 4. 使用方式

```python
# 需要跨域时显式配置
VannaFastAPIServer(agent, {
    "cors": {
        "enabled": True,
        "allow_origins": ["https://app.example.com"],
        "allow_credentials": True,
    },
})
```

---

## 5. 验证

- `tests/test_public_api_defaults.py::test_public_api_names_and_version` 中已验证 server 类可正常构造；
- 核心回归 **174 passed, 3 skipped**。

---

## 6. 兼容性与注意事项

1. **行为变化**：默认不再允许任何跨域来源；前端若与后端不同源，需显式配置 `cors.allow_origins`。
2. **鉴权**：本项只收紧 CORS；示例 `UserResolver` 仍基于可伪造的 cookie，生产需替换为真实认证（JWT/Session）+ 反 CSRF。

---

## 7. 后续可扩展点

- 提供 `require_auth` 中间件/钩子示例，从传输层强制鉴权；
- 增加生产配置模板（HTTPS-only、严格 origin、限流）。

---

## 附：文件

- 修改：`src/vanna/servers/fastapi/app.py`
