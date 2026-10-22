"""Stage 0 evaluation harness.

模块职责：
- `executor`    只读执行 SQL（agent 重放 + golden）
- `normalize`   DataFrame 归一化
- `compare`     结果集等价比较
- `evaluators`  ResultSet / Clarification / FirstTry
- `labeling`    失败分类
- `preflight`   数据锚点、连接、只读、golden 自检
- `report`      JSON / HTML / CSV
- `cli`         check / seed / run / report
"""

__all__: list[str] = []
